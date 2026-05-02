#!/usr/bin/env python3
"""
精确价格抓取:
  - 识别每条 swap 的 quote 侧 (非 bridge_token 那一侧)
  - SOL 侧 quote: 用 /defi/historical_price_unix 逐 ts 精确查 (1s 粒度)
  - ETH 侧 quote: 用 /defi/history_price type=1m 抓时间序列
  - 稳定币直接赋 $1, 不查

输出:
  prices/exact_sol.json   {"solana:addr": {"<ts_unix>": price}}
  prices/series_eth_1m.json {"ethereum:addr": {"<ts_unix>": price}}
  prices/quote_stats.json   统计

速率: Birdeye Lite 900 rpm
"""

import json
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
SOL_EXACT = PRICES_DIR / "exact_sol.json"
ETH_SERIES = PRICES_DIR / "series_eth_1m.json"
STATS = PRICES_DIR / "quote_stats.json"

API_UNIX = "https://public-api.birdeye.so/defi/historical_price_unix"
API_HIST = "https://public-api.birdeye.so/defi/history_price"

MAX_WORKERS = 15
REQ_TIMEOUT = 20
RATE_PER_MIN = 900
MIN_REQ_INTERVAL = 60.0 / RATE_PER_MIN  # ~0.067s

# 稳定币直接 $1
STABLES = {
    "solana:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "solana:Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "ethereum:0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
}


def load_api_key():
    env = (Path(__file__).parent.parent / ".env").read_text()
    for line in env.splitlines():
        if line.startswith("BIRDEYE_API_KEY"):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("BIRDEYE_API_KEY 未找到")


def is_eth_addr(s):
    return isinstance(s, str) and s.startswith("0x") and len(s) == 42

def is_sol_addr(s):
    return isinstance(s, str) and 32 <= len(s) <= 44 and not s.startswith("0x") and s.isalnum()


def resolve(chain, tok, symap):
    if not tok:
        return None
    if chain == "ETH":
        if tok == "ETH":
            return "ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
        if is_eth_addr(tok):
            return f"ethereum:{tok.lower()}"
        addr = symap["eth"].get(tok)
        return f"ethereum:{addr.lower()}" if addr else None
    elif chain == "SOL":
        if tok in ("SOL", "WSOL"):
            return "solana:So11111111111111111111111111111111111111112"
        if is_sol_addr(tok):
            return f"solana:{tok}"
        addr = symap["sol"].get(tok)
        return f"solana:{addr}" if addr else None
    return None


def extract_quote_queries(cands, symap):
    """
    对每条 swap 识别 quote 侧, 生成查询对。
    返回:
      sol_exact: set of (token_key, ts)       — SOL 侧用 historical_price_unix
      eth_tokens: {token_key: (ts_min, ts_max)}  — ETH 侧用 history_price 1m
      stable_count, bridge_side_misses, unresolved
    """
    sol_exact = set()
    eth_ts_by_token = defaultdict(list)
    stable_count = 0
    bridge_misses = 0
    unresolved = defaultdict(int)

    for c in cands:
        bridge = c.get("bridge") or {}
        sol_mint = bridge.get("spl_mint")
        eth_contract = (bridge.get("erc20_contract") or "").lower()

        def process_swap(swap, side):
            nonlocal stable_count, bridge_misses
            chain = swap.get("chain")
            ts = swap.get("ts")
            if not ts or not chain:
                return
            bridge_key = None
            if chain == "SOL" and sol_mint:
                bridge_key = f"solana:{sol_mint}"
            elif chain == "ETH" and eth_contract:
                bridge_key = f"ethereum:{eth_contract}"

            # quote 侧
            quote_items = swap.get("sold") if side == "entry" else swap.get("bought")
            if not quote_items:
                bridge_misses += 1
                return
            for item in quote_items:
                tok = item.get("token")
                key = resolve(chain, tok, symap)
                if not key:
                    unresolved[f"{chain}:{tok}"] += 1
                    continue
                # 如果 quote == bridge_token, 异常 (对倒/套娃), 跳过
                if bridge_key and key == bridge_key:
                    continue
                if key in STABLES:
                    stable_count += 1
                    continue
                if key.startswith("solana:"):
                    sol_exact.add((key, ts))
                else:
                    eth_ts_by_token[key].append(ts)

        for s in (c.get("entry_swaps_greedy") or []):
            process_swap(s, "entry")
        for s in (c.get("exit_swaps_greedy") or []):
            process_swap(s, "exit")

    eth_ranges = {k: (min(v), max(v)) for k, v in eth_ts_by_token.items()}
    return sol_exact, eth_ranges, stable_count, bridge_misses, unresolved


# ─────────── SOL exact-ts ───────────

def fetch_sol_exact(key, ts, api_key):
    _, addr = key.split(":", 1)
    for attempt in range(3):
        try:
            r = requests.get(
                API_UNIX,
                params={"address": addr, "unixtime": ts},
                headers={"X-API-KEY": api_key, "x-chain": "solana"},
                timeout=REQ_TIMEOUT,
            )
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            d = r.json()
            if not d.get("success"):
                return None, "no_success"
            val = (d.get("data") or {}).get("value")
            if val is None:
                return None, "no_value"
            return val, None
        except Exception as e:
            if attempt == 2:
                return None, str(e)[:80]
            time.sleep(1)
    return None, "retries"


# ─────────── ETH 1m series ───────────

