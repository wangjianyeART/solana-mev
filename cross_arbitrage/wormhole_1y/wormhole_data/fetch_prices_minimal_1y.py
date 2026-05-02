#!/usr/bin/env python3
"""
1y 最小版价格抓取 (API 节省版, 仅 buy/sell 价):
  - 只抓每条 greedy swap 的 quote 侧 token @ swap.ts
    (entry swap = sold 侧, exit swap = bought 侧)
  - 跳过: bridge timeline 4 点, SOL/ETH native gas
  - 稳定币 $1 直出, 未解析符号跳过
  - 复用 exact_sol.json / series_eth_1m.json 缓存, resume 已抓过的对

输出: prices/exact_sol.json + series_eth_1m.json (追加)
"""

import json
import sys
import time
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_1y" / "matched" / "arbitrage"
PRICES_DIR = ROOT / "prices"
SYMBOL_MAP = PRICES_DIR / "symbol_map.json"
SOL_EXACT = PRICES_DIR / "exact_sol.json"
ETH_SERIES = PRICES_DIR / "series_eth_1m.json"
STATS = PRICES_DIR / "quote_stats_minimal.json"

API_UNIX = "https://public-api.birdeye.so/defi/historical_price_unix"
API_HIST = "https://public-api.birdeye.so/defi/history_price"

MAX_WORKERS = 15
REQ_TIMEOUT = 20

STABLES = {
    "solana:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "solana:Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7",
    "ethereum:0x6b175474e89094c44da98b954eedeac495271d0f",
}

SOL_NATIVE = "solana:So11111111111111111111111111111111111111112"
ETH_NATIVE = "ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def load_api_key():
    for p in [Path(__file__).parent.parent / ".env", Path(__file__).parent / ".env"]:
        if p.exists():
            for line in p.read_text().splitlines():
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
            return ETH_NATIVE
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


def collect_quote_only(files, symap):
    """只收集 greedy swap 的 quote 侧 (buy/sell 价)"""
    sol_pairs = set()
    eth_ts_by_token = defaultdict(list)
    stable_n = 0
    unresolved = defaultdict(int)
    bridge_miss = 0
    cand_n = 0

    for fp in files:
        print(f"  读 {fp.name} ...")
        doc = json.load(open(fp, encoding="utf-8"))
        for c in doc.get("candidates", []):
            cand_n += 1
            br = c.get("bridge") or {}
            spl_mint = br.get("spl_mint")
            erc20 = (br.get("erc20_contract") or "").lower()

            def proc_swap(swap, side):
                nonlocal stable_n, bridge_miss
                chain = swap.get("chain")
                ts_s = swap.get("ts")
                if not ts_s or not chain:
                    return
                bkey = None
                if chain == "SOL" and spl_mint:
                    bkey = f"solana:{spl_mint}"
                elif chain == "ETH" and erc20:
                    bkey = f"ethereum:{erc20}"
                items = swap.get("sold") if side == "entry" else swap.get("bought")
                if not items:
                    bridge_miss += 1
                    return
                for it in items:
                    tok = it.get("token")
                    key = resolve(chain, tok, symap)
                    if not key:
                        unresolved[f"{chain}:{tok}"] += 1
                        continue
                    if bkey and key == bkey:
                        continue
                    if key in STABLES:
                        stable_n += 1
                        continue
                    if key.startswith("solana:"):
                        sol_pairs.add((key, ts_s))
                    else:
                        eth_ts_by_token[key].append(ts_s)

            for s in c.get("entry_swaps_greedy") or []:
                proc_swap(s, "entry")
            for s in c.get("exit_swaps_greedy") or []:
                proc_swap(s, "exit")

    eth_ranges = {k: (min(v) - 60, max(v) + 60) for k, v in eth_ts_by_token.items()}
    return sol_pairs, eth_ranges, stable_n, bridge_miss, unresolved, cand_n


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
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            d = r.json()
            if not d.get("success"):
                return None, "no_success"
            val = (d.get("data") or {}).get("value")
            return (val, None) if val is not None else (None, "no_value")
        except Exception as e:
            if attempt == 2:
                return None, str(e)[:80]
            time.sleep(1)
    return None, "retries"


def fetch_eth_1m(key, tmin, tmax, api_key):
    _, addr = key.split(":", 1)
    WINDOW = 900 * 60
    series = {}
    errors = []
    cur = tmin
    end = tmax
    while cur < end:
        w_end = min(cur + WINDOW, end)
        for attempt in range(3):
            try:
                r = requests.get(
                    API_HIST,
                    params={"address": addr, "address_type": "token",
                            "type": "1m", "time_from": cur, "time_to": w_end},
                    headers={"X-API-KEY": api_key, "x-chain": "ethereum"},
                    timeout=REQ_TIMEOUT,
                )
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1)); continue
                if r.status_code != 200:
                    errors.append(f"http_{r.status_code}"); break
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


