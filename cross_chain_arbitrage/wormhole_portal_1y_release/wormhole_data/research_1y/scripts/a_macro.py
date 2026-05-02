#!/usr/bin/env python3
"""
主题 A · 宏观描述 (Macro Overview).

Fig 1: 2 x 3 grid 组合图
  (a) A1 数据集总览: subtype + reliability 计数
  (b) A2 方向不对称性: direction × (规模 / ROI / 耗时) 箱线
  (c) A3 Token 集中度: top-10 token + HHI
  (d) A4 时间分布: hour × weekday 热力图
  (e) A5 桥接规模分布: entry_cost_usd log-hist (按 direction)
  (f)    补充: liquidity_category × direction 堆叠条

输出:
  figs/01_macro.png
  tables/a01_overview.csv
  tables/a02_direction_asymmetry.csv
  tables/a03_token_hhi.csv
  tables/a05_size_quantiles.csv
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl

from common_loader import load_df, FIGS, TABLES, PALETTE, MPL_STYLE

mpl.rcParams.update(MPL_STYLE)


def hhi(counts: pd.Series) -> float:
    """Herfindahl-Hirschman Index on a Series of counts (0-10000 scale)."""
    p = counts / counts.sum()
    return float((p ** 2).sum() * 10_000)


def main():
    df = load_df()
    n_total = len(df)
    n_rel = int(df["is_reliable"].sum())

    # ---------- Table A1: overview ----------
    overview = pd.DataFrame({
        "metric": [
            "total_candidates", "reliable", "reliable_pct",
            "date_min", "date_max", "span_days",
            "direction_SOL_to_ETH", "direction_ETH_to_SOL",
            "subtype_full_clean", "subtype_other",
            "liq_dead", "liq_thin", "liq_healthy",
        ],
        "value": [
            n_total, n_rel, f"{100*n_rel/n_total:.1f}%",
            str(df["sol_dt"].min().date()), str(df["sol_dt"].max().date()),
            (df["sol_dt"].max() - df["sol_dt"].min()).days,
            int((df.direction == "SOL→ETH").sum()),
            int((df.direction == "ETH→SOL").sum()),
            int((df.subtype == "full_clean").sum()),
            int((df.subtype != "full_clean").sum()),
            int((df.liquidity_category == "dead_pool_exploit").sum()),
            int((df.liquidity_category == "thin_pool_arb").sum()),
            int((df.liquidity_category == "healthy_market_arb").sum()),
        ],
    })
    overview.to_csv(TABLES / "a01_overview.csv", index=False)

    # ---------- Table A2: direction asymmetry ----------
    dir_stats = df.groupby("direction").agg(
        n=("id", "count"),
        size_median=("entry_cost_usd", "median"),
        size_mean=("entry_cost_usd", "mean"),
        roi_median=("roi_pct", "median"),
        roi_mean=("roi_pct", "mean"),
        net_pnl_median=("net_pnl_usd", "median"),
        net_pnl_sum=("net_pnl_usd", "sum"),
        time_diff_median=("time_diff_sec", "median"),
        fully_atomic_rate=("fully_atomic", "mean"),
    ).round(3)
    dir_stats.to_csv(TABLES / "a02_direction_asymmetry.csv")

    # ---------- Table A3: token HHI ----------
    token_counts = df["token_key"].value_counts()
    token_hhi = hhi(token_counts)
    top_tokens = token_counts.head(15).rename_axis("token").to_frame("count")
    top_tokens["share"] = (top_tokens["count"] / n_total).round(4)
    top_tokens.to_csv(TABLES / "a03_token_hhi.csv")

    # ---------- Table A5: size quantiles ----------
    size_qs = df.groupby("direction")["entry_cost_usd"].describe(
        percentiles=[.1, .25, .5, .75, .9, .95, .99]
    ).round(2)
    size_qs.to_csv(TABLES / "a05_size_quantiles.csv")

    # ====== Figure 1 ======
    fig = plt.figure(figsize=(14, 8.5))
    gs = fig.add_gridspec(2, 3, hspace=0.45, wspace=0.35)

    # --- (a) Dataset overview: subtype bar + reliability pie ---
    ax_a = fig.add_subplot(gs[0, 0])
    subtype_cnt = df["subtype"].value_counts()
    bars = ax_a.barh(subtype_cnt.index[::-1], subtype_cnt.values[::-1],
                     color=PALETTE["neutral"], edgecolor="black", linewidth=0.4)
    for b, v in zip(bars, subtype_cnt.values[::-1]):
        ax_a.text(v + n_total * 0.01, b.get_y() + b.get_height() / 2,
                  f"{v}  ({v/n_total:.0%})", va="center", fontsize=7.5)
    ax_a.set_xlim(0, max(subtype_cnt.values) * 1.25)
    ax_a.set_xlabel("candidates")
    ax_a.set_title(f"(a) Dataset overview  N={n_total}  ({n_rel} reliable, {100*n_rel/n_total:.0f}%)")

    # --- (b) Direction asymmetry: paired violin ---
    ax_b = fig.add_subplot(gs[0, 1])
    for i, (d, color) in enumerate(zip(["SOL→ETH", "ETH→SOL"],
                                       [PALETTE["sol_to_eth"], PALETTE["eth_to_sol"]])):
        data = df[(df.direction == d) & df.entry_cost_usd.notna() & (df.entry_cost_usd > 0)]
        vals = np.log10(data["entry_cost_usd"].clip(lower=0.01))
        parts = ax_b.violinplot(vals, positions=[i], widths=0.7, showmeans=False,
                                showmedians=True, showextrema=False)
        for pc in parts["bodies"]:
            pc.set_facecolor(color); pc.set_alpha(0.65); pc.set_edgecolor("black")
        parts["cmedians"].set_color("black")
    ax_b.set_xticks([0, 1])
    ax_b.set_xticklabels(["SOL→ETH", "ETH→SOL"])
    ax_b.set_ylabel("log10(entry_cost USD)")
    ax_b.set_title("(b) Direction asymmetry: bridge size")

    # annotate medians with absolute USD
    med_s = df.loc[df.direction == "SOL→ETH", "entry_cost_usd"].median()
    med_e = df.loc[df.direction == "ETH→SOL", "entry_cost_usd"].median()
    ax_b.text(0, ax_b.get_ylim()[1]*0.95, f"med=${med_s:,.0f}", ha="center", fontsize=7.5)
    ax_b.text(1, ax_b.get_ylim()[1]*0.95, f"med=${med_e:,.0f}", ha="center", fontsize=7.5)

    # --- (c) Token HHI: top-10 horizontal bar ---
    ax_c = fig.add_subplot(gs[0, 2])
    top10 = token_counts.head(10).iloc[::-1]
    colors_c = [PALETTE["accent"] if i == len(top10) - 1 else PALETTE["neutral"]
                for i in range(len(top10))]
    ax_c.barh(top10.index, top10.values, color=colors_c, edgecolor="black", linewidth=0.4)
    for i, (name, v) in enumerate(top10.items()):
        ax_c.text(v + n_total*0.005, i, f"{v} ({v/n_total:.0%})", va="center", fontsize=7.5)
    ax_c.set_xlim(0, max(top10.values) * 1.25)
    ax_c.set_xlabel("candidates")
    ax_c.set_title(f"(c) Token concentration  HHI={token_hhi:.0f}  (n_tokens={len(token_counts)})")

    # --- (d) Temporal heatmap: hour × weekday ---
    ax_d = fig.add_subplot(gs[1, 0])
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    pivot = (df.groupby(["weekday", "hour_utc"]).size()
               .unstack(fill_value=0)
               .reindex(weekday_order))
    # ensure all 24 hours columns
    for h in range(24):
        if h not in pivot.columns:
            pivot[h] = 0
    pivot = pivot[sorted(pivot.columns)]
    im = ax_d.imshow(pivot.values, aspect="auto", cmap="YlOrRd", interpolation="nearest")
    ax_d.set_xticks(range(0, 24, 3))
    ax_d.set_xticklabels(range(0, 24, 3))
    ax_d.set_yticks(range(len(weekday_order)))
    ax_d.set_yticklabels([w[:3] for w in weekday_order])
    ax_d.set_xlabel("hour (UTC)")
    ax_d.set_title(f"(d) Activity heatmap  peak={pivot.values.max()} at "
                   f"{weekday_order[np.unravel_index(pivot.values.argmax(), pivot.shape)[0]][:3]} "
                   f"{np.unravel_index(pivot.values.argmax(), pivot.shape)[1]:02d}:00")
    cbar = plt.colorbar(im, ax=ax_d, shrink=0.8, pad=0.02)
    cbar.set_label("candidates", fontsize=7)
    cbar.ax.tick_params(labelsize=6)

    # --- (e) Bridge size distribution ---
    ax_e = fig.add_subplot(gs[1, 1])
    sizes = df["entry_cost_usd"].dropna()
    sizes = sizes[sizes > 0]
    bins = np.logspace(np.log10(sizes.min()), np.log10(sizes.max()), 40)
    for d, color in zip(["SOL→ETH", "ETH→SOL"],
                        [PALETTE["sol_to_eth"], PALETTE["eth_to_sol"]]):
        dat = df[df.direction == d]["entry_cost_usd"].dropna()
        dat = dat[dat > 0]
        ax_e.hist(dat, bins=bins, alpha=0.55, color=color, label=d, edgecolor="black", linewidth=0.3)
    ax_e.set_xscale("log")
    ax_e.set_xlabel("entry_cost USD (log scale)")
    ax_e.set_ylabel("candidates")
    ax_e.set_title(f"(e) Bridge size distribution  median=${sizes.median():,.0f}  "
                   f"p99=${sizes.quantile(.99):,.0f}")
    ax_e.legend(frameon=False, loc="upper left")

    # --- (f) Liquidity tier × direction stacked bar ---
    ax_f = fig.add_subplot(gs[1, 2])
    tier_order = ["dead_pool_exploit", "thin_pool_arb", "healthy_market_arb"]
    tier_short = ["dead", "thin", "healthy"]
    if df["liquidity_category"].notna().any():
        group_col = "liquidity_category"
        plot_order = tier_order
        plot_labels = tier_short
        title = "(f) Liquidity tier × direction"
    else:
        # Some open-source candidate snapshots omit liquidity enrichment.
        # Fall back to reliability tiers so the macro figure remains runnable.
        group_col = "pnl_reliability"
        plot_order = list(df[group_col].dropna().value_counts().index[:4])
        plot_labels = [str(x).replace("_", "\n") for x in plot_order]
        title = "(f) Reliability tier × direction"

    ctab = (df.groupby([group_col, "direction"]).size()
              .unstack(fill_value=0)
              .reindex(plot_order))
    if "SOL→ETH" not in ctab.columns:
        ctab["SOL→ETH"] = 0
    if "ETH→SOL" not in ctab.columns:
        ctab["ETH→SOL"] = 0
    width = 0.6
    x = np.arange(len(plot_order))
    s = ctab["SOL→ETH"].values
    e = ctab["ETH→SOL"].values
    ax_f.bar(x, s, width, color=PALETTE["sol_to_eth"], label="SOL→ETH",
             edgecolor="black", linewidth=0.4)
    ax_f.bar(x, e, width, bottom=s, color=PALETTE["eth_to_sol"], label="ETH→SOL",
             edgecolor="black", linewidth=0.4)
    for xi, (si, ei) in enumerate(zip(s, e)):
        tot = si + ei
        ax_f.text(xi, tot + n_total*0.01, f"{tot}\n({tot/n_total:.0%})",
                  ha="center", fontsize=7.5)
    ax_f.set_xticks(x)
    ax_f.set_xticklabels(plot_labels)
    ax_f.set_ylabel("candidates")
    ymax = max(s + e) if len(s) else 0
    ax_f.set_ylim(0, max(ymax * 1.18, 1))
    ax_f.set_title(title)
    ax_f.legend(frameon=False, loc="upper left")

    fig.suptitle("Figure 1 — Macro Overview of Wormhole Portal Arbitrage (SOL↔ETH, 1-year window)",
                 fontsize=12, y=0.995, fontweight="bold")
    out = FIGS / "01_macro.png"
    fig.savefig(out)
    plt.close(fig)

    # ---------- Console summary ----------
    print(f"[A1] N={n_total}, reliable={n_rel} ({100*n_rel/n_total:.1f}%)")
    print(f"[A2] direction: SOL→ETH={int((df.direction=='SOL→ETH').sum())}  "
          f"ETH→SOL={int((df.direction=='ETH→SOL').sum())}")
    print(f"[A3] tokens: {len(token_counts)}  HHI={token_hhi:.0f}  top1={top_tokens.index[0]}"
          f" ({top_tokens.iloc[0]['share']:.1%})")
    print(f"[A4] peak hour × weekday = {np.unravel_index(pivot.values.argmax(), pivot.shape)}")
    print(f"[A5] size median=${sizes.median():,.2f}  p99=${sizes.quantile(.99):,.2f}")
    print(f"saved: {out}")
    for t in TABLES.glob("a0*.csv"):
        print(f"saved: {t}")


if __name__ == "__main__":
    main()
