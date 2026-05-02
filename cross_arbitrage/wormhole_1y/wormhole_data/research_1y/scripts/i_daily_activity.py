#!/usr/bin/env python3
"""Daily time series: trade count + net PnL."""

import json
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

import common_loader as cl

OUT_DIR = Path(__file__).resolve().parents[1] / "figs"
TAB_DIR = Path(__file__).resolve().parents[1] / "tables"
OUTLIER_FILE = Path(__file__).resolve().parent / "excluded_outliers.json"

plt.rcParams.update(cl.MPL_STYLE)
PAL = cl.PALETTE


def build_daily(apply_outlier_exclusion: bool):
    df = cl.load_df()
    df = df[df["is_reliable"]].copy()
    if apply_outlier_exclusion and OUTLIER_FILE.exists():
        o = json.load(open(OUTLIER_FILE))
        excl = set(o.get("worst10", [])) | set(o.get("best10", []))
        df = df[~df["id"].isin(excl)].copy()
    df["date_utc"] = pd.to_datetime(df["date_utc"])
    agg = (df.groupby(["date_utc", "direction"])
             .agg(n=("id", "size"),
                  pnl=("net_pnl_usd", "sum"))
             .reset_index())
    return df, agg


def plot(apply_outlier_exclusion: bool, tag: str):
    df, agg = build_daily(apply_outlier_exclusion)
    # full date range
    dmin, dmax = df["date_utc"].min(), df["date_utc"].max()
    full_idx = pd.date_range(dmin, dmax, freq="D")

    def pivot(col):
        p = (agg.pivot(index="date_utc", columns="direction", values=col)
                .reindex(full_idx).fillna(0))
        if "SOL→ETH" not in p.columns: p["SOL→ETH"] = 0
        if "ETH→SOL" not in p.columns: p["ETH→SOL"] = 0
        return p

    cnt = pivot("n")
    pnl = pivot("pnl")

    # ------ figure ------
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 5.8), sharex=True,
                                    gridspec_kw={"hspace": 0.18})

    # (a) daily count, overlaid lines (no stacking to avoid visual illusion)
    roll_s = cnt["SOL→ETH"].rolling(7, min_periods=1).mean()
    roll_e = cnt["ETH→SOL"].rolling(7, min_periods=1).mean()
    ax1.plot(cnt.index, cnt["SOL→ETH"], color=PAL["sol_to_eth"],
             lw=0.7, alpha=0.35)
    ax1.plot(cnt.index, cnt["ETH→SOL"], color=PAL["eth_to_sol"],
             lw=0.7, alpha=0.35)
    n_s = int(cnt["SOL→ETH"].sum()); n_e = int(cnt["ETH→SOL"].sum())
    ax1.plot(roll_s.index, roll_s, color=PAL["sol_to_eth"], lw=1.8,
             label=f"SOL→ETH (n={n_s:,})")
    ax1.plot(roll_e.index, roll_e, color=PAL["eth_to_sol"], lw=1.8,
             label=f"ETH→SOL (n={n_e:,})")
    ax1.set_ylabel("daily events (7-day MA)")
    tot = cnt.sum(axis=1)
    peak_day = tot.idxmax().date()
    peak_val = int(tot.max())
    ax1.set_title(f"(a) Daily arbitrage count   total={len(df):,}   "
                   f"peak={peak_val} on {peak_day}")
    ax1.legend(frameon=False, loc="upper left", ncol=2)
    ax1.grid(True, alpha=0.3)

    # (b) daily net PnL, overlaid 7-day rolling lines + cumulative
    pnl_roll_s = pnl["SOL→ETH"].rolling(7, min_periods=1).mean()
    pnl_roll_e = pnl["ETH→SOL"].rolling(7, min_periods=1).mean()
    ax2.plot(pnl.index, pnl["SOL→ETH"], color=PAL["sol_to_eth"],
             lw=0.6, alpha=0.3)
    ax2.plot(pnl.index, pnl["ETH→SOL"], color=PAL["eth_to_sol"],
             lw=0.6, alpha=0.3)
    pnl_s_tot = float(pnl["SOL→ETH"].sum()); pnl_e_tot = float(pnl["ETH→SOL"].sum())
    ax2.plot(pnl_roll_s.index, pnl_roll_s, color=PAL["sol_to_eth"], lw=1.8,
             label=f"SOL→ETH (${pnl_s_tot/1e3:,.1f}k)")
    ax2.plot(pnl_roll_e.index, pnl_roll_e, color=PAL["eth_to_sol"], lw=1.8,
             label=f"ETH→SOL (${pnl_e_tot/1e3:,.1f}k)")
    ax2.axhline(0, color="black", lw=0.6)
    # cumulative line on secondary axis
    cum = (pnl["SOL→ETH"] + pnl["ETH→SOL"]).cumsum()
    ax2r = ax2.twinx()
    ax2r.plot(cum.index, cum.values / 1e3, color="black", lw=1.3, ls="--",
              label="cumulative ($k)")
    ax2r.set_ylabel("cumulative net PnL ($k)")
    ax2.set_ylabel("daily net PnL (USD, 7-day MA)")
    tot_pnl = float(pnl.values.sum())
    ax2.set_title(f"(b) Daily net PnL   aggregate={tot_pnl:,.0f} USD   "
                   f"cumulative end={cum.iloc[-1]/1e3:,.1f}k")
    ax2.grid(True, alpha=0.3)

    # combine legends
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2r.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, frameon=False, loc="upper left", ncol=3)

    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.autofmt_xdate(rotation=30)

    title = ("Figure — Daily cross-chain arbitrage activity "
             f"({dmin.date()} to {dmax.date()}, "
             f"{'20-outlier excluded' if apply_outlier_exclusion else 'full reliable'})")
    fig.suptitle(title, fontsize=11, y=0.995, fontweight="bold")

    out = OUT_DIR / f"10_daily_activity_{tag}.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")

    # also dump CSV for downstream reference
    csv = cnt.join(pnl, lsuffix="_n", rsuffix="_pnl")
    csv["n_total"] = cnt.sum(axis=1)
    csv["pnl_total"] = pnl.sum(axis=1)
    csv["pnl_cumulative"] = cum
    csv_path = TAB_DIR / f"10_daily_activity_{tag}.csv"
    csv.to_csv(csv_path)
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    plot(apply_outlier_exclusion=True, tag="excl20")
    plot(apply_outlier_exclusion=False, tag="all_reliable")
