#!/usr/bin/env python3
"""
给 prices_timeline 的 4 个时间点补齐 Birdeye 细粒度价:
  - SOL 侧桥接 token: /defi/historical_price_unix (1s 精度, 逐 ts 查)
  - ETH 侧桥接 token: /defi/history_price type=1m (1m 精度, 按 token 整段抓)

写入:
  prices/exact_sol.json    (已存在, 追加)
  prices/series_eth_1m.json (已存在, 追加)
"""

import json, time, sys
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path(__file__).parent
ARB = ROOT / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
PRICES = ARB / "prices"
SOL_EXACT = PRICES / "exact_sol.json"
ETH_1M = PRICES / "series_eth_1m.json"

API_UNIX = "https://public-api.birdeye.so/defi/historical_price_unix"
API_HIST = "https://public-api.birdeye.so/defi/history_price"
MAX_WORKERS = 15
REQ_TIMEOUT = 20


def load_api_key():
    for p in [ROOT.parent / ".env", ROOT / ".env", Path.home() / ".env"]:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("BIRDEYE_API_KEY"):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("BIRDEYE_API_KEY 未找到")


def collect_pairs():
    """遍历 4 个 arb 文件 prices_timeline, 收集桥接 token + 4 个时间点"""
    sol_pairs = set()        # (sol_key, ts)
    eth_ranges = defaultdict(list)  # eth_key -> [ts, ts, ts, ts]

    for fp in sorted(ARB.glob("arbitrage_candidates_*pct.json")):
        doc = json.load(open(fp, encoding="utf-8"))
        for c in doc["candidates"]:
            tl = c.get("prices_timeline")
            if not tl:
                continue
            sol_key = tl["sol_key"]
            eth_key = tl["eth_key"]
            for p in tl["points"].values():
                if not p:
                    continue
                ts = p.get("ts")
                if ts is None:
                    continue
                sol_pairs.add((sol_key, ts))
                eth_ranges[eth_key].append(ts)
    eth_range_minmax = {k: (min(v) - 60, max(v) + 60) for k, v in eth_ranges.items()}
    return sol_pairs, eth_range_minmax


# ─── fetch helpers ───

def fetch_sol_exact(key, ts, api_key):
    _, addr = key.split(":", 1)
    for attempt in range(3):
        try:
            r = requests.get(API_UNIX,
                params={"address": addr, "unixtime": ts},
                headers={"X-API-KEY": api_key, "x-chain": "solana"},
                timeout=REQ_TIMEOUT)
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
                r = requests.get(API_HIST,
                    params={"address": addr, "address_type": "token",
                            "type": "1m", "time_from": cur, "time_to": w_end},
                    headers={"X-API-KEY": api_key, "x-chain": "ethereum"},
                    timeout=REQ_TIMEOUT)
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
                if attempt == 2: errors.append(str(e)[:60])
                time.sleep(1)
        cur = w_end
    return series, errors


def main():
    api_key = load_api_key()
    print(f"API key ok ({api_key[:6]}...)")

    print("\n收集 (token, ts) 对...")
    sol_pairs, eth_ranges = collect_pairs()
    print(f"  SOL unique (key, ts): {len(sol_pairs)}  (unique tokens: {len({k for k,_ in sol_pairs})})")
    print(f"  ETH unique tokens: {len(eth_ranges)}")

    # ─── SOL 增量 ───
    sol_cache = json.load(open(SOL_EXACT, encoding="utf-8")) if SOL_EXACT.exists() else {}
    todo_sol = [(k, t) for k, t in sol_pairs if str(t) not in sol_cache.get(k, {})]
    print(f"\n[SOL] 待抓: {len(todo_sol)} 对 (已缓存 {len(sol_pairs)-len(todo_sol)})")
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
                    print(f"  {done}/{len(todo_sol)} ok={ok} err={err} ({rate:.1f}/s ETA {eta:.0f}s)")
                    json.dump(sol_cache, open(SOL_EXACT, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(sol_cache, open(SOL_EXACT, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  SOL 完成: ok={ok} err={err} ({time.time()-t0:.0f}s) err_types={dict(errs)}")

    # ─── ETH 增量 ───
    eth_cache = json.load(open(ETH_1M, encoding="utf-8")) if ETH_1M.exists() else {}
    eth_todo = {k: v for k, v in eth_ranges.items() if k not in eth_cache or not eth_cache[k]}
    print(f"\n[ETH] 待抓 token: {len(eth_todo)} (已缓存 {len(eth_ranges)-len(eth_todo)})")
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
                if done % 20 == 0:
                    print(f"  {done}/{len(eth_todo)} ok={ok} err={err} ({time.time()-t0:.0f}s)")
                    json.dump(eth_cache, open(ETH_1M, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(eth_cache, open(ETH_1M, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  ETH 完成: ok={ok} err={err} ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
