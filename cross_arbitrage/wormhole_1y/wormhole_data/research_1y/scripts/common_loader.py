#!/usr/bin/env python3
"""
1y 版共享数据加载。主文件: arbitrage_candidates_10pct.json (reliable only primary)。
相比 30d 版: minimal mode 没有 prices_timeline 4 点, 也没有 volume_1h_at_trade。
"""

import gzip
import json
import os
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = Path(__file__).resolve().parents[1]
RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
MATCHED_DIR = f"matched_{RUN_TAG}" if RUN_TAG else "matched"
ARB_JSON = ROOT / "use" / "portal_full" / "recent_1y" / MATCHED_DIR / "arbitrage" / "arbitrage_candidates_10pct.json"
FIGS = RESEARCH / (f"figs_{RUN_TAG}" if RUN_TAG else "figs")
TABLES = RESEARCH / (f"tables_{RUN_TAG}" if RUN_TAG else "tables")
FIGS.mkdir(parents=True, exist_ok=True)
TABLES.mkdir(parents=True, exist_ok=True)

PALETTE = {
    "sol_to_eth": "#2E86AB",
    "eth_to_sol": "#E07A5F",
    "neutral":    "#6D6875",
    "accent":     "#F4A261",
    "positive":   "#2A9D8F",
    "negative":   "#C44536",
    "dead":       "#8D99AE",
    "thin":       "#F4A261",
    "healthy":    "#2A9D8F",
}

MPL_STYLE = {
    "font.family":       "serif",
    "font.size":         9,
    "axes.titlesize":    10,
    "axes.labelsize":    9,
    "xtick.labelsize":   8,
    "ytick.labelsize":   8,
    "legend.fontsize":   8,
    "figure.dpi":        110,
    "savefig.dpi":       300,
    "savefig.bbox":      "tight",
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.grid":         True,
    "grid.alpha":        0.25,
    "grid.linestyle":    "--",
    "grid.linewidth":    0.5,
}


_RAW_CACHE = {}


def load_raw(path=None):
    """Module-level singleton cache: arbitrage_candidates JSON 只 parse 一次,
    后续 build_gap_frame / build_feature_frame 共享同一份 dict,
    避免 292MB 文件被反复 parse (每份约 1.5 GB Python dict)."""
    path = Path(path or ARB_JSON)
    if not path.exists() and path.suffix != ".gz":
        gz_path = Path(str(path) + ".gz")
        if gz_path.exists():
            path = gz_path
    key = str(path)
    cached = _RAW_CACHE.get(key)
    if cached is not None:
        return cached
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        _RAW_CACHE[key] = json.load(f)
    return _RAW_CACHE[key]


def clear_raw_cache():
    _RAW_CACHE.clear()


