#!/usr/bin/env python3
"""
用 Birdeye API 抓所有需要的 token 历史价格 (1H 粒度)。

策略:
  - 每个 token 查一次: 用 time_from..time_to 覆盖所有需要的 ts
  - 返回 1H 时间序列, 存成 {hour_unix: price}
  - compute_pnl 查价时取最近的小时

Cache 格式:
  prices/birdeye_cache.json
    {
      "solana:<mint>":    {"<hour_unix>": <price>, ...},
      "ethereum:<addr>":  {"<hour_unix>": <price>, ...}
    }

环境:
  .env 需要 BIRDEYE_API_KEY
  免费 / Lite 速率: 100 rpm
"""

import json
import os
import sys
import time
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
CANDIDATES = ROOT / "arbitrage_candidates_1pct.json"
PRICES_DIR = ROOT / "prices"
SYMBOL_MAP = PRICES_DIR / "symbol_map.json"
CACHE_FILE = PRICES_DIR / "birdeye_cache.json"
STATS_FILE = PRICES_DIR / "birdeye_stats.json"

API = "https://public-api.birdeye.so/defi/history_price"
RATE_PER_MIN = 100
MAX_WORKERS = 8
REQ_TIMEOUT = 20

SOL_NATIVE = "solana:So11111111111111111111111111111111111111112"
ETH_NATIVE = "coingecko:ethereum"
# Birdeye 没有 coingecko 原生 ETH, 用 WETH 代替 (价格一致)
ETH_NATIVE_BIRDEYE = "ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def load_env():
    env_path = Path(__file__).parent.parent / ".env"
    for line in env_path.read_text().splitlines():
        if line.startswith("BIRDEYE_API_KEY"):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("BIRDEYE_API_KEY 未找到")


def is_eth_addr(s):
    return isinstance(s, str) and s.startswith("0x") and len(s) == 42

def is_sol_addr(s):
    return isinstance(s, str) and 32 <= len(s) <= 44 and not s.startswith("0x") and s.isalnum()


def resolve_token(chain, tok, symap):
    if not tok:
        return None
    if chain == "ETH":
        if tok == "ETH":
            return ETH_NATIVE_BIRDEYE
        if is_eth_addr(tok):
            return f"ethereum:{tok.lower()}"
        addr = symap["eth"].get(tok)
        return f"ethereum:{addr.lower()}" if addr else None
    elif chain == "SOL":
        if tok in ("SOL", "WSOL"):
            return SOL_NATIVE
        if is_sol_addr(tok):
            return f"solana:{tok}"
        addr = symap["sol"].get(tok)
        return f"solana:{addr}" if addr else None
    return None


def extract_token_ts_ranges(candidates, symap):
    """返回 {token_key: (min_ts, max_ts)}"""
    token_ts = defaultdict(list)
    unresolved = defaultdict(int)

    for c in candidates:
        sol_ts = c.get("sol_ts")
        eth_ts = c.get("eth_ts")
        br = c.get("bridge") or {}

        if br.get("spl_mint") and sol_ts:
            token_ts[f"solana:{br['spl_mint']}"].append(sol_ts)
        if br.get("erc20_contract") and eth_ts:
            token_ts[f"ethereum:{br['erc20_contract'].lower()}"].append(eth_ts)
        if sol_ts:
            token_ts[SOL_NATIVE].append(sol_ts)
        if eth_ts:
            token_ts[ETH_NATIVE_BIRDEYE].append(eth_ts)

        for s in (c.get("entry_swaps_greedy") or []) + (c.get("exit_swaps_greedy") or []):
            ts = s.get("ts")
            chain = s.get("chain")
            if not ts or not chain:
                continue
            for item in (s.get("sold") or []) + (s.get("bought") or []):
                key = resolve_token(chain, item.get("token"), symap)
                if key:
                    token_ts[key].append(ts)
                else:
                    unresolved[f"{chain}:{item.get('token')}"] += 1

    ranges = {k: (min(v), max(v)) for k, v in token_ts.items()}
    return ranges, unresolved


