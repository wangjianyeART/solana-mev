#!/usr/bin/env python3
"""
1y 最小版流动性抓取:
  - 只抓 bridge token 的 token_overview (当前流动性 + v24hUSD 成交量)
  - 跳过 OHLCV 历史成交量 (节省 API)
  - 一个 token 一次 call

输出: prices/token_overview_sol.json + token_overview_eth.json
"""

import json, time
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path(__file__).parent
ARB = ROOT / "use" / "portal_full" / "recent_1y" / "matched" / "arbitrage"
PRICES = ARB / "prices"
OVW_SOL = PRICES / "token_overview_sol.json"
OVW_ETH = PRICES / "token_overview_eth.json"

API_OVW = "https://public-api.birdeye.so/defi/token_overview"
MAX_WORKERS = 8
REQ_TIMEOUT = 20


def load_api_key():
    for p in [ROOT.parent / ".env", ROOT / ".env"]:
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("BIRDEYE_API_KEY"):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("BIRDEYE_API_KEY 未找到")


def collect_bridge_tokens():
    sol_set, eth_set = set(), set()
    for fp in sorted(ARB.glob("arbitrage_candidates_*pct*.json")):
        if fp.name.endswith(".bak"):
            continue
        doc = json.load(open(fp, encoding="utf-8"))
        for c in doc["candidates"]:
            br = c.get("bridge") or {}
            if br.get("spl_mint"):
                sol_set.add("solana:" + br["spl_mint"])
            if br.get("erc20_contract"):
                eth_set.add("ethereum:" + br["erc20_contract"].lower())
    return sol_set, eth_set


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
            keep = ["liquidity", "price", "marketCap", "fdv", "holder",
                    "v1hUSD", "v24hUSD", "v24hChangePercent",
                    "numberMarkets", "lastTradeUnixTime", "symbol", "name"]
            return {k: data.get(k) for k in keep}, None
        except Exception as e:
            if attempt == 2: return None, str(e)[:80]
            time.sleep(1)
    return None, "retries"


def main():
    api_key = load_api_key()
    print(f"API key ok ({api_key[:6]}...)")

    sol_set, eth_set = collect_bridge_tokens()
    print(f"SOL bridge tokens: {len(sol_set)}, ETH bridge tokens: {len(eth_set)}")

    for target_file, tokens, chain, label in [
        (OVW_SOL, sol_set, "solana", "SOL"),
        (OVW_ETH, eth_set, "ethereum", "ETH"),
    ]:
        cache = json.load(open(target_file, encoding="utf-8")) if target_file.exists() else {}
        todo = [k for k in tokens if k not in cache]
        print(f"\n[{label}] 待抓: {len(todo)} / {len(tokens)} (已缓存 {len(cache)})", flush=True)
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
                    print(f"  {done}/{len(todo)} ok={ok} err={err} ({done/(time.time()-t0):.1f}/s)", flush=True)
                    json.dump(cache, open(target_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        json.dump(cache, open(target_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  {label} 完成: ok={ok} err={err} ({time.time()-t0:.0f}s) errs={dict(err_types)}", flush=True)

    print("\n全部完成")


if __name__ == "__main__":
    main()
