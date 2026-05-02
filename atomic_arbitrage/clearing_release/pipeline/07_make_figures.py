import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


# Run from the release root, e.g.
#   python pipeline/07_make_figures.py
# Resolution order for the cleaned dataset:
#   1. $ARB_DATA_DIR if set
#   2. ./merged_arbs_clean (CWD)
#   3. <repo>/merged_arbs_clean
#   4. <repo>/data/merged_arbs_clean
ROOT = Path(__file__).resolve().parents[1]
_candidates = [
    Path(os.environ["ARB_DATA_DIR"]) if os.environ.get("ARB_DATA_DIR") else None,
    Path.cwd() / "merged_arbs_clean",
    ROOT / "merged_arbs_clean",
    ROOT / "data" / "merged_arbs_clean",
]
DATA_DIR = next((p for p in _candidates if p and p.exists()), ROOT / "merged_arbs_clean")
FIG_DIR = ROOT / "figures"
STATS_OUT = ROOT / "data" / "stats.json"
FIG_DIR.mkdir(exist_ok=True)
STATS_OUT.parent.mkdir(exist_ok=True)
print(f"[07_make_figures] reading: {DATA_DIR}")
print(f"[07_make_figures] figures → {FIG_DIR}")
print(f"[07_make_figures] stats   → {STATS_OUT}")

WSOL = "So11111111111111111111111111111111111111112"
SOL = 1e9

plt.rcParams.update(
    {
        "figure.dpi": 120,
        "savefig.dpi": 240,
        "font.family": "DejaVu Sans",
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.axisbelow": True,
    }
)


def parse_dt(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            pass
    return None


def settlement_regime(row):
    if row.get("in_bundle") is True:
        return "single-tx bundle" if (row.get("bundle_tx_count") or 0) == 1 else "multi-tx bundle"
    return "non-bundle"


def corrected_net(row):
    if "net_profit_lamports" in row:
        return row.get("net_profit_lamports") or 0
    sol = row.get("sol_lamports") or 0
    fee = row.get("tx_fee_lamports") or 0
    if row.get("in_bundle") is True and (row.get("bundle_tx_count") or 0) > 1:
        return sol - fee - (row.get("bundle_tip_lamports") or 0)
    return sol - fee


def tip_paid(row):
    if row.get("in_bundle") is not True:
        return 0
    if (row.get("bundle_tx_count") or 0) > 1:
        return row.get("bundle_tip_lamports") or 0
    return row.get("jito_tip_lamports") or 0


records = []
for path in sorted(DATA_DIR.glob("merged_*.json")):
    with path.open() as fh:
        for row in json.load(fh):
            dt = parse_dt(row.get("time"))
            if dt is None:
                continue
            is_wsol = WSOL in (row.get("arb_token_mints") or [])
            rec = {
                "month": dt.strftime("%Y-%m"),
                "date": dt.date().isoformat(),
                "wallet": row.get("wallet"),
                "is_wsol": is_wsol,
                "regime": settlement_regime(row),
                "dexes": [d for d in (row.get("dexes") or []) if d and d != "Jupiter v6"],
                "swap_count": row.get("swap_count") or 0,
                "gross": row.get("arb_gross_profit_lamports") if is_wsol else None,
                "net": corrected_net(row) if is_wsol else None,
                "tip": tip_paid(row) if is_wsol else 0,
                "fee": row.get("tx_fee_lamports") or 0,
            }
            records.append(rec)

if not records:
    raise SystemExit("No records loaded")

wsol = [r for r in records if r["is_wsol"] and r["gross"] is not None]
months = sorted({r["month"] for r in records})
regimes = ["non-bundle", "single-tx bundle", "multi-tx bundle"]
colors = {
    "non-bundle": "#4C78A8",
    "single-tx bundle": "#59A14F",
    "multi-tx bundle": "#E15759",
}


# Figure 1: monthly clearing channels.
month_regime = defaultdict(Counter)
for r in records:
    month_regime[r["month"]][r["regime"]] += 1

fig, ax = plt.subplots(figsize=(7.2, 3.7))
x = np.arange(len(months))
bottom = np.zeros(len(months))
for regime in regimes:
    vals = np.array([month_regime[m][regime] for m in months])
    ax.bar(x, vals, bottom=bottom, label=regime.title(), color=colors[regime], edgecolor="white", linewidth=0.4)
    bottom += vals
ax.set_yscale("log")
ax.set_ylabel("Detected arbitrages, log scale")
ax.set_xlabel("Month")
ax.set_xticks(x)
ax.set_xticklabels(months, rotation=0)
ax.set_title("Arbitrage settlement channel over time")
if "2025-09" in months and "2026-02" in months:
    lo = months.index("2025-09") + 0.5
    hi = months.index("2026-02") - 0.5
    ax.axvspan(lo, hi, color="#D8D8D8", alpha=0.55, zorder=0)
    ax.text((lo + hi) / 2, max(bottom) * 0.35, "159-day\ndata gap", ha="center", va="center", fontsize=8, color="#555")
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.19), ncol=3, frameon=False, fontsize=8)
fig.tight_layout()
fig.savefig(FIG_DIR / "fig1_settlement_channels.png")
fig.savefig(FIG_DIR / "fig1_settlement_channels.pdf")
plt.close(fig)


