#!/usr/bin/env python3
"""
主题 B · 时间与原子性 (Timing).

Fig 2: 1 x 3 grid
  (a) B6 跨链延迟: time_diff_sec CDF, 按方向
  (b) B8 Swap-bridge 时间差: entry & exit delta_bridge_sec log-hist
  (c) B9 快速往返: 速度分层 (<60s / 60–300s / 300s–1h / >1h) ROI 箱线

原子性矩阵仍写入 tables/b07_atomicity_matrix.csv 供附录使用,图中不再展示.

输出:
  figs/02_timing.png
  tables/b06_latency_quantiles.csv
  tables/b07_atomicity_matrix.csv
  tables/b08_delta_bridge.csv
  tables/b09_speed_tier_roi.csv
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl

from common_loader import load_df, load_raw, FIGS, TABLES, PALETTE, MPL_STYLE

mpl.rcParams.update(MPL_STYLE)


def _swap_deltas():
    """返回 (entry_deltas, exit_deltas) 两个 1d array (秒)."""
    raw = load_raw()
    e, x = [], []
    for r in raw["candidates"]:
        for s in (r.get("entry_swaps_greedy") or []):
            v = s.get("delta_bridge_sec")
            if v is not None:
                e.append(v)
        for s in (r.get("exit_swaps_greedy") or []):
            v = s.get("delta_bridge_sec")
            if v is not None:
                x.append(v)
    return np.asarray(e), np.asarray(x)


def main():
    df = load_df()

    # ---------- Table B6: latency quantiles ----------
    lat = df.groupby("direction")["time_diff_sec"].describe(
        percentiles=[.1, .25, .5, .75, .9, .95, .99]
    ).round(1)
    lat.to_csv(TABLES / "b06_latency_quantiles.csv")

    # ---------- Table B7: atomicity matrix ----------
    atom_mat = (
        df.groupby(["atomic_entry_same_block", "atomic_exit_same_block"])
          .agg(n=("id", "count"),
               roi_med=("roi_pct", "median"),
               size_med=("entry_cost_usd", "median"))
          .round(3)
    )
    atom_mat.to_csv(TABLES / "b07_atomicity_matrix.csv")

    # ---------- Table B8: delta_bridge summary ----------
    e_d, x_d = _swap_deltas()
    b8 = pd.DataFrame({
        "entry_delta_sec": pd.Series(e_d).describe(percentiles=[.1,.25,.5,.75,.9,.95,.99]),
        "exit_delta_sec":  pd.Series(x_d).describe(percentiles=[.1,.25,.5,.75,.9,.95,.99]),
    }).round(1)
    b8.to_csv(TABLES / "b08_delta_bridge.csv")

    # ---------- Table B9: speed-tier × ROI ----------
    td = df["time_diff_sec"].fillna(-1)
    tier = pd.cut(td,
        bins=[-2, 60, 300, 3600, 86400, np.inf],
        labels=["<60s", "60–300s", "300s–1h", "1h–24h", ">24h"])
    df["speed_tier"] = tier.astype("object").where(td >= 0, other="missing")
    tier_stats = df.groupby("speed_tier", observed=True).agg(
        n=("id", "count"),
        roi_median=("roi_pct", "median"),
        roi_mean=("roi_pct", "mean"),
        net_pnl_median=("net_pnl_usd", "median"),
        net_pnl_sum=("net_pnl_usd", "sum"),
        size_median=("entry_cost_usd", "median"),
        fully_atomic_rate=("fully_atomic", "mean"),
    ).round(3)
    tier_stats.to_csv(TABLES / "b09_speed_tier_roi.csv")

    # ====== Figure 2 ======
    fig = plt.figure(figsize=(16, 4.6))
    gs = fig.add_gridspec(1, 3, wspace=0.30)

    # --- (a) CDF of time_diff_sec by direction ---
    ax_a = fig.add_subplot(gs[0, 0])
    for d, color in zip(["SOL→ETH", "ETH→SOL"],
                        [PALETTE["sol_to_eth"], PALETTE["eth_to_sol"]]):
        dat = np.sort(df.loc[df.direction == d, "time_diff_sec"].dropna().values)
        dat = dat[dat >= 0]
        if len(dat) == 0:
            continue
        # CDF on log-x, clip lower to 1s for log
        dat_clip = np.clip(dat, 1, None)
        y = np.arange(1, len(dat_clip) + 1) / len(dat_clip)
        ax_a.plot(dat_clip, y, color=color, lw=1.8, label=f"{d} (n={len(dat)})")
        med = np.median(dat)
        ax_a.axvline(med, color=color, ls="--", lw=0.8, alpha=0.6)
        ax_a.text(med, 0.02, f"med={med:.0f}s",
                  color=color, fontsize=7.5, rotation=0, ha="left", va="bottom")
    ax_a.set_xscale("log")
    ax_a.set_xlim(1, 1e6)
    ax_a.set_xlabel("bridge latency $\\Delta t = |t_{eth} - t_{sol}|$  (s, log)")
    ax_a.set_ylabel("cumulative fraction")
    ax_a.set_title("(a) Bridge latency CDF by direction")
    ax_a.legend(frameon=False, loc="lower right")
    # reference vertical guides
    for ref, lbl in [(60, "1 min"), (300, "5 min"), (3600, "1 hr"), (86400, "1 d")]:
        ax_a.axvline(ref, color="grey", ls=":", lw=0.5, alpha=0.5)
        ax_a.text(ref, 0.98, lbl, ha="right", va="top", fontsize=6.5,
                  color="grey", rotation=90)

    # (atomicity 2x2 matrix still written to tables/b07_atomicity_matrix.csv
    #  but no longer shown in the figure)
    ee = df["atomic_entry_same_block"].astype(int)
    xx = df["atomic_exit_same_block"].astype(int)
    mat = np.zeros((2, 2), dtype=int)  # rows = exit, cols = entry
    for i in range(2):
        for j in range(2):
            mat[i, j] = int(((ee == j) & (xx == i)).sum())
    n = mat.sum()
    n_full = int(df["fully_atomic"].sum())

    # --- (b) delta_bridge_sec distributions ---
    ax_c = fig.add_subplot(gs[0, 1])
    # clip at 24h for visualization; count how many trimmed
    cap = 86400
    e_plot = e_d[(e_d >= 0) & (e_d <= cap)]
    x_plot = x_d[(x_d >= 0) & (x_d <= cap)]
    bins = np.logspace(0, np.log10(cap), 50)
    ax_c.hist(np.clip(e_plot, 1, None), bins=bins, alpha=0.55,
              color=PALETTE["sol_to_eth"], label=f"entry swap (n={len(e_d)})",
              edgecolor="black", linewidth=0.3)
    ax_c.hist(np.clip(x_plot, 1, None), bins=bins, alpha=0.55,
              color=PALETTE["eth_to_sol"], label=f"exit swap (n={len(x_d)})",
              edgecolor="black", linewidth=0.3)
    ax_c.set_xscale("log")
    ax_c.set_xlabel("$|t_{swap} - t_{bridge}|$  (s, log, clipped at 24 h)")
    ax_c.set_ylabel("swap count")
    ax_c.set_title(f"(b) Swap–bridge time gap  "
                   f"entry med={np.median(e_d):.0f}s, exit med={np.median(x_d):.0f}s")
    ax_c.legend(frameon=False, loc="upper right")
    # reference lines
    for ref, lbl in [(60, "1m"), (3600, "1h"), (86400, "1d")]:
        ax_c.axvline(ref, color="grey", ls=":", lw=0.5, alpha=0.6)

    # --- (c) Speed-tier ROI box ---
    ax_d = fig.add_subplot(gs[0, 2])
    tier_order = ["<60s", "60–300s", "300s–1h", "1h–24h", ">24h"]
    tier_rois = []
    tier_labels = []
    tier_ns = []
    for t in tier_order:
        sub = df[(df.speed_tier == t) & df.roi_pct.notna()]["roi_pct"].values
        if len(sub) > 0:
            tier_rois.append(sub)
            tier_labels.append(t)
            tier_ns.append(len(sub))
    # clip ROI to ±50% for visibility (state clipping in title)
    clipped = [np.clip(r, -50, 50) for r in tier_rois]
    bp = ax_d.boxplot(clipped, showfliers=False, patch_artist=True,
                      widths=0.55, medianprops=dict(color="black", lw=1.2))
    ax_d.set_xticks(range(1, len(tier_labels) + 1))
    ax_d.set_xticklabels(tier_labels)
    tier_colors = [PALETTE["positive"], PALETTE["sol_to_eth"], PALETTE["accent"],
                   PALETTE["eth_to_sol"], PALETTE["negative"]][:len(tier_labels)]
    for box, c in zip(bp["boxes"], tier_colors):
        box.set_facecolor(c); box.set_alpha(0.7)
    # overlay n and median labels
    for i, (t, r) in enumerate(zip(tier_labels, tier_rois)):
        med = np.median(r)
        ax_d.text(i + 1, 52, f"n={len(r)}\nmed={med:.2f}%",
                  ha="center", fontsize=7)
    ax_d.axhline(0, color="grey", lw=0.5, ls="--")
    ax_d.set_ylim(-20, 65)
    ax_d.set_ylabel("ROI % (clipped to ±50 %)")
    ax_d.set_xlabel("bridge latency tier")
    ax_d.set_title("(c) Speed tier → ROI")

    fig.suptitle("Figure 2 — Timing of Wormhole Portal Arbitrage",
                 fontsize=12, y=1.02, fontweight="bold")
    out = FIGS / "02_timing.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)

    # ---------- console summary ----------
    print("[B6] bridge latency by direction:")
    print(lat)
    print()
    print("[B7] atomicity matrix (rows=exit, cols=entry):")
    print(mat)
    print(f"     fully_atomic={n_full}/{n} = {n_full/n:.2%}")
    print()
    print("[B8] swap-bridge delta: entry med=%.0fs, exit med=%.0fs"
          % (np.median(e_d), np.median(x_d)))
    print()
    print("[B9] speed tier ROI medians:")
    print(tier_stats[["n","roi_median","net_pnl_median","size_median"]])
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
