#!/usr/bin/env python3
"""
用 fetch_liquidity_volume 的缓存给每条 candidate 加:
  - current_liquidity: {sol, eth}  当前池子深度 USD
  - volume_1h_at_trade: {t_buy/t_bridge_out/t_bridge_in/t_sell: {sol, eth} (USD)}

重跑后也顺便用新的 exact_sol + series_eth_1m 重建 prices_timeline.
"""

import json
from pathlib import Path
from compute_pnl import PriceLookup
from enrich_price_timeline import enrich_one as enrich_prices

ROOT = Path(__file__).parent
ARB = ROOT / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
PRICES = ARB / "prices"

OVW_SOL = PRICES / "token_overview_sol.json"
OVW_ETH = PRICES / "token_overview_eth.json"
OHLCV_SOL = PRICES / "ohlcv_1h_sol.json"
OHLCV_ETH = PRICES / "ohlcv_1h_eth.json"


def nearest_ohlcv(series, ts):
    """返回最近的 1h 桶 v_usd (tol=3600 因为 1H 桶)"""
    if not series: return None, None
    best_k, best_d = None, None
    for k in series.keys():
        d = abs(int(k) - int(ts))
        if best_d is None or d < best_d:
            best_d, best_k = d, k
    if best_k and best_d <= 3600:
        return series[best_k].get("v_usd"), best_d
    return None, best_d


def main():
    overview_sol = json.load(open(OVW_SOL)) if OVW_SOL.exists() else {}
    overview_eth = json.load(open(OVW_ETH)) if OVW_ETH.exists() else {}
    ohlcv_sol = json.load(open(OHLCV_SOL)) if OHLCV_SOL.exists() else {}
    ohlcv_eth = json.load(open(OHLCV_ETH)) if OHLCV_ETH.exists() else {}
    prices = PriceLookup()  # 用最新 exact_sol + series_eth_1m 重建时间线

    print(f"overview cached: sol={len(overview_sol)} eth={len(overview_eth)}")
    print(f"ohlcv cached:    sol={len(ohlcv_sol)} eth={len(ohlcv_eth)}")

    for fp in sorted(ARB.glob("arbitrage_candidates_*pct.json")):
        doc = json.load(open(fp, encoding="utf-8"))
        updated_tl = updated_lv = 0
        for c in doc["candidates"]:
            # 先重建 timeline (用新缓存)
            new_tl = enrich_prices(c, prices)
            if new_tl:
                c["prices_timeline"] = new_tl
                updated_tl += 1
                sol_key = new_tl["sol_key"]
                eth_key = new_tl["eth_key"]
            else:
                continue

            # current liquidity
            ov_s = overview_sol.get(sol_key) or {}
            ov_e = overview_eth.get(eth_key) or {}
            c["current_liquidity"] = {
                "sol_usd": ov_s.get("liquidity"),
                "eth_usd": ov_e.get("liquidity"),
                "sol_v24h_usd": ov_s.get("v24hUSD"),
                "eth_v24h_usd": ov_e.get("v24hUSD"),
                "sol_mc": ov_s.get("marketCap"),
                "eth_mc": ov_e.get("marketCap"),
            }

            # volume at each 4 points
            s_series = ohlcv_sol.get(sol_key, {})
            e_series = ohlcv_eth.get(eth_key, {})
            vols = {}
            for label, pt in (new_tl.get("points") or {}).items():
                if not pt:
                    vols[label] = None; continue
                ts = pt.get("ts")
                v_sol, off_s = nearest_ohlcv(s_series, ts) if s_series else (None, None)
                v_eth, off_e = nearest_ohlcv(e_series, ts) if e_series else (None, None)
                vols[label] = {"sol_v1h_usd": v_sol, "eth_v1h_usd": v_eth,
                               "sol_offset": off_s, "eth_offset": off_e}
            c["volume_1h_at_trade"] = vols
            updated_lv += 1

        json.dump(doc, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"{fp.name}: timeline 重建 {updated_tl}, liquidity/volume 填 {updated_lv}")


if __name__ == "__main__":
    main()
