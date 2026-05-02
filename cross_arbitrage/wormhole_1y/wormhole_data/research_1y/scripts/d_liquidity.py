#!/usr/bin/env python3
"""
主题 D · 流动性与价差 (Liquidity & Price Gap) — 1y 版.

Minimal 模式没有 4 点 prices_timeline, 所以这里用 bridge token 在两条链上
同一时间戳 (sol_ts, eth_ts) 的价格直接算价差 (2-stage event study):

  stage="entry" 取套利入场时刻 (SOL→ETH: sol_ts, ETH→SOL: eth_ts)
  stage="exit"  取套利出场时刻 (SOL→ETH: eth_ts, ETH→SOL: sol_ts)

Signed gap convention (正值=获利方向):
  SOL→ETH: signed = (eth_price/sol_price − 1) × 100 %
  ETH→SOL: signed = (sol_price/eth_price − 1) × 100 %

Fig 4: 1 x 3
  (a) D15 liquidity_category × direction 下的 ROI 箱线
  (b) D16 entry/exit gap 分布 (按方向) + 均值的 event-study
  (c) D17 entry gap → realised ROI 散点 + binned median + Pearson/Spearman

输出:
  figs/04_liquidity.png
  tables/d15_liquidity_tier.csv
  tables/d16_price_gap_eventstudy.csv
  tables/d17_gap_vs_roi.csv
"""
from __future__ import annotations
import gc
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from compute_pnl_1y import PriceLookup

from common_loader import load_df, load_raw, clear_raw_cache, FIGS, TABLES, PALETTE, MPL_STYLE

mpl.rcParams.update(MPL_STYLE)

STAGES = ["entry", "exit"]


def build_gap_frame():
    """对每个 complete 的 candidate, 分别在 entry_ts 和 exit_ts 查价,
    计算 signed_gap_pct (long form)."""
    raw = load_raw()
    print("  loading price caches ...", flush=True)
    pl = PriceLookup()

    rows = []
    missing = 0
    for r in raw["candidates"]:
        pnl = r.get("pnl") or {}
        if not pnl.get("complete"):
            continue
        dirn = r["direction"]
        br = r.get("bridge") or {}
        spl = br.get("spl_mint")
        erc = (br.get("erc20_contract") or "").lower() or None
        if not spl or not erc:
            continue
        sol_key = f"solana:{spl}"
        eth_key = f"ethereum:{erc}"
        sol_ts = r.get("sol_ts")
        eth_ts = r.get("eth_ts")
        if sol_ts is None or eth_ts is None:
            continue

        if dirn == "SOL→ETH":
            entry_ts, exit_ts = sol_ts, eth_ts
        elif dirn == "ETH→SOL":
            entry_ts, exit_ts = eth_ts, sol_ts
        else:
            continue

        for stage, ts in [("entry", entry_ts), ("exit", exit_ts)]:
            sp, _, _ = pl.get(sol_key, ts)
            ep, _, _ = pl.get(eth_key, ts)
            if sp is None or ep is None or sp <= 0 or ep <= 0:
                missing += 1
                continue
            if dirn == "SOL→ETH":
                signed = (ep / sp - 1.0) * 100.0
            else:
                signed = (sp / ep - 1.0) * 100.0
            rows.append({
                "id":             r["id"],
                "direction":      dirn,
                "stage":          stage,
                "sol_price":      sp,
                "eth_price":      ep,
                "signed_gap_pct": signed,
                "roi_pct":        pnl.get("roi_pct"),
                "entry_cost_usd": pnl.get("entry_cost_usd"),
            })
    print(f"  gap_frame rows={len(rows)}, missing price points={missing}", flush=True)
    df_gap = pd.DataFrame(rows)
    del rows, pl
    gc.collect()
    return df_gap


