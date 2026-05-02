#!/usr/bin/env python3
"""
主题 C · 经济学 (Economics).

Fig 3: 2 x 3 grid
  (a) C10 ROI 分布: hist (clip ±20 %) + CDF 插图
  (b) C10 ROI CDF by direction (log 正负分离)
  (c) C11 本金规模 log-hist + size 阶层标注 (散户 / 中户 / 巨鲸)
  (d) C12 手续费占比 fee/|gross_pnl| 分布 + break-even 高亮
  (e) C13 Gross vs Net 散点 (log size axis) + 临界规模
  (f) C14 net_pnl ~ entry_cost 散点 + OLS 回归

输出:
  figs/03_economics.png
  tables/c10_roi_quantiles.csv
  tables/c11_size_tiers.csv
  tables/c12_fee_ratio.csv
  tables/c13_fee_by_size_decile.csv
  tables/c14_regression.csv
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl
from scipy import stats

from common_loader import load_df, FIGS, TABLES, PALETTE, MPL_STYLE

mpl.rcParams.update(MPL_STYLE)


def size_tier(x):
    if pd.isna(x):        return np.nan
    if x < 100:           return "retail (<$100)"
    if x < 1_000:         return "small ($100–$1k)"
    if x < 10_000:        return "medium ($1k–$10k)"
    if x < 100_000:       return "large ($10k–$100k)"
    return "whale (>$100k)"


SIZE_TIER_ORDER = [
    "retail (<$100)", "small ($100–$1k)", "medium ($1k–$10k)",
    "large ($10k–$100k)", "whale (>$100k)",
]


def main():
    df = load_df().copy()
    # restrict to reliable + finite economics for the regression/fee analysis
    df = df[df.pnl_complete == True].copy()
    df["size_tier"] = df["entry_cost_usd"].apply(size_tier)

    # ---------- Table C10: ROI quantiles ----------
    roi_q = df["roi_pct"].describe(percentiles=[.01, .05, .1, .25, .5, .75, .9, .95, .99]).round(3)
    roi_q.to_csv(TABLES / "c10_roi_quantiles.csv")

    # ---------- Table C11: size tier stats ----------
    size_stats = (df.groupby("size_tier", observed=True)
                    .agg(n=("id", "count"),
                         roi_median=("roi_pct", "median"),
                         net_pnl_median=("net_pnl_usd", "median"),
                         net_pnl_sum=("net_pnl_usd", "sum"),
                         total_fee_median=("total_fee_usd", "median"),
                         fee_to_size_median=("total_fee_usd", lambda s: s.median()),
                         )
                    .reindex(SIZE_TIER_ORDER)
                    .round(3))
    size_stats.to_csv(TABLES / "c11_size_tiers.csv")

    # ---------- Table C12: fee / |gross| ratio ----------
    # avoid div-zero; only consider candidates where gross_pnl_usd != 0
    df["fee_over_abs_gross"] = np.where(
        df["gross_pnl_usd"].abs() > 0,
        df["total_fee_usd"] / df["gross_pnl_usd"].abs(),
        np.nan,
    )
    df["fee_over_size"] = np.where(
        df["entry_cost_usd"] > 0,
        df["total_fee_usd"] / df["entry_cost_usd"],
        np.nan,
    )
    c12 = pd.DataFrame({
        "fee_over_abs_gross": df["fee_over_abs_gross"].describe(
            percentiles=[.1,.25,.5,.75,.9,.95,.99]).round(3),
        "fee_over_size":      df["fee_over_size"].describe(
            percentiles=[.1,.25,.5,.75,.9,.95,.99]).round(4),
    })
    c12.to_csv(TABLES / "c12_fee_ratio.csv")

    # ---------- Table C13: fee / size by size decile ----------
    df["size_decile"] = pd.qcut(df["entry_cost_usd"].rank(method="first"),
                                 q=10, labels=[f"D{i+1}" for i in range(10)])
    dec = (df.groupby("size_decile", observed=True)
             .agg(n=("id", "count"),
                  size_median=("entry_cost_usd", "median"),
                  fee_median=("total_fee_usd", "median"),
                  gross_median=("gross_pnl_usd", "median"),
                  net_median=("net_pnl_usd", "median"),
                  fee_to_gross_median=("fee_over_abs_gross", "median"),
                  fee_to_size_median=("fee_over_size", "median"),
                  pct_net_positive=("net_pnl_usd", lambda s: (s > 0).mean()),
                  )
             .round(4))
    dec.to_csv(TABLES / "c13_fee_by_size_decile.csv")

    # ---------- Table C14: regression net_pnl ~ entry_cost ----------
    reg = df[df.entry_cost_usd.notna() & df.net_pnl_usd.notna()].copy()
    # drop extreme outliers (|ROI| > 200 % or capital < $1)
    reg = reg[(reg.entry_cost_usd >= 1) & (reg.roi_pct.abs() <= 200)]
    x = np.log10(reg["entry_cost_usd"].values)
    y = reg["net_pnl_usd"].values
    slope_log, intercept_log, r_log, p_log, se_log = stats.linregress(x, y)
    # also linear-linear for the simple interpretation
    slope_lin, intercept_lin, r_lin, p_lin, se_lin = stats.linregress(
        reg["entry_cost_usd"].values, y)
    reg_tbl = pd.DataFrame([
        {"model": "net_pnl ~ log10(size)", "slope": slope_log, "intercept": intercept_log,
         "r": r_log, "r2": r_log**2, "p": p_log, "n": len(reg)},
        {"model": "net_pnl ~ size",        "slope": slope_lin, "intercept": intercept_lin,
         "r": r_lin, "r2": r_lin**2, "p": p_lin, "n": len(reg)},
    ]).round(6)
    reg_tbl.to_csv(TABLES / "c14_regression.csv", index=False)

    # ====== Figure 3 ======
    fig = plt.figure(figsize=(14, 8.5))
    gs = fig.add_gridspec(2, 3, hspace=0.42, wspace=0.38)

    # --- (a) ROI histogram, clipped to ±20 % ---
    ax_a = fig.add_subplot(gs[0, 0])
    roi = df["roi_pct"].dropna().values
    roi_clip = np.clip(roi, -20, 20)
    ax_a.hist(roi_clip, bins=60, color=PALETTE["sol_to_eth"], alpha=0.75,
              edgecolor="black", linewidth=0.3)
    ax_a.axvline(0, color="grey", lw=0.7, ls="--")
    med = np.median(roi)
    ax_a.axvline(med, color=PALETTE["negative"], lw=1.2, ls="-")
    ax_a.text(med + 0.3, ax_a.get_ylim()[1] * 0.9,
              f"median={med:.2f} %", color=PALETTE["negative"], fontsize=8)
    n_pos = int((roi > 0).sum()); n_neg = int((roi < 0).sum())
    ax_a.set_xlim(-20, 20)
    ax_a.set_xlabel("ROI %  (clipped to ±20 %)")
    ax_a.set_ylabel("candidates")
    ax_a.set_title(f"(a) ROI distribution  "
                   f"net+={n_pos} ({n_pos/len(roi):.0%}), "
                   f"net−={n_neg} ({n_neg/len(roi):.0%})")
    # inset: CDF
    axi = ax_a.inset_axes([0.62, 0.58, 0.36, 0.38])
    s_roi = np.sort(roi)
    axi.plot(s_roi, np.arange(1, len(s_roi)+1) / len(s_roi),
             color=PALETTE["negative"], lw=1.2)
    axi.set_xlim(-10, 10); axi.axvline(0, color="grey", ls="--", lw=0.5)
    axi.set_xlabel("ROI %", fontsize=7); axi.set_ylabel("CDF", fontsize=7)
    axi.tick_params(labelsize=6); axi.grid(alpha=0.2)
    axi.set_title("CDF (−10..10 %)", fontsize=7)

    # --- (b) ROI CDF by direction ---
    ax_b = fig.add_subplot(gs[0, 1])
    for d, color in zip(["SOL→ETH", "ETH→SOL"],
                        [PALETTE["sol_to_eth"], PALETTE["eth_to_sol"]]):
        sub = df[df.direction == d]["roi_pct"].dropna().values
        if len(sub) == 0:
            continue
        s = np.sort(sub)
        y = np.arange(1, len(s)+1) / len(s)
        ax_b.plot(s, y, color=color, lw=1.6, label=f"{d}  n={len(sub)}  med={np.median(sub):.2f}%")
    ax_b.axvline(0, color="grey", ls="--", lw=0.6)
    ax_b.set_xlim(-15, 15)
    ax_b.set_xlabel("ROI %")
    ax_b.set_ylabel("CDF")
    ax_b.set_title("(b) ROI CDF by direction")
    ax_b.legend(frameon=False, loc="lower right", fontsize=7.5)

    # --- (c) entry_cost_usd log-hist with tier shading ---
    ax_c = fig.add_subplot(gs[0, 2])
    sizes = df["entry_cost_usd"].dropna()
    sizes = sizes[sizes > 0]
    bins = np.logspace(np.log10(sizes.min()), np.log10(sizes.max()), 55)
    ax_c.hist(sizes, bins=bins, color=PALETTE["neutral"], alpha=0.8,
              edgecolor="black", linewidth=0.3)
    ax_c.set_xscale("log")
    tier_edges = [100, 1_000, 10_000, 100_000]
    tier_lbls  = ["retail | small", "small | medium", "medium | large", "large | whale"]
    for e, lbl in zip(tier_edges, tier_lbls):
        ax_c.axvline(e, color=PALETTE["accent"], ls=":", lw=0.8)
    # annotate counts per tier
    counts = df["size_tier"].value_counts().reindex(SIZE_TIER_ORDER).fillna(0).astype(int)
    top = ax_c.get_ylim()[1]
    # after setting hist, get actual ylim
    ymax = ax_c.get_ylim()[1]
    xpos = [30, 300, 3000, 30000, 300000]
    for xp, t in zip(xpos, SIZE_TIER_ORDER):
        c = counts.get(t, 0)
        ax_c.text(xp, ymax * 0.92, f"{c}\n({c/len(sizes):.0%})",
                  ha="center", fontsize=7)
    ax_c.set_xlabel("entry_cost USD (log)")
    ax_c.set_ylabel("candidates")
    ax_c.set_title(f"(c) Capital tiers  "
                   f"retail:{counts.iloc[0]} / whale:{counts.iloc[-1]}")

    # --- (d) fee / |gross| ratio histogram, log-x, highlight >1 (fees eat everything) ---
    ax_d = fig.add_subplot(gs[1, 0])
    r = df["fee_over_abs_gross"].dropna()
    r_clip = r.clip(lower=1e-4, upper=100)
    bins = np.logspace(-4, 2, 55)
    ax_d.hist(r_clip, bins=bins, color=PALETTE["accent"], alpha=0.75,
              edgecolor="black", linewidth=0.3)
    ax_d.set_xscale("log")
    ax_d.axvline(1.0, color=PALETTE["negative"], lw=1.2, ls="-")
    frac_above_1 = (r > 1).mean()
    ax_d.text(1.2, ax_d.get_ylim()[1] * 0.9,
              f"fee>|gross|\n{frac_above_1:.0%}", color=PALETTE["negative"],
              fontsize=8, ha="left")
    ax_d.set_xlabel("total_fee_usd / |gross_pnl_usd|  (log)")
    ax_d.set_ylabel("candidates")
    ax_d.set_title(f"(d) Fee burden  median={r.median():.3f}  p75={r.quantile(.75):.2f}")

    # --- (e) Gross vs Net scatter, log-log (signed) with fee=size contour ---
    ax_e = fig.add_subplot(gs[1, 1])
    good = df[df.gross_pnl_usd.notna() & df.net_pnl_usd.notna() &
              (df.entry_cost_usd > 0)]
    col = np.log10(good["entry_cost_usd"].clip(lower=1))
    sc = ax_e.scatter(good["gross_pnl_usd"].clip(-50, 50),
                      good["net_pnl_usd"].clip(-50, 50),
                      c=col, cmap="viridis", s=5, alpha=0.55, edgecolors="none")
    # 45° line (zero-fee baseline)
    lims = [-50, 50]
    ax_e.plot(lims, lims, color=PALETTE["negative"], lw=0.8, ls="--", label="net=gross")
    ax_e.axhline(0, color="grey", lw=0.4, ls=":"); ax_e.axvline(0, color="grey", lw=0.4, ls=":")
    ax_e.set_xlim(*lims); ax_e.set_ylim(*lims)
    ax_e.set_xlabel("gross_pnl USD (clipped ±50)")
    ax_e.set_ylabel("net_pnl USD (clipped ±50)")
    ax_e.set_title("(e) Gross → Net  (colour = log size)")
    cbar = plt.colorbar(sc, ax=ax_e, shrink=0.85, pad=0.02)
    cbar.set_label("log10 size USD", fontsize=7); cbar.ax.tick_params(labelsize=6)
    ax_e.legend(frameon=False, loc="lower right", fontsize=7)

    # --- (f) net_pnl ~ entry_cost regression ---
    ax_f = fig.add_subplot(gs[1, 2])
    xp = reg["entry_cost_usd"].values
    yp = reg["net_pnl_usd"].values
    ax_f.scatter(xp, yp, s=5, alpha=0.5, color=PALETTE["sol_to_eth"],
                 edgecolors="none")
    ax_f.set_xscale("log")
    ax_f.set_xlim(1, xp.max()*1.5)
    y_clip = 1000
    ax_f.set_ylim(-y_clip, y_clip)
    # binned medians
    bins_x = np.logspace(0, np.log10(xp.max()), 16)
    bin_mid, bin_med = [], []
    for lo, hi in zip(bins_x[:-1], bins_x[1:]):
        m = (xp >= lo) & (xp < hi)
        if m.sum() >= 50:
            bin_mid.append(np.sqrt(lo*hi))
            bin_med.append(np.median(yp[m]))
    ax_f.plot(bin_mid, bin_med, color=PALETTE["negative"], lw=1.5,
              marker="o", markersize=4, label="binned median")
    ax_f.axhline(0, color="grey", ls="--", lw=0.5)
    ax_f.set_xlabel("entry_cost USD (log)")
    ax_f.set_ylabel("net_pnl USD (clipped ±1000)")
    ax_f.set_title(f"(f) Capital → Net PnL  "
                   f"log-OLS slope={slope_log:.2f} ($/decade), r²={r_log**2:.3f}")
    ax_f.legend(frameon=False, loc="upper left", fontsize=7)

    fig.suptitle("Figure 3 — Economics of Wormhole Portal Arbitrage (ROI, Size, Fees, PnL)",
                 fontsize=12, y=0.995, fontweight="bold")
    out = FIGS / "03_economics.png"
    fig.savefig(out)
    plt.close(fig)

    # ---------- Console summary ----------
    print(f"[C10] ROI: med={df.roi_pct.median():.3f}% mean={df.roi_pct.mean():.3f}% "
          f"p25={df.roi_pct.quantile(.25):.3f} p75={df.roi_pct.quantile(.75):.3f} "
          f"n_pos={(df.roi_pct>0).sum()} n_neg={(df.roi_pct<0).sum()}")
    print(f"[C11] size tiers:")
    print(size_stats[["n", "roi_median", "net_pnl_median", "net_pnl_sum"]].to_string())
    print(f"[C12] fee / |gross|: med={df.fee_over_abs_gross.median():.3f} "
          f"frac >1: {(df.fee_over_abs_gross>1).mean():.3f}")
    print(f"[C13] size decile fee/gross:")
    print(dec[["n", "size_median", "fee_to_gross_median",
               "net_median", "pct_net_positive"]].to_string())
    print(f"[C14] log-OLS net = {intercept_log:.2f} + {slope_log:.2f} · log10(size), "
          f"r²={r_log**2:.3f}, p={p_log:.2e}")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