def load_df(path=None):
    raw = load_raw(path)
    rows = []
    for r in raw["candidates"]:
        pnl  = r.get("pnl") or {}
        br   = r.get("bridge") or {}
        arb  = r.get("arbitrageur") or {}
        liq  = r.get("current_liquidity") or {}
        flags = set(r.get("flags") or [])
        es = r.get("entry_swaps_greedy") or []
        xs = r.get("exit_swaps_greedy") or []

        # accept multiple shapes of liquidity (dict with sol_usd or nested)
        sol_liq = liq.get("sol_usd")
        if sol_liq is None and isinstance(liq.get("sol"), dict):
            sol_liq = liq["sol"].get("liq_usd")
        eth_liq = liq.get("eth_usd")
        if eth_liq is None and isinstance(liq.get("eth"), dict):
            eth_liq = liq["eth"].get("liq_usd")

        rows.append({
            "id":            r["id"],
            "direction":     r["direction"],
            "sol_sig":       r.get("sol_sig"),
            "eth_hash":      r.get("eth_hash"),
            "sol_ts":        r.get("sol_ts"),
            "eth_ts":        r.get("eth_ts"),
            "sol_slot":      r.get("sol_slot"),
            "eth_block":     r.get("eth_block"),
            "time_diff_sec": r.get("time_diff_sec"),

            "arb_sol":       arb.get("sol"),
            "arb_eth":       (arb.get("eth") or "").lower() or None,

            "token_sol_sym": br.get("sol_symbol") or liq.get("sol_symbol"),
            "token_eth_sym": br.get("eth_symbol") or liq.get("eth_symbol"),
            "token_key":     (br.get("sol_symbol") or br.get("eth_symbol")
                              or liq.get("sol_symbol") or liq.get("eth_symbol") or "UNK").upper(),
            "sol_amount":    br.get("sol_amount"),
            "eth_amount":    br.get("eth_amount"),
            "spl_mint":      br.get("spl_mint"),
            "erc20":         br.get("erc20_contract"),

            "classification":     r.get("classification"),
            "subtype":            r.get("subtype_greedy") or r.get("subtype"),
            "pnl_reliability":    r.get("pnl_reliability_greedy") or r.get("pnl_reliability"),
            "liquidity_category": r.get("liquidity_category"),

            "entry_coverage": r.get("entry_coverage_greedy") or r.get("entry_coverage"),
            "exit_coverage":  r.get("exit_coverage_greedy")  or r.get("exit_coverage"),

            "atomic_entry_same_block": "atomic_entry_same_block" in flags,
            "atomic_exit_same_block":  "atomic_exit_same_block"  in flags,
            "fully_atomic":            "fully_atomic"            in flags,
            "quick_round_trip_60s":    "quick_round_trip_60s"    in flags,
            "quick_round_trip_300s":   "quick_round_trip_300s"   in flags,

            "entry_cost_usd":  pnl.get("entry_cost_usd"),
            "exit_value_usd":  pnl.get("exit_value_usd"),
            "sol_fee_usd":     pnl.get("sol_fee_usd"),
            "eth_fee_usd":     pnl.get("eth_fee_usd"),
            "total_fee_usd":   pnl.get("total_fee_usd"),
            "gross_pnl_usd":   pnl.get("gross_pnl_usd"),
            "net_pnl_usd":     pnl.get("net_pnl_usd"),
            "roi_pct":         pnl.get("roi_pct"),
            "pnl_complete":    pnl.get("complete"),

            "sol_liq_usd":     sol_liq,
            "eth_liq_usd":     eth_liq,
            "sol_v24h_usd":    liq.get("sol_v24h_usd"),
            "eth_v24h_usd":    liq.get("eth_v24h_usd"),
            "sol_mc":          liq.get("sol_mc"),
            "eth_mc":          liq.get("eth_mc"),

            "n_entry_swaps": len(es),
            "n_exit_swaps":  len(xs),
            "entry_delta_bridge_sec": (es[0].get("delta_bridge_sec") if es else None),
            "exit_delta_bridge_sec":  (xs[-1].get("delta_bridge_sec") if xs else None),
        })

    df = pd.DataFrame(rows)
    # derive time_diff_sec if missing
    mask = df["time_diff_sec"].isna() & df["sol_ts"].notna() & df["eth_ts"].notna()
    df.loc[mask, "time_diff_sec"] = (df.loc[mask, "sol_ts"] - df.loc[mask, "eth_ts"]).abs()
    df["sol_dt"] = pd.to_datetime(df["sol_ts"], unit="s", utc=True)
    df["eth_dt"] = pd.to_datetime(df["eth_ts"], unit="s", utc=True)
    df["hour_utc"] = df["sol_dt"].dt.hour
    df["date_utc"] = df["sol_dt"].dt.date
    df["weekday"]  = df["sol_dt"].dt.day_name()
    df["is_reliable"] = df["pnl_reliability"].eq("reliable")

    # --- dust-entry artifact filter ---------------------------------------
    # Drop rows where entry_cost_usd is below $1, which is below the minimum
    # plausible on-chain trade cost (Ethereum L1 swap gas alone ≈ $1–$5).
    # These are pipeline artifacts: token-amount rounding in bridge wrapping
    # collapses the denominator of ROI, producing nonsense values up to 10^11%.
    # We intentionally do NOT filter on fee > entry, which is a legitimate
    # economic loss rather than a measurement error.
    _n0 = len(df)
    dust_mask = df["entry_cost_usd"].notna() & (df["entry_cost_usd"] < 1.0)
    dust_dropped = int(dust_mask.sum())
    df = df.loc[~dust_mask].reset_index(drop=True)
    df.attrs["dust_dropped"] = dust_dropped
    df.attrs["n_before_dust_filter"] = _n0
    return df


if __name__ == "__main__":
    df = load_df()
    print(f"loaded {len(df)} records, {df['is_reliable'].sum()} reliable")
    print(df[["direction","subtype","liquidity_category","roi_pct","net_pnl_usd"]].describe(include="all"))