def fetch_one(key, ts_min, ts_max, api_key):
    """抓一个 token 的 1H 价格序列"""
    chain_prefix, addr = key.split(":", 1)
    chain = "solana" if chain_prefix == "solana" else "ethereum"
    # 留 1h buffer
    tf = ts_min - 3600
    tt = ts_max + 3600
    # Birdeye 1H 限 1000 点, 1000h ≈ 42 天, 安全范围
    if tt - tf > 1000 * 3600:
        tt = tf + 1000 * 3600
    params = {
        "address": addr,
        "address_type": "token",
        "type": "1H",
        "time_from": tf,
        "time_to": tt,
    }
    headers = {"X-API-KEY": api_key, "x-chain": chain}
    for attempt in range(3):
        try:
            r = requests.get(API, params=params, headers=headers, timeout=REQ_TIMEOUT)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            data = r.json()
            items = (data.get("data") or {}).get("items") or []
            if not items:
                return {}, "empty"
            series = {str(it["unixTime"]): it["value"] for it in items if "value" in it}
            return series, None
        except Exception as e:
            if attempt == 2:
                return None, str(e)[:80]
            time.sleep(1)
    return None, "retries_exhausted"


def main():
    api_key = load_env()
    print(f"API key 已加载 ({api_key[:6]}...)")

    print(f"\n加载 {CANDIDATES.name}...")
    cdata = json.load(open(CANDIDATES, encoding="utf-8"))
    cands = cdata.get("candidates", [])
    symap = json.load(open(SYMBOL_MAP, encoding="utf-8"))

    ranges, unresolved = extract_token_ts_ranges(cands, symap)
    print(f"  唯一 token: {len(ranges)}, 未解析: {sum(unresolved.values())}")

    # Load cache (resume)
    cache = {}
    if CACHE_FILE.exists():
        cache = json.load(open(CACHE_FILE, encoding="utf-8"))
        print(f"  已有缓存: {len(cache)} token, {sum(len(v) for v in cache.values())} 条价格")

    # 过滤已有的 token (完全跳过, 不重抓)
    todo = {k: v for k, v in ranges.items() if k not in cache or not cache[k]}
    print(f"\n待抓: {len(todo)} token")

    if not todo:
        print("无需 fetch")
        return

    # 速率控制: 100 rpm → sleep 0.6s/req 单线程, 或多线程 + 令牌桶
    rate_sleep = 60.0 / RATE_PER_MIN  # 0.6s

    t0 = time.time()
    done = 0
    ok = 0
    empty = 0
    err = 0
    errors_by_type = defaultdict(int)

    def _do(item):
        key, (tmin, tmax) = item
        return key, fetch_one(key, tmin, tmax, api_key)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_do, kv): kv[0] for kv in todo.items()}
        for fut in as_completed(futures):
            key, (series, error) = fut.result()
            done += 1
            if error is None:
                cache[key] = series
                ok += 1
            elif error == "empty":
                cache[key] = {}
                empty += 1
            else:
                err += 1
                errors_by_type[error] += 1
            if done % 50 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed else 0
                print(f"  {done}/{len(todo)} ok={ok}, empty={empty}, err={err} ({rate:.1f}/s)")
                json.dump(cache, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)

    json.dump(cache, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    elapsed = time.time() - t0

    stats = {
        "total_tokens": len(ranges),
        "fetched_now": done,
        "ok": ok,
        "empty": empty,
        "error": err,
        "errors_by_type": dict(errors_by_type),
        "elapsed_sec": round(elapsed, 1),
        "cache_total_tokens": len(cache),
        "cache_total_prices": sum(len(v) for v in cache.values()),
    }
    json.dump(stats, open(STATS_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    print(f"\n完成: ok={ok}, empty={empty}, err={err}, 耗时 {elapsed:.1f}s")
    print(f"缓存: {stats['cache_total_tokens']} token, {stats['cache_total_prices']} 条 price")
    if errors_by_type:
        print(f"错误分布: {dict(errors_by_type)}")


if __name__ == "__main__":
    main()
