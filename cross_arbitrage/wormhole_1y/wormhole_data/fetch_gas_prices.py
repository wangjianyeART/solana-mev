#!/usr/bin/env python3
"""
为 gas fee 估值抓 SOL 原生在每个 sol_ts 的 1s 精确价格,
ETH 原生 (WETH) 已在 series_eth_1m 里 (1m), 不用补。
追加写入 exact_sol.json。
"""

import json
import time
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
CANDIDATES = ROOT / "arbitrage_candidates_1pct.json"
SOL_EXACT = ROOT / "prices" / "exact_sol.json"

API = "https://public-api.birdeye.so/defi/historical_price_unix"
SOL_NATIVE = "solana:So11111111111111111111111111111111111111112"
SOL_ADDR = "So11111111111111111111111111111111111111112"
MAX_WORKERS = 15


def load_key():
    env = Path(__file__).parent.parent / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("BIRDEYE_API_KEY"):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("no BIRDEYE_API_KEY")


def fetch_one(ts, api_key):
    for attempt in range(3):
        try:
            r = requests.get(
                API,
                params={"address": SOL_ADDR, "unixtime": ts},
                headers={"X-API-KEY": api_key, "x-chain": "solana"},
                timeout=20,
            )
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            d = r.json()
            val = (d.get("data") or {}).get("value")
            if val is None:
                return None, "no_value"
            return val, None
        except Exception as e:
            if attempt == 2:
                return None, str(e)[:60]
            time.sleep(1)
    return None, "retries"


def main():
    api_key = load_key()
    cands = json.load(open(CANDIDATES))["candidates"]
    sol_ts_all = sorted({c["sol_ts"] for c in cands if c.get("sol_ts")})
    print(f"{len(sol_ts_all)} 个唯一 sol_ts")

    cache = {}
    if SOL_EXACT.exists():
        cache = json.load(open(SOL_EXACT))
    sol_series = cache.setdefault(SOL_NATIVE, {})

    todo = [t for t in sol_ts_all if str(t) not in sol_series]
    print(f"待抓: {len(todo)}")
    if not todo:
        print("无需补")
        return

    t0 = time.time()
    done = ok = err = 0
    err_types = defaultdict(int)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_one, t, api_key): t for t in todo}
        for fut in as_completed(futures):
            t = futures[fut]
            val, e = fut.result()
            done += 1
            if e is None:
                sol_series[str(t)] = val
                ok += 1
            else:
                err += 1
                err_types[e] += 1
            if done % 200 == 0:
                rate = done / (time.time() - t0)
                print(f"  {done}/{len(todo)} ok={ok} err={err} ({rate:.1f}/s)")
                json.dump(cache, open(SOL_EXACT, "w"), ensure_ascii=False)

    json.dump(cache, open(SOL_EXACT, "w"), ensure_ascii=False)
    print(f"完成: ok={ok}, err={err}, 耗时 {time.time()-t0:.1f}s, 错误: {dict(err_types)}")


if __name__ == "__main__":
    main()