# Figure 2: DEX venues where arbitrage paths close.
venue_counter = Counter()
for r in records:
    for dex in set(r["dexes"]):
        venue_counter[dex] += 1
top_venues = venue_counter.most_common(12)
names = [name for name, _ in top_venues][::-1]
vals = [count for _, count in top_venues][::-1]

fig, ax = plt.subplots(figsize=(7.0, 4.2))
bars = ax.barh(names, vals, color="#7F6D5F", edgecolor="none")
ax.set_xlabel("Arbitrages containing venue")
ax.set_title("DEX venues used by detected arbitrage paths")
for bar, value in zip(bars, vals):
    ax.text(value, bar.get_y() + bar.get_height() / 2, f" {value:,}", va="center", fontsize=8)
fig.tight_layout()
fig.savefig(FIG_DIR / "fig2_dex_venues.png")
fig.savefig(FIG_DIR / "fig2_dex_venues.pdf")
plt.close(fig)


# Figure 3: bundle clearing cost pressure.
bundle = [r for r in wsol if r["regime"] != "non-bundle" and r["gross"] and r["gross"] > 0]
ratios_by_regime = {"single-tx bundle": [], "multi-tx bundle": []}
for r in bundle:
    ratio = r["tip"] / r["gross"] if r["gross"] else math.nan
    if math.isfinite(ratio) and ratio >= 0:
        ratios_by_regime[r["regime"]].append(ratio)

fig, ax = plt.subplots(figsize=(5.8, 3.8))
data = [
    np.clip(np.array(ratios_by_regime["single-tx bundle"]) * 100, 0, 400),
    np.clip(np.array(ratios_by_regime["multi-tx bundle"]) * 100, 0, 400),
]
bp = ax.boxplot(
    data,
    labels=["Single-tx\nbundle", "Multi-tx\nbundle"],
    whis=(5, 95),
    showfliers=False,
    patch_artist=True,
)
for patch, color in zip(bp["boxes"], [colors["single-tx bundle"], colors["multi-tx bundle"]]):
    patch.set_facecolor(color)
    patch.set_alpha(0.82)
for median in bp["medians"]:
    median.set_color("black")
    median.set_linewidth(1.4)
ax.axhline(100, color="#333", linestyle="--", linewidth=1.0, label="tip = gross")
ax.set_ylabel("Jito tip / gross profit (%)")
ax.set_title("Bundle clearing cost relative to gross profit")
ax.set_ylim(0, 400)
ax.legend(frameon=False, fontsize=8)
fig.tight_layout()
fig.savefig(FIG_DIR / "fig3_tip_to_gross.png")
fig.savefig(FIG_DIR / "fig3_tip_to_gross.pdf")
plt.close(fig)


def pct(values, p):
    arr = np.array(values)
    return float(np.percentile(arr, p)) if len(arr) else None


stats = {
    "total_records": len(records),
    "wsol_records": len(wsol),
    "first_month": months[0],
    "last_month": months[-1],
    "first_date": min(r["date"] for r in records),
    "last_date": max(r["date"] for r in records),
    "unique_wallets": len({r["wallet"] for r in records if r["wallet"]}),
    "regime_counts": {regime: sum(1 for r in records if r["regime"] == regime) for regime in regimes},
    "wsol_regime_counts": {regime: sum(1 for r in wsol if r["regime"] == regime) for regime in regimes},
    "top_venues": top_venues,
    "median_swap_count": float(np.median([r["swap_count"] for r in records])),
    "wsol_total_gross_sol": sum(r["gross"] or 0 for r in wsol) / SOL,
    "wsol_total_net_sol": sum(r["net"] or 0 for r in wsol) / SOL,
    "wsol_median_net_lamports": float(np.median([r["net"] for r in wsol])),
    "bundle_tip_to_gross_pct": {
        "single_tx_median": pct([v * 100 for v in ratios_by_regime["single-tx bundle"]], 50),
        "single_tx_p90": pct([v * 100 for v in ratios_by_regime["single-tx bundle"]], 90),
        "multi_tx_median": pct([v * 100 for v in ratios_by_regime["multi-tx bundle"]], 50),
        "multi_tx_p90": pct([v * 100 for v in ratios_by_regime["multi-tx bundle"]], 90),
    },
}

STATS_OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=2))

print(json.dumps(stats, ensure_ascii=False, indent=2))