def fetch_eth_1m_range(key, tmin, tmax, api_key):
    """可能需要分段 (Birdeye 1m 限 1000 点 = 1000min ≈ 16.7h)"""
    _, addr = key.split(":", 1)
    WINDOW = 900 * 60  # 900 min per call (留 buffer)
    series = {}
    errors = []
    cur = tmin - 60  # 1min buffer
    end = tmax + 60
    while cur < end:
        w_end = min(cur + WINDOW, end)
        for attempt in range(3):
            try:
                r = requests.get(
                    API_HIST,
                    params={
                        "address": addr,
                        "address_type": "token",
                        "type": "1m",
                        "time_from": cur,
                        "time_to": w_end,
                    },
                    headers={"X-API-KEY": api_key, "x-chain": "ethereum"},
                    timeout=REQ_TIMEOUT,
                )
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                if r.status_code != 200:
                    errors.append(f"http_{r.status_code}")
                    break
                d = r.json()
                items = (d.get("data") or {}).get("items") or []
                for it in items:
                    if "value" in it and "unixTime" in it:
                        series[str(it["unixTime"])] = it["value"]
                break
            except Exception as e:
                if attempt == 2:
                    errors.append(str(e)[:60])
                time.sleep(1)
        cur = w_end
    return series, errors


# ─────────── main ───────────

def main():
    args = sys.argv[1:]
    extract_only = "--extract-only" in args

    api_key = load_api_key()
    print(f"API key ok ({api_key[:6]}...)")

    print(f"\n加载候选...")
    cands = json.load(open(CANDIDATES, encoding="utf-8"))["candidates"]
    symap = json.load(open(SYMBOL_MAP, encoding="utf-8"))
    print(f"  {len(cands)} 条")

    sol_exact, eth_ranges, stable_n, bridge_miss, unresolved = extract_quote_queries(cands, symap)

    stats = {
        "stable_quote_count": stable_n,
        "bridge_side_missing": bridge_miss,
        "sol_exact_unique_pairs": len(sol_exact),
        "sol_exact_unique_tokens": len({k for k, _ in sol_exact}),
        "eth_quote_tokens": len(eth_ranges),
        "eth_quote_token_list": sorted(eth_ranges.keys()),
        "unresolved_count": sum(unresolved.values()),
        "unresolved_top": sorted(unresolved.items(), key=lambda kv: -kv[1])[:10],
    }
    json.dump(stats, open(STATS, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in stats.items() if k != "eth_quote_token_list"}, indent=2, ensure_ascii=False))

    if extract_only:
        print("\n[extract-only] 跳过 fetch")
        return

    # ─── fetch SOL ───
    sol_cache = {}
    if SOL_EXACT.exists():
        sol_cache = json.load(open(SOL_EXACT, encoding="utf-8"))

    todo = [(k, t) for k, t in sol_exact if str(t) not in sol_cache.get(k, {})]
    print(f"\n[SOL] 待抓: {len(todo)} 对 (并发 {MAX_WORKERS})")
    if todo:
        t0 = time.time()
        done = ok = err = 0
        err_types = defaultdict(int)

        def _do(item):
            k, t = item
            return k, t, fetch_sol_exact(k, t, api_key)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_do, x): x for x in todo}
            for fut in as_completed(futures):
                k, t, (val, e) = fut.result()
                done += 1
                if e is None:
                    sol_cache.setdefault(k, {})[str(t)] = val
                    ok += 1
                else:
                    err += 1
                    err_types[e] += 1
                if done % 200 == 0:
                    rate = done / (time.time() - t0)
                    eta = (len(todo) - done) / rate if rate else 0
                    print(f"  {done}/{len(todo)} ok={ok} err={err} ({rate:.1f}/s, ETA {eta:.0f}s)")
                    json.dump(sol_cache, open(SOL_EXACT, "w", encoding="utf-8"), ensure_ascii=False)

        json.dump(sol_cache, open(SOL_EXACT, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  完成: ok={ok}, err={err}, 耗时 {time.time()-t0:.1f}s, 错误类型: {dict(err_types)}")

    # ─── fetch ETH ───
    eth_cache = {}
    if ETH_SERIES.exists():
        eth_cache = json.load(open(ETH_SERIES, encoding="utf-8"))

    eth_todo = {k: v for k, v in eth_ranges.items() if k not in eth_cache or not eth_cache[k]}
    print(f"\n[ETH] 待抓: {len(eth_todo)} token")
    if eth_todo:
        t0 = time.time()
        done = ok = err = 0

        def _doeth(item):
            k, (tmin, tmax) = item
            return k, fetch_eth_1m_range(k, tmin, tmax, api_key)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_doeth, kv): kv[0] for kv in eth_todo.items()}
            for fut in as_completed(futures):
                k, (series, errors) = fut.result()
                done += 1
                if series:
                    eth_cache[k] = series
                    ok += 1
                else:
                    err += 1
                if done % 5 == 0:
                    print(f"  {done}/{len(eth_todo)} ok={ok} err={err} ({time.time()-t0:.0f}s)")
                    json.dump(eth_cache, open(ETH_SERIES, "w", encoding="utf-8"), ensure_ascii=False)

        json.dump(eth_cache, open(ETH_SERIES, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  完成: ok={ok}, err={err}, 耗时 {time.time()-t0:.1f}s")

    print("\n全部完成")


if __name__ == "__main__":
    main()
