#!/usr/bin/env python3
"""
主题 E · 结构性风险 (Structural Risk).

Fig 5: 1 x 3 grid
  (a) E18 套利者集中度: Lorenz 曲线 (按 count 和 按 |net_pnl|) + Gini + HHI
  (b) E19 可靠性分层: ROI 分布 × pnl_reliability
  (c) E20 Round-trip 检测: 同一 actor 的 SOL→ETH→SOL / ETH→SOL→ETH 闭环,
        横轴为 round-trip 时间 (log s), 纵轴为 paired ROI sum

Actor identity = (arb_sol, arb_eth) tuple.

输出:
  figs/05_risk.png
  tables/e18_concentration.csv
  tables/e19_reliability_tiers.csv
  tables/e20_round_trips.csv
  tables/e20_top_round_trip_actors.csv
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl

from common_loader import load_df, FIGS, TABLES, PALETTE, MPL_STYLE

mpl.rcParams.update(MPL_STYLE)


def gini(x):
    """Gini coefficient on a 1d non-negative array."""
    x = np.asarray(x, dtype=float)
    x = x[x >= 0]
    if len(x) == 0 or x.sum() == 0:
        return np.nan
    x_sorted = np.sort(x)
    n = len(x_sorted)
    cum = np.cumsum(x_sorted)
    # Gini via Lorenz area
    return (n + 1 - 2 * (cum.sum() / cum[-1])) / n


def hhi(counts):
    p = np.asarray(counts, dtype=float) / np.asarray(counts, dtype=float).sum()
    return float((p ** 2).sum() * 10_000)


def lorenz_points(x):
    x = np.asarray(x, dtype=float)
    x = np.sort(x[x >= 0])
    if len(x) == 0:
        return np.array([0]), np.array([0])
    cum_y = np.insert(np.cumsum(x) / x.sum(), 0, 0)
    cum_x = np.insert(np.arange(1, len(x)+1) / len(x), 0, 0)
    return cum_x, cum_y


def main():
    df = load_df().copy()
    df["actor"] = df["arb_sol"].fillna("") + "|" + df["arb_eth"].fillna("")

    # ---------- E18: concentration ----------
    per_actor = df.groupby("actor").agg(
        trades=("id", "count"),
        net_pnl_sum=("net_pnl_usd", lambda s: s.dropna().sum()),
        net_pnl_abs_sum=("net_pnl_usd", lambda s: s.dropna().abs().sum()),
        size_sum=("entry_cost_usd", lambda s: s.dropna().sum()),
    ).sort_values("trades", ascending=False)
    per_actor["trade_share"] = per_actor["trades"] / per_actor["trades"].sum()
    per_actor["cum_trade_share"] = per_actor["trade_share"].cumsum()
    per_actor.to_csv(TABLES / "e18_concentration.csv")

    gini_trades = gini(per_actor["trades"].values)
    gini_pnl    = gini(per_actor["net_pnl_abs_sum"].values)
    hhi_trades  = hhi(per_actor["trades"].values)
    top5_share  = per_actor["trade_share"].head(5).sum()
    top10_share = per_actor["trade_share"].head(10).sum()

    # ---------- E19: reliability tier stats ----------
    rel_stats = df.groupby("pnl_reliability").agg(
        n=("id", "count"),
        roi_median=("roi_pct", "median"),
        roi_std=("roi_pct", "std"),
        net_pnl_median=("net_pnl_usd", "median"),
        pct_net_positive=("net_pnl_usd", lambda s: (s > 0).mean()),
        size_median=("entry_cost_usd", "median"),
    ).round(3)
    rel_stats.to_csv(TABLES / "e19_reliability_tiers.csv")

    # ---------- E20: round-trip detection ----------
    # For each actor, sort by timestamp; find adjacent-direction-flip pairs.
    df_sorted = df[df.pnl_complete == True].copy()
    # use sol_ts as a unified timeline
    df_sorted["ts"] = df_sorted["sol_ts"]
    df_sorted = df_sorted.sort_values(["actor", "ts"])

    round_trips = []
    for actor, g in df_sorted.groupby("actor"):
        g = g.reset_index(drop=True)
        if g["direction"].nunique() < 2:
            continue
        for i in range(len(g) - 1):
            a, b = g.iloc[i], g.iloc[i + 1]
            if a.direction == b.direction:
                continue
            dt = int(b.ts - a.ts)
            pnl_a = a.net_pnl_usd if pd.notna(a.net_pnl_usd) else 0.0
            pnl_b = b.net_pnl_usd if pd.notna(b.net_pnl_usd) else 0.0
            round_trips.append({
                "actor":       actor,
                "actor_short": actor[:12] + ".." + actor.split("|")[1][-6:],
                "leg1_dir":    a.direction,
                "leg1_token":  a.token_key,
                "leg2_dir":    b.direction,
                "leg2_token":  b.token_key,
                "leg1_ts":     int(a.ts),
                "leg2_ts":     int(b.ts),
                "round_trip_sec": dt,
                "leg1_size_usd": a.entry_cost_usd,
                "leg2_size_usd": b.entry_cost_usd,
                "leg1_roi":    a.roi_pct,
                "leg2_roi":    b.roi_pct,
                "leg1_pnl":    pnl_a,
                "leg2_pnl":    pnl_b,
                "round_trip_pnl": pnl_a + pnl_b,
                "same_token":  a.token_key == b.token_key,
            })
    rt_df = pd.DataFrame(round_trips)
    rt_df.to_csv(TABLES / "e20_round_trips.csv", index=False)

    # top round-trip actors
    if len(rt_df) > 0:
        top_actors = (rt_df.groupby("actor")
                           .agg(n_round_trips=("round_trip_sec", "count"),
                                rt_sec_median=("round_trip_sec", "median"),
                                total_rt_pnl=("round_trip_pnl", "sum"),
                                same_token_rate=("same_token", "mean"))
                           .sort_values("n_round_trips", ascending=False)
                           .round(3)
                           .head(20))
        top_actors.to_csv(TABLES / "e20_top_round_trip_actors.csv")

    # ====== Figure 5 ======
    fig = plt.figure(figsize=(14, 5.2))
    gs = fig.add_gridspec(1, 3, wspace=0.32)

    # --- (a) Lorenz curve + bar chart of top 10 actors ---
    ax_a = fig.add_subplot(gs[0, 0])
    cx_t, cy_t = lorenz_points(per_actor["trades"].values)
    cx_p, cy_p = lorenz_points(per_actor["net_pnl_abs_sum"].values)
    ax_a.plot([0, 1], [0, 1], color="grey", ls="--", lw=0.8, label="perfect equality")
    ax_a.plot(cx_t, cy_t, color=PALETTE["sol_to_eth"], lw=1.8,
              label=f"by trade count  Gini={gini_trades:.2f}")
    ax_a.plot(cx_p, cy_p, color=PALETTE["eth_to_sol"], lw=1.8,
              label=f"by |net PnL|  Gini={gini_pnl:.2f}")
    # shade area
    ax_a.fill_between(cx_t, cy_t, cx_t, color=PALETTE["sol_to_eth"], alpha=0.10)
    ax_a.set_xlabel("cumulative actor share (sorted low→high)")
    ax_a.set_ylabel("cumulative share of value")
    ax_a.set_title(f"(a) Arbitrageur concentration  N={len(per_actor)}  "
                   f"HHI={hhi_trades:.0f}\n"
                   f"top-5 = {top5_share:.0%}, top-10 = {top10_share:.0%} of trades")
    ax_a.legend(frameon=False, loc="upper left", fontsize=7.5)
    ax_a.set_xlim(0, 1); ax_a.set_ylim(0, 1)

    # --- (b) Reliability tiers ---
    ax_b = fig.add_subplot(gs[0, 1])
    tier_order = ["reliable", "reliable_after_dedup_failed",
                  "unreliable_over_coverage", "unreliable_asymmetric",
                  "unreliable_partial_holdings"]
    short_lbl = {
        "reliable":                    "reliable",
        "reliable_after_dedup_failed": "reliable\n(dedup_fail)",
        "unreliable_over_coverage":    "unreliable\n(over_cov)",
        "unreliable_asymmetric":       "unreliable\n(asymmetric)",
        "unreliable_partial_holdings": "unreliable\n(partial)",
    }
    tiers_present = [t for t in tier_order if t in df.pnl_reliability.values]
    colors_b = [PALETTE["positive"], PALETTE["accent"], PALETTE["negative"],
                PALETTE["eth_to_sol"], PALETTE["neutral"]][:len(tiers_present)]
    data_b, labels_b, ns_b = [], [], []
    for t in tiers_present:
        sub = df[df.pnl_reliability == t]["roi_pct"].dropna().values
        sub = np.clip(sub, -30, 30)
        if len(sub) > 0:
            data_b.append(sub)
            labels_b.append(short_lbl[t])
            ns_b.append(len(sub))
    bp = ax_b.boxplot(data_b, patch_artist=True, showfliers=False,
                      widths=0.55, medianprops=dict(color="black", lw=1.1))
    ax_b.set_xticks(range(1, len(labels_b) + 1))
    ax_b.set_xticklabels(labels_b)
    for box, c in zip(bp["boxes"], colors_b):
        box.set_facecolor(c); box.set_alpha(0.75)
    for i, (l, n) in enumerate(zip(labels_b, ns_b)):
        ax_b.text(i + 1, 28, f"n={n}", ha="center", fontsize=7)
    ax_b.axhline(0, color="grey", ls="--", lw=0.5)
    ax_b.set_ylim(-35, 35)
    ax_b.set_ylabel("ROI %  (clipped ±30)")
    ax_b.set_title("(b) ROI by reliability tier")
    ax_b.tick_params(axis="x", labelsize=7.5)

    # --- (c) Round-trip scatter: dt vs total PnL ---
    ax_c = fig.add_subplot(gs[0, 2])
    if len(rt_df) > 0:
        # color by whether leg1 and leg2 are same token
        same = rt_df["same_token"]
        x_rt = np.clip(rt_df["round_trip_sec"], 1, 30 * 86400)
        y_rt = np.clip(rt_df["round_trip_pnl"], -500, 500)
        ax_c.scatter(x_rt[same], y_rt[same], s=8, alpha=0.7,
                     color=PALETTE["positive"],
                     label=f"same token  n={int(same.sum())}",
                     edgecolors="none")
        ax_c.scatter(x_rt[~same], y_rt[~same], s=8, alpha=0.5,
                     color=PALETTE["neutral"],
                     label=f"cross token  n={int((~same).sum())}",
                     edgecolors="none")
        ax_c.set_xscale("log")
        ax_c.axhline(0, color="grey", lw=0.5, ls="--")
        # reference vertical lines
        for ref, lbl in [(60, "1m"), (3600, "1h"), (86400, "1d"),
                         (7*86400, "7d")]:
            ax_c.axvline(ref, color="grey", ls=":", lw=0.5, alpha=0.6)
            ax_c.text(ref, ax_c.get_ylim()[1] * 0.95, lbl, fontsize=6.5,
                      color="grey", rotation=90, ha="right", va="top")
        ax_c.set_xlabel("round-trip Δt  (s, log)")
        ax_c.set_ylabel("leg1+leg2 net PnL $  (clipped ±500)")
        med_dt = np.median(rt_df["round_trip_sec"])
        pos_rate = (rt_df["round_trip_pnl"] > 0).mean()
        ax_c.set_title(f"(c) Round-trip events  n={len(rt_df)}  "
                       f"med Δt={med_dt/3600:.1f}h  "
                       f"profitable={pos_rate:.0%}")
        ax_c.legend(frameon=False, loc="lower right", fontsize=7)
    else:
        ax_c.text(0.5, 0.5, "(no round-trips detected)",
                  ha="center", va="center", transform=ax_c.transAxes)

    fig.suptitle("Figure 5 — Structural Risk: Concentration, Reliability, Round-Trips",
                 fontsize=12, y=1.01, fontweight="bold")
    out = FIGS / "05_risk.png"
    fig.savefig(out)
    plt.close(fig)

    # ---------- Console summary ----------
    print(f"[E18] actors={len(per_actor)}  HHI={hhi_trades:.0f}  "
          f"Gini(trades)={gini_trades:.3f}  Gini(|PnL|)={gini_pnl:.3f}")
    print(f"      top-5 share = {top5_share:.2%}, top-10 = {top10_share:.2%}")
    print(f"      top-5 actors (trades, net_pnl_sum):")
    for a, row in per_actor.head(5).iterrows():
        print(f"        {a[:12]}..{a.split('|')[1][-6:]}  "
              f"n={int(row['trades'])}  pnl=${row['net_pnl_sum']:,.0f}")
    print()
    print("[E19] reliability tiers:")
    print(rel_stats[["n", "roi_median", "pct_net_positive", "size_median"]].to_string())
    print()
    if len(rt_df) > 0:
        print(f"[E20] round-trip events = {len(rt_df)}  "
              f"median Δt = {np.median(rt_df['round_trip_sec'])/3600:.1f} h")
        print(f"      same-token round-trips = {int(rt_df['same_token'].sum())} "
              f"({rt_df['same_token'].mean():.0%})")
        print(f"      profitable = {(rt_df['round_trip_pnl']>0).mean():.0%}, "
              f"total round-trip PnL = ${rt_df['round_trip_pnl'].sum():,.0f}")
        # fast round-trips
        fast = rt_df[rt_df["round_trip_sec"] <= 3600]
        print(f"      <1h round-trips = {len(fast)}  "
              f"({len(fast)/len(rt_df):.0%})")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
