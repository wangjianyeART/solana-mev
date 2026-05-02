#!/usr/bin/env python3
"""
1y 版: 给每条 arbitrage candidate 加 4 个时间点 × 2 条链的桥接资产价格。
使用 compute_pnl_1y.PriceLookup (严格 ts 精度, 无 1H fallback)。
"""

import json, statistics
from pathlib import Path
from compute_pnl_1y import PriceLookup

ROOT = Path(__file__).parent
ARB = ROOT / "use" / "portal_full" / "recent_1y" / "matched" / "arbitrage"
FILES = sorted(ARB.glob("arbitrage_candidates_*pct*.json"))
FILES = [f for f in FILES if not f.name.endswith(".bak")]


def median_ts(swaps):
    ts = [s.get("ts") for s in (swaps or []) if s.get("ts") is not None]
    return int(statistics.median(ts)) if ts else None


def build_timeline(c):
    br = c.get("bridge") or {}
    spl_mint = br.get("spl_mint")
    erc20 = br.get("erc20_contract")
    if not spl_mint or not erc20:
        return None
    sol_key = f"solana:{spl_mint}"
    eth_key = f"ethereum:{erc20.lower()}"

    direction = c.get("direction")
    sol_ts = c.get("sol_ts")
    eth_ts = c.get("eth_ts")
    t_buy = median_ts(c.get("entry_swaps_greedy"))
    t_sell = median_ts(c.get("exit_swaps_greedy"))

    if direction == "SOL→ETH":
        t_bridge_out, t_bridge_in = sol_ts, eth_ts
    elif direction == "ETH→SOL":
        t_bridge_out, t_bridge_in = eth_ts, sol_ts
    else:
        return None

    return {
        "sol_key": sol_key,
        "eth_key": eth_key,
        "points": {
            "t_buy": t_buy,
            "t_bridge_out": t_bridge_out,
            "t_bridge_in": t_bridge_in,
            "t_sell": t_sell,
        },
    }


def enrich_one(c, prices):
    tl = build_timeline(c)
    if not tl:
        return None
    sol_key, eth_key = tl["sol_key"], tl["eth_key"]
    out = {"sol_key": sol_key, "eth_key": eth_key, "points": {}}
    for label, ts in tl["points"].items():
        if ts is None:
            out["points"][label] = None
            continue
        sol_p, sol_src, sol_off = prices.get(sol_key, ts)
        eth_p, eth_src, eth_off = prices.get(eth_key, ts)
        gap = None
        if sol_p and eth_p and sol_p > 0:
            gap = round(eth_p / sol_p, 4)
        out["points"][label] = {
            "ts": ts,
            "sol": {"price": sol_p, "source": sol_src, "offset_sec": sol_off} if sol_p is not None else None,
            "eth": {"price": eth_p, "source": eth_src, "offset_sec": eth_off} if eth_p is not None else None,
            "eth_div_sol": gap,
        }
    return out


def summarize(doc):
    n = len(doc["candidates"])
    with_tl = 0
    full_tl = 0
    any_price = 0
    for c in doc["candidates"]:
        tl = c.get("prices_timeline")
        if not tl: continue
        with_tl += 1
        pts = tl.get("points") or {}
        good = sum(1 for v in pts.values() if v and v.get("sol") and v.get("eth"))
        if good == 4: full_tl += 1
        if any(v and (v.get("sol") or v.get("eth")) for v in pts.values()):
            any_price += 1
    return n, with_tl, any_price, full_tl


def main():
    prices = PriceLookup()
    print(f"Price caches: sol_exact={len(prices.sol_exact)}, eth_block={len(prices.eth_block)}, eth_1m={len(prices.eth_1m)}")
    for fp in FILES:
        doc = json.load(open(fp, encoding="utf-8"))
        for c in doc["candidates"]:
            tl = enrich_one(c, prices)
            if tl:
                c["prices_timeline"] = tl
        n, with_tl, any_p, full_tl = summarize(doc)
        doc.setdefault("meta", {})["price_timeline_enriched"] = {
            "total": n, "has_bridge_info": with_tl,
            "any_price_hit": any_p, "all_4_points_both_chains": full_tl,
        }
        json.dump(doc, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"{fp.name}: total={n} bridge_info={with_tl} any_price={any_p} full(4pts×2chains)={full_tl}")


if __name__ == "__main__":
    main()