def main():
    args = sys.argv[1:]
    extract_only = "--extract-only" in args

    api_key = load_api_key()
    print(f"API key ok ({api_key[:6]}...)")

    symap = json.load(open(SYMBOL_MAP, encoding="utf-8"))
    files = sorted(ROOT.glob("arbitrage_candidates_*pct*.json"))
    files = [f for f in files if not f.name.endswith(".bak")]
    print(f"\n扫描 {len(files)} 个候选文件 (minimal: 只 buy/sell 价)...")

    sol_pairs, eth_ranges, stable_n, bridge_miss, unresolved, cand_n = collect_quote_only(files, symap)

    # 现有缓存里已有的 SOL 对数量
    sol_cache = json.load(open(SOL_EXACT, encoding="utf-8")) if SOL_EXACT.exists() else {}
    cached = sum(1 for k, t in sol_pairs if str(t) in sol_cache.get(k, {}))
    to_fetch_sol = len(sol_pairs) - cached

    eth_cache = json.load(open(ETH_SERIES, encoding="utf-8")) if ETH_SERIES.exists() else {}
    eth_to_fetch = sum(1 for k in eth_ranges if k not in eth_cache or not eth_cache[k])

    stats = {
        "mode": "minimal_buy_sell_only",
        "total_candidates": cand_n,
        "stable_quote_count": stable_n,
        "bridge_side_missing": bridge_miss,
        "sol_unique_pairs": len(sol_pairs),
        "sol_unique_tokens": len({k for k, _ in sol_pairs}),
        "sol_cached_hit": cached,
        "sol_to_fetch": to_fetch_sol,
        "eth_tokens": len(eth_ranges),
        "eth_cached_hit": len(eth_ranges) - eth_to_fetch,
        "eth_to_fetch": eth_to_fetch,
        "unresolved_count": sum(unresolved.values()),
        "unresolved_top": sorted(unresolved.items(), key=lambda kv: -kv[1])[:10],
    }
    json.dump(stats, open(STATS, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in stats.items() if k != "eth_token_list"}, indent=2, ensure_ascii=False))

    if extract_only:
        print("\n[extract-only] 跳过 fetch")
        return

    # ─── SOL ───
    todo_sol = [(k, t) for k, t in sol_pairs if str(t) not in sol_cache.get(k, {})]
    print(f"\n[SOL] 待抓: {len(todo_sol)} 对, workers={MAX_WORKERS}")
    if todo_sol:
        t0 = time.time()
        done = ok = err = 0
        errs = defaultdict(int)

        def _do(x):
            k, t = x
            return k, t, fetch_sol_exact(k, t, api_key)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(_do, x) for x in todo_sol]
            for f in as_completed(futs):
                k, t, (v, e) = f.result()
                done += 1
                if e is None:
                    sol_cache.setdefault(k, {})[str(t)] = v
                    ok += 1
                else:
                    err += 1; errs[e] += 1
                if done % 500 == 0:
                    rate = done / (time.time() - t0)
                    eta = (len(todo_sol) - done) / rate if rate else 0
                    print(f"  {done}/{len(todo_sol)} ok={ok} err={err} ({rate:.1f}/s ETA {eta:.0f}s)", flush=True)
                    json.dump(sol_cache, open(SOL_EXACT, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(sol_cache, open(SOL_EXACT, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  SOL 完成: ok={ok} err={err} ({time.time()-t0:.0f}s) err_types={dict(errs)}")

    # ─── ETH ───
    eth_todo = {k: v for k, v in eth_ranges.items() if k not in eth_cache or not eth_cache[k]}
    print(f"\n[ETH] 待抓 token: {len(eth_todo)}")
    if eth_todo:
        t0 = time.time()
        done = ok = err = 0

        def _doeth(kv):
            k, (tmin, tmax) = kv
            return k, fetch_eth_1m(k, tmin, tmax, api_key)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(_doeth, kv) for kv in eth_todo.items()]
            for f in as_completed(futs):
                k, (series, errors) = f.result()
                done += 1
                if series:
                    eth_cache[k] = series; ok += 1
                else:
                    err += 1
                if done % 10 == 0:
                    print(f"  {done}/{len(eth_todo)} ok={ok} err={err} ({time.time()-t0:.0f}s)", flush=True)
                    json.dump(eth_cache, open(ETH_SERIES, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(eth_cache, open(ETH_SERIES, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  ETH 完成: ok={ok} err={err} ({time.time()-t0:.0f}s)")

    print("\n全部完成")


if __name__ == "__main__":
    main()
