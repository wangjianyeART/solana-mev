#!/usr/bin/env python3
"""
为每个桥接 token 抓:
  1. 当前流动性 (Birdeye token_overview)
  2. 历史 1H OHLCV (覆盖所有 record 时间戳) → 取出每个小时桶的成交量

写入:
  prices/token_overview_sol.json  {"solana:addr": {liquidity, mc, v24h, ...}}
  prices/token_overview_eth.json  同上 (x-chain: ethereum)
  prices/ohlcv_1h_sol.json        {"solana:addr": {"<unixTime>": v_usd}}
  prices/ohlcv_1h_eth.json        同上
"""

import json, time, sys
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path(__file__).parent
ARB = ROOT / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
PRICES = ARB / "prices"
OVW_SOL = PRICES / "token_overview_sol.json"
OVW_ETH = PRICES / "token_overview_eth.json"
OHLCV_SOL = PRICES / "ohlcv_1h_sol.json"
OHLCV_ETH = PRICES / "ohlcv_1h_eth.json"

API_OVW = "https://public-api.birdeye.so/defi/token_overview"
API_OHLCV = "https://public-api.birdeye.so/defi/ohlcv"
MAX_WORKERS = 8  # 低并发避免和 price fetch 打架
REQ_TIMEOUT = 20


def load_api_key():
    for p in [ROOT.parent / ".env", ROOT / ".env"]:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("BIRDEYE_API_KEY"):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("BIRDEYE_API_KEY 未找到")


def collect_tokens():
    """返回 (sol_tokens: {key: [ts,...]}, eth_tokens: {key: [ts,...]})"""
    sol, eth = defaultdict(list), defaultdict(list)
    for fp in sorted(ARB.glob("arbitrage_candidates_*pct.json")):
        doc = json.load(open(fp, encoding="utf-8"))
        for c in doc["candidates"]:
            tl = c.get("prices_timeline")
            if not tl:
                continue
            sol_key = tl["sol_key"]
            eth_key = tl["eth_key"]
            for p in (tl.get("points") or {}).values():
                if not p: continue
                ts = p.get("ts")
                if ts is None: continue
                sol[sol_key].append(ts)
                eth[eth_key].append(ts)
    return sol, eth


def fetch_overview(addr, chain, api_key):
    for attempt in range(3):
        try:
            r = requests.get(API_OVW,
                params={"address": addr},
                headers={"X-API-KEY": api_key, "x-chain": chain},
                timeout=REQ_TIMEOUT)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            d = r.json()
            if not d.get("success"):
                return None, "no_success"
            data = d.get("data") or {}
            # 提取关键字段
            keep = ["liquidity", "price", "marketCap", "fdv", "holder",
                    "v1hUSD", "v24hUSD", "v24hChangePercent",
                    "numberMarkets", "lastTradeUnixTime", "symbol", "name"]
            return {k: data.get(k) for k in keep}, None
        except Exception as e:
            if attempt == 2: return None, str(e)[:80]
            time.sleep(1)
    return None, "retries"


def fetch_ohlcv_1h(addr, chain, tmin, tmax, api_key):
    WINDOW = 900 * 3600  # 900 小时 / call, buffer
    series = {}
    cur = tmin - 3600
    end = tmax + 3600
    while cur < end:
        w_end = min(cur + WINDOW, end)
        for attempt in range(3):
            try:
                r = requests.get(API_OHLCV,
                    params={"address": addr, "type": "1H",
                            "time_from": cur, "time_to": w_end},
                    headers={"X-API-KEY": api_key, "x-chain": chain},
                    timeout=REQ_TIMEOUT)
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1)); continue
                if r.status_code != 200:
                    break
                d = r.json()
                items = (d.get("data") or {}).get("items") or []
                for it in items:
                    ut = it.get("unixTime")
                    v = it.get("v")  # volume (native)
                    vusd = it.get("v") if "vUSD" not in it else it.get("vUSD")
                    # v 是 native, 用 v × close 近似 USD
                    close = it.get("c")
                    v_usd = (v * close) if (v is not None and close) else None
                    if ut is not None:
                        series[str(ut)] = {"v": v, "v_usd": v_usd, "c": close}
                break
            except Exception:
                time.sleep(1)
        cur = w_end
    return series


# ─── main ───

def main():
    api_key = load_api_key()
    print(f"API key ok ({api_key[:6]}...)")

    sol_tokens, eth_tokens = collect_tokens()
    print(f"SOL tokens: {len(sol_tokens)}, ETH tokens: {len(eth_tokens)}")

    # overview
    for target_file, tokens, chain, label in [
        (OVW_SOL, sol_tokens, "solana", "SOL"),
        (OVW_ETH, eth_tokens, "ethereum", "ETH"),
    ]:
        cache = json.load(open(target_file, encoding="utf-8")) if target_file.exists() else {}
        todo = [k for k in tokens if k not in cache]
        print(f"\n[{label} overview] 待抓: {len(todo)} / {len(tokens)} (已缓存 {len(cache)})")
        if not todo: continue
        t0 = time.time()
        done = ok = err = 0
        err_types = defaultdict(int)
        def _do(k):
            addr = k.split(":", 1)[1]
            return k, fetch_overview(addr, chain, api_key)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(_do, k) for k in todo]
            for f in as_completed(futs):
                k, (data, e) = f.result()
                done += 1
                if data is not None:
                    cache[k] = data; ok += 1
                else:
                    err += 1; err_types[e] += 1
                if done % 30 == 0:
                    print(f"  {done}/{len(todo)} ok={ok} err={err} ({done/(time.time()-t0):.1f}/s)")
                    json.dump(cache, open(target_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        json.dump(cache, open(target_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  {label} overview 完成: ok={ok} err={err} ({time.time()-t0:.0f}s) errs={dict(err_types)}")

    # OHLCV 1H
    for target_file, tokens, chain, label in [
        (OHLCV_SOL, sol_tokens, "solana", "SOL"),
        (OHLCV_ETH, eth_tokens, "ethereum", "ETH"),
    ]:
        cache = json.load(open(target_file, encoding="utf-8")) if target_file.exists() else {}
        todo = []
        for k, tss in tokens.items():
            if k not in cache or not cache[k]:
                todo.append((k, min(tss), max(tss)))
        print(f"\n[{label} OHLCV 1H] 待抓: {len(todo)} / {len(tokens)} (已缓存 {len(cache)})")
        if not todo: continue
        t0 = time.time()
        done = ok = err = 0
        def _do(x):
            k, tmin, tmax = x
            addr = k.split(":", 1)[1]
            return k, fetch_ohlcv_1h(addr, chain, tmin, tmax, api_key)
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(_do, x) for x in todo]
            for f in as_completed(futs):
                k, series = f.result()
                done += 1
                if series:
                    cache[k] = series; ok += 1
                else:
                    err += 1
                if done % 20 == 0:
                    print(f"  {done}/{len(todo)} ok={ok} err={err} ({time.time()-t0:.0f}s)")
                    json.dump(cache, open(target_file, "w", encoding="utf-8"), ensure_ascii=False)
        json.dump(cache, open(target_file, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"  {label} OHLCV 完成: ok={ok} err={err} ({time.time()-t0:.0f}s)")

    print("\n全部完成")


if __name__ == "__main__":
    main()
