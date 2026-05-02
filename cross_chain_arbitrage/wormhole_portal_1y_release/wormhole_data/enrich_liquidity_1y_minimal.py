#!/usr/bin/env python3
"""
1y 最小版: 把 token_overview 缓存注入每条 candidate:
  - current_liquidity: {sol_usd, eth_usd, sol_v24h_usd, eth_v24h_usd, sol_mc, eth_mc}
  - liquidity_category: dead_pool_exploit / thin_pool_arb / healthy_market_arb
                        (based on min(sol_liq, eth_liq))

跳过 volume_1h_at_trade (需 OHLCV, 省 API)。
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent
ARB = ROOT / "use" / "portal_full" / "recent_1y" / "matched" / "arbitrage"
PRICES = ARB / "prices"
OVW_SOL = PRICES / "token_overview_sol.json"
OVW_ETH = PRICES / "token_overview_eth.json"


def classify(min_liq):
    if min_liq is None:
        return None
    if min_liq < 1000:
        return "dead_pool_exploit"
    if min_liq < 100_000:
        return "thin_pool_arb"
    return "healthy_market_arb"


def main():
    ov_sol = json.load(open(OVW_SOL, encoding="utf-8")) if OVW_SOL.exists() else {}
    ov_eth = json.load(open(OVW_ETH, encoding="utf-8")) if OVW_ETH.exists() else {}
    print(f"overview: sol={len(ov_sol)} eth={len(ov_eth)}")

    for fp in sorted(ARB.glob("arbitrage_candidates_*pct*.json")):
        if fp.name.endswith(".bak"):
            continue
        doc = json.load(open(fp, encoding="utf-8"))
        n_total = len(doc["candidates"])
        n_lq = 0
        cat_counts = {"dead_pool_exploit": 0, "thin_pool_arb": 0,
                      "healthy_market_arb": 0, None: 0}
        for c in doc["candidates"]:
            br = c.get("bridge") or {}
            sol_key = f"solana:{br['spl_mint']}" if br.get("spl_mint") else None
            eth_key = f"ethereum:{br['erc20_contract'].lower()}" if br.get("erc20_contract") else None
            s = ov_sol.get(sol_key) or {} if sol_key else {}
            e = ov_eth.get(eth_key) or {} if eth_key else {}
            sol_liq = s.get("liquidity")
            eth_liq = e.get("liquidity")
            cl = {
                "sol_usd": sol_liq,
                "eth_usd": eth_liq,
                "sol_v24h_usd": s.get("v24hUSD"),
                "eth_v24h_usd": e.get("v24hUSD"),
                "sol_mc": s.get("marketCap"),
                "eth_mc": e.get("marketCap"),
                "sol_symbol": s.get("symbol"),
                "eth_symbol": e.get("symbol"),
            }
            c["current_liquidity"] = cl

            mins = [x for x in (sol_liq, eth_liq) if x is not None]
            min_liq = min(mins) if mins else None
            cat = classify(min_liq)
            c["liquidity_category"] = cat
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            if min_liq is not None:
                n_lq += 1

        doc.setdefault("meta", {})["liquidity_enriched"] = {
            "total": n_total, "has_liq": n_lq, "category_dist": cat_counts,
        }
        json.dump(doc, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"{fp.name}: total={n_total} has_liq={n_lq} dist={cat_counts}")


if __name__ == "__main__":
    main()