def main():
    df = load_df()
    df_ok = df[df.pnl_complete == True].copy()

    # ---------- Table D15 ----------
    liq_tbl = (df_ok.groupby(["liquidity_category", "direction"], observed=True)
                    .agg(n=("id", "count"),
                         roi_median=("roi_pct", "median"),
                         roi_mean=("roi_pct", "mean"),
                         net_pnl_median=("net_pnl_usd", "median"),
                         net_pnl_sum=("net_pnl_usd", "sum"),
                         size_median=("entry_cost_usd", "median"),
                         pct_net_positive=("net_pnl_usd", lambda s: (s > 0).mean()),
                         )
                    .round(3))
    liq_tbl.to_csv(TABLES / "d15_liquidity_tier.csv")

    # ---------- gap frame ----------
    gap_df = build_gap_frame()

    # ---------- Table D16 ----------
    if len(gap_df) > 0:
        es = (gap_df.groupby(["direction", "stage"], observed=True)
                    .agg(n=("id", "count"),
                         signed_gap_median=("signed_gap_pct", "median"),
                         signed_gap_mean=("signed_gap_pct", "mean"),
                         signed_gap_p25=("signed_gap_pct", lambda s: s.quantile(.25)),
                         signed_gap_p75=("signed_gap_pct", lambda s: s.quantile(.75)),
                         )
                    .round(4))
        es = es.reindex(
            pd.MultiIndex.from_product([["SOL→ETH", "ETH→SOL"], STAGES],
                                       names=["direction", "stage"])
        ).dropna(how="all")
        es.to_csv(TABLES / "d16_price_gap_eventstudy.csv")
    else:
        es = pd.DataFrame()

    # ---------- Table D17 ----------
    if len(gap_df) > 0:
        entry_gaps = gap_df[gap_df.stage == "entry"].set_index("id")["signed_gap_pct"]
        df_ok["entry_gap_pct"] = df_ok["id"].map(entry_gaps)
        r = df_ok.dropna(subset=["entry_gap_pct", "roi_pct"])
        if len(r) > 10:
            pearson = stats.pearsonr(r["entry_gap_pct"], r["roi_pct"])
            spearman = stats.spearmanr(r["entry_gap_pct"], r["roi_pct"])
            r = r.copy()
            r["gap_decile"] = pd.qcut(r["entry_gap_pct"].rank(method="first"),
                                      q=10, labels=[f"D{i+1}" for i in range(10)])
            gap_dec = (r.groupby("gap_decile", observed=True)
                        .agg(n=("id", "count"),
                             gap_median=("entry_gap_pct", "median"),
                             roi_median=("roi_pct", "median"),
                             roi_mean=("roi_pct", "mean"),
                             pct_net_positive=("roi_pct", lambda s: (s > 0).mean()),
                             )
                        .round(3))
            gap_dec.to_csv(TABLES / "d17_gap_vs_roi.csv")
        else:
            pearson = spearman = None
            gap_dec = pd.DataFrame()
    else:
        r = pd.DataFrame()
        pearson = spearman = None
        gap_dec = pd.DataFrame()

    # ====== Figure 4 ======
    fig = plt.figure(figsize=(14, 5.0))
    gs = fig.add_gridspec(1, 3, wspace=0.32)

    # --- (a) liquidity tier × direction ROI ---
    ax_a = fig.add_subplot(gs[0, 0])
    tier_order = ["dead_pool_exploit", "thin_pool_arb", "healthy_market_arb"]
    tier_short = ["dead\n(<$1k)", "thin\n($1k–$100k)", "healthy\n(≥$100k)"]
    positions = np.arange(len(tier_order))
    width = 0.35
    for i, (d, color) in enumerate(zip(["SOL→ETH", "ETH→SOL"],
                                       [PALETTE["sol_to_eth"], PALETTE["eth_to_sol"]])):
        data, ns = [], []
        for t in tier_order:
            sub = df_ok[(df_ok.liquidity_category == t) & (df_ok.direction == d)]["roi_pct"].dropna().values
            sub = np.clip(sub, -50, 50)
            data.append(sub); ns.append(len(sub))
        offsets = positions + (i - 0.5) * width
        valid = [j for j, x in enumerate(data) if len(x) > 0]
        if not valid:
            continue
        bp = ax_a.boxplot([data[j] for j in valid],
                          positions=[offsets[j] for j in valid],
                          widths=width * 0.85,
                          patch_artist=True, showfliers=False,
                          medianprops=dict(color="black", lw=1.1))
        for box in bp["boxes"]:
            box.set_facecolor(color); box.set_alpha(0.75)
        for off, n in zip(offsets, ns):
            ax_a.text(off, 45, f"n={n}", ha="center", fontsize=6.5, color=color)
    ax_a.set_xticks(positions)
    ax_a.set_xticklabels(tier_short, fontsize=8)
    ax_a.axhline(0, color="grey", ls="--", lw=0.5)
    ax_a.set_ylim(-20, 50)
    ax_a.set_ylabel("ROI % (clipped ±50)")
    ax_a.set_title("(a) ROI by liquidity tier × direction")
    from matplotlib.patches import Patch
    ax_a.legend(handles=[Patch(color=PALETTE["sol_to_eth"], alpha=0.75, label="SOL→ETH"),
                         Patch(color=PALETTE["eth_to_sol"], alpha=0.75, label="ETH→SOL")],
                frameon=False, loc="upper right", fontsize=7.5)

    # --- (b) 2-point event-study ---
    ax_b = fig.add_subplot(gs[0, 1])
    if len(gap_df) > 0:
        stage_x = np.arange(len(STAGES))
        for d, color in zip(["SOL→ETH", "ETH→SOL"],
                            [PALETTE["sol_to_eth"], PALETTE["eth_to_sol"]]):
            meds, p25s, p75s = [], [], []
            for st in STAGES:
                sub = gap_df[(gap_df.direction == d) & (gap_df.stage == st)]["signed_gap_pct"]
                if len(sub) == 0:
                    meds.append(np.nan); p25s.append(np.nan); p75s.append(np.nan); continue
                sub_c = sub.clip(-20, 20)
                meds.append(sub.median())
                p25s.append(sub_c.quantile(.25))
                p75s.append(sub_c.quantile(.75))
            ax_b.plot(stage_x, meds, color=color, lw=1.8, marker="o",
                      label=f"{d} (median)")
            ax_b.fill_between(stage_x, p25s, p75s, color=color, alpha=0.18)
        ax_b.axhline(0, color="grey", ls="--", lw=0.5)
        ax_b.set_xticks(stage_x)
        ax_b.set_xticklabels(["entry", "exit"], fontsize=9)
        ax_b.set_ylabel("signed price gap (%)")
        ax_b.set_title("(b) Gap at entry vs exit (bridge token, 2-stage)")
        ax_b.legend(frameon=False, loc="upper right", fontsize=7.5)
        ax_b.annotate("profitable", xy=(-0.15, 0.95), xycoords="axes fraction",
                      fontsize=7, color=PALETTE["positive"])
        ax_b.annotate("loss", xy=(-0.15, 0.05), xycoords="axes fraction",
                      fontsize=7, color=PALETTE["negative"])
    else:
        ax_b.text(0.5, 0.5, "(no gap data)", ha="center", va="center",
                  transform=ax_b.transAxes)

    # --- (c) entry gap → ROI scatter ---
    ax_c = fig.add_subplot(gs[0, 2])
    if len(r) > 10 and pearson is not None:
        xc = r["entry_gap_pct"].values
        yc = r["roi_pct"].values
        xc_plot = np.clip(xc, -15, 15)
        yc_plot = np.clip(yc, -20, 30)
        ax_c.scatter(xc_plot, yc_plot, s=4, alpha=0.35, color=PALETTE["neutral"],
                     edgecolors="none")
        bins = np.linspace(-10, 10, 21)
        bin_mid, bin_med = [], []
        for lo, hi in zip(bins[:-1], bins[1:]):
            m = (xc >= lo) & (xc < hi)
            if m.sum() >= 5:
                bin_mid.append((lo + hi) / 2)
                bin_med.append(np.median(yc[m]))
        if bin_mid:
            ax_c.plot(bin_mid, bin_med, color=PALETTE["negative"], lw=1.8,
                      marker="o", markersize=5, label="binned median ROI")
        lims = np.array([-10, 10])
        ax_c.plot(lims, lims, color=PALETTE["positive"], lw=0.8, ls=":",
                  label="1:1 (full capture)")
        ax_c.axhline(0, color="grey", lw=0.4, ls="--")
        ax_c.axvline(0, color="grey", lw=0.4, ls="--")
        ax_c.set_xlim(-15, 15); ax_c.set_ylim(-20, 30)
        ax_c.set_xlabel("entry signed gap %")
        ax_c.set_ylabel("realised ROI % (clipped)")
        ax_c.set_title(f"(c) Entry gap → ROI  "
                       f"ρ_Spearman={spearman.correlation:.3f}  "
                       f"r_Pearson={pearson.statistic:.3f}  n={len(r)}")
        ax_c.legend(frameon=False, loc="upper left", fontsize=7)
    else:
        ax_c.text(0.5, 0.5, "(insufficient entry-gap coverage)",
                  ha="center", va="center", transform=ax_c.transAxes)

    fig.suptitle("Figure 4 — Liquidity Tier & Bridge-Token Price Gap",
                 fontsize=12, y=1.02, fontweight="bold")
    out = FIGS / "04_liquidity.png"
    fig.savefig(out)
    plt.close(fig)

    # ---------- Console summary ----------
    print("[D15] liquidity_category × direction:")
    print(liq_tbl[["n", "roi_median", "net_pnl_median", "net_pnl_sum", "pct_net_positive"]].to_string())
    print()
    if len(es) > 0:
        print("[D16] entry/exit gap by direction:")
        print(es[["n", "signed_gap_median", "signed_gap_mean"]].to_string())
        print()
    if pearson is not None:
        print(f"[D17] entry_gap ~ ROI:  Pearson r={pearson.statistic:.3f} "
              f"(p={pearson.pvalue:.2e})  "
              f"Spearman ρ={spearman.correlation:.3f} "
              f"(p={spearman.pvalue:.2e})  n={len(r)}")
        if len(gap_dec):
            print("      by entry-gap decile:")
            print(gap_dec.to_string())
    print(f"saved: {out}")
    clear_raw_cache()


if __name__ == "__main__":
    main()
