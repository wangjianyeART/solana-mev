#!/usr/bin/env python3
"""Step 5 — Build the four-panel sandwich anatomy figure (paper Fig. 1).

Single-column-width version designed to render legibly at ~3.3 in wide in an
ACM sigconf 2-column layout. Layout is 2x2:

  (A) profitability decomposition  (price-edge vs realised SOL)
  (B) capital -> profit / cost log-log scaling
  (C) attacker concentration (Lorenz, Gini)
  (D) symmetry x profit hexbin

Inputs (both shipped):
  data/sandwiches_strong_clean.jsonl
  data/strong_tip_fee_cache.jsonl

Output:
  figures/sandwich_anatomy_col_en.pdf
"""
import os
import json
from collections import Counter
from pathlib import Path

import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import matplotlib.colors as mcolors


ROOT = Path(__file__).resolve().parent.parent
STRONG = ROOT / "data" / "sandwiches_strong_clean.jsonl"
TIPS = ROOT / "data" / "strong_tip_fee_cache.jsonl"
FIG_DIR = ROOT / "figures"


plt.rcParams.update({
    "font.family":       ["DejaVu Sans"],
    "font.size":         17,
    "axes.titlesize":    19,
    "axes.titleweight":  "bold",
    "axes.labelsize":    17,
    "xtick.labelsize":   15,
    "ytick.labelsize":   15,
    "legend.fontsize":   14,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.linewidth":    1.0,
    "xtick.major.width": 1.0,
    "ytick.major.width": 1.0,
    "savefig.bbox":      "tight",
    "pdf.fonttype":      42,
})

PROFIT, LOSS = "#2c7a3e", "#b3293f"
ACCENT, NEUTRAL, GREY = "#d97c1e", "#3b6ea5", "#888"
SOL = "So11111111111111111111111111111111111111112"


def load():
    strongs = []
    with open(STRONG) as f:
        for l in f:
            strongs.append(json.loads(l))
    bm = {}
    with open(TIPS) as f:
        for l in f:
            r = json.loads(l)
            bm[r["bid"]] = r
    return strongs, bm


def per_record(r, bm):
    fb, fq, bb, bq = r["front_base"], r["front_quote"], r["back_base"], r["back_quote"]
    fp = abs(fq) / abs(fb) if fb else 0
    bp = abs(bq) / abs(bb) if bb else 0
    if r["direction"] == "sell" and fp:
        edge = (bp - fp) / fp
    elif fp:
        edge = (fp - bp) / fp
    else:
        edge = 0
    if r["base_mint"] == SOL:
        cap = (abs(fb) + abs(bb)) / 2
    elif r["quote_mint"] == SOL:
        cap = (abs(fq) + abs(bq)) / 2
    else:
        cap = None
    m = bm.get(r["bundle_id"], {})
    return {
        "profit_sol": r.get("profit_sol", 0),
        "edge": edge,
        "capital": cap,
        "tip": (m.get("tip", 0) or 0) / 1e9,
        "fee": (m.get("fees", 0) or 0) / 1e9,
        "n_victims": len(r.get("victims") or []),
        "direction": r["direction"],
        "attacker": r["attacker"],
        "sym": r["sym_ratio"],
    }


def make_fig(D, S):
    L = {
        "title_a": "(A) Profitability decomposition",
        "ax_x":    "Sell ≤ Buy",
        "ax_y":    "Sell > Buy",
        "ax_yt":   "Net profit",
        "ax_yb":   "Net loss",
        "title_b": "(B) Capital → profit / cost",
        "xlab_b":  "Capital (SOL, log)",
        "ylab_b":  "|profit| or cost (SOL, log)",
        "leg_p":   "profit",
        "leg_l":   "loss",
        "leg_c":   "cost",
        "title_c": "(C) Attacker concentration",
        "xlab_c":  "Attacker percentile",
        "ylab_c":  "Cumulative bundles (%)",
        "title_d": "(D) Symmetry × profit",
        "xlab_d":  "sym_ratio (lower = tighter)",
        "ylab_d":  "profit (SOL, ±0.4)",
        "binmean": "binned mean",
    }
    plt.rcParams["font.family"] = ["DejaVu Sans"]

    fig = plt.figure(figsize=(9.0, 9.0))
    gs = fig.add_gridspec(2, 2, hspace=0.62, wspace=0.50,
                          top=0.94, bottom=0.07, left=0.09, right=0.97)

    # (A) contingency
    gs_a = gs[0, 0].subgridspec(2, 2, width_ratios=[5, 1], height_ratios=[1, 5],
                                hspace=0.06, wspace=0.06)
    axA = fig.add_subplot(gs_a[1, 0])
    axAx = fig.add_subplot(gs_a[0, 0], sharex=axA)
    axAy = fig.add_subplot(gs_a[1, 1], sharey=axA)
    cells = np.zeros((2, 2), dtype=int)
    for d in S:
        r = 0 if d["profit_sol"] > 0 else 1
        c = 1 if d["edge"] > 0 else 0
        cells[r, c] += 1
    total = cells.sum()
    cmap = mcolors.LinearSegmentedColormap.from_list("ablue", ["#f3f7fa", "#1a4d80"])
    axA.imshow(cells, cmap=cmap, aspect="auto", origin="upper")
    for i in range(2):
        for j in range(2):
            v = cells[i, j]
            col = "white" if v > total * 0.25 else "black"
            axA.text(j, i, f"{v:,}\n{v/total*100:.0f}%",
                     ha="center", va="center", fontsize=18,
                     color=col, fontweight="bold")
    axA.set_xticks([0, 1])
    axA.set_xticklabels([L["ax_x"], L["ax_y"]], fontsize=14)
    axA.set_yticks([0, 1])
    axA.set_yticklabels([L["ax_yt"], L["ax_yb"]], fontsize=14)
    axA.add_patch(plt.Rectangle((0.5, -0.5), 1, 1, fill=False, edgecolor=PROFIT, lw=3))

    edge_pos = cells[:, 1].sum()
    edge_neg = cells[:, 0].sum()
    axAx.bar([0, 1], [edge_neg, edge_pos], color=[GREY, ACCENT], width=0.85, edgecolor="white")
    axAx.set_xticks([])
    axAx.set_yticks([])
    axAx.set_ylim(0, max(edge_pos, edge_neg) * 1.25)
    for s in ["top", "right", "left"]:
        axAx.spines[s].set_visible(False)
    axAx.set_title(L["title_a"], loc="left", pad=4, fontsize=18)

    prof_pos = cells[0, :].sum()
    prof_neg = cells[1, :].sum()
    axAy.barh([0, 1], [prof_pos, prof_neg], color=[PROFIT, LOSS], height=0.85, edgecolor="white")
    axAy.set_yticks([])
    axAy.set_xticks([])
    axAy.invert_yaxis()
    for s in ["top", "right", "bottom"]:
        axAy.spines[s].set_visible(False)
    axAy.set_xlim(0, max(prof_pos, prof_neg) * 1.4)

    # (B) scaling
    axB = fig.add_subplot(gs[0, 1])
    cap = np.array([d["capital"] for d in S])
    prof = np.array([d["profit_sol"] for d in S])
    cost = np.array([d["tip"] + d["fee"] for d in S])
    m_pos = (cap > 0) & (prof > 0)
    m_neg = (cap > 0) & (prof < 0)
    m_cos = (cap > 0) & (cost > 0)
    axB.scatter(cap[m_pos], prof[m_pos], s=8, c=PROFIT, alpha=0.32, linewidths=0)
    axB.scatter(cap[m_neg], -prof[m_neg], s=8, c=LOSS, alpha=0.20, linewidths=0)
    axB.scatter(cap[m_cos], cost[m_cos], s=10, c=ACCENT, alpha=0.30, linewidths=0, marker="x")
    sp, ip = np.polyfit(np.log10(cap[m_pos]), np.log10(prof[m_pos]), 1)
    sc, ic = np.polyfit(np.log10(cap[m_cos]), np.log10(cost[m_cos]), 1)
    xx = np.logspace(-2, 2, 100)
    axB.plot(xx, 10 ** (sp * np.log10(xx) + ip), "-", color="#0d3a17", lw=2.4)
    axB.plot(xx, 10 ** (sc * np.log10(xx) + ic), "-", color="#7a3a08", lw=2.4)
    axB.set_xscale("log")
    axB.set_yscale("log")
    axB.set_xlim(0.01, 50)
    axB.set_ylim(1e-7, 5)
    axB.set_xlabel(L["xlab_b"], fontsize=15)
    axB.set_ylabel(L["ylab_b"], fontsize=15)
    axB.set_title(L["title_b"], loc="left", pad=4, fontsize=18)
    axB.text(0.04, 0.95, f"slope={sp:+.2f}", color="#0d3a17", fontsize=14,
             transform=axB.transAxes, fontweight="bold")
    axB.text(0.04, 0.86, f"slope={sc:+.2f}", color="#7a3a08", fontsize=14,
             transform=axB.transAxes, fontweight="bold")
    leg = [Line2D([0], [0], marker="o", ls="", color=PROFIT, markersize=9, label=L["leg_p"]),
           Line2D([0], [0], marker="o", ls="", color=LOSS, markersize=9, label=L["leg_l"]),
           Line2D([0], [0], marker="x", ls="", color=ACCENT, markersize=9, label=L["leg_c"])]
    axB.legend(handles=leg, loc="lower right", framealpha=0.95, fontsize=12,
               handletextpad=0.4, borderpad=0.3)
    axB.grid(True, which="both", alpha=0.18)

    # (C) Lorenz
    axC = fig.add_subplot(gs[1, 0])
    atk = Counter(d["attacker"] for d in D)
    counts = sorted(atk.values(), reverse=True)
    cumshare = np.cumsum(counts) / sum(counts)
    xshare = np.arange(1, len(counts) + 1) / len(counts)
    axC.plot(xshare * 100, cumshare * 100, color=NEUTRAL, lw=2.6)
    axC.fill_between(xshare * 100, cumshare * 100, color=NEUTRAL, alpha=0.12)
    axC.plot([0, 100], [0, 100], "k--", lw=0.8)
    idx = max(0, int(len(counts) * 10 / 100) - 1)
    axC.scatter([10], [cumshare[idx] * 100], color=ACCENT, s=110, zorder=5,
                edgecolor="white", lw=1.5)
    axC.text(12, cumshare[idx] * 100 - 3, f"{cumshare[idx]*100:.0f}%",
             fontsize=15, color="#7a3a08", fontweight="bold")

    def gini(c):
        c = np.sort(np.array(c))
        n = len(c)
        x = np.arange(1, n + 1)
        return (2 * np.sum(x * c) - (n + 1) * np.sum(c)) / (n * np.sum(c))

    G = gini(counts)
    axC.set_xlim(0, 100)
    axC.set_ylim(0, 100)
    axC.set_xlabel(L["xlab_c"], fontsize=15)
    axC.set_ylabel(L["ylab_c"], fontsize=15)
    axC.set_title(L["title_c"] + f"  (Gini={G:.2f})", loc="left", pad=4, fontsize=17)
    axC.grid(True, alpha=0.2)

    # (D) sym x profit hexbin
    axD = fig.add_subplot(gs[1, 1])
    sym_arr = np.array([d["sym"] for d in S])
    pr_arr = np.array([d["profit_sol"] for d in S])
    clip = 0.4
    pr_clip = np.clip(pr_arr, -clip, clip)
    hb = axD.hexbin(sym_arr, pr_clip, gridsize=(28, 22), cmap="magma_r",
                    mincnt=1, extent=(0, 0.10, -clip, clip))
    axD.axhline(0, color="white", lw=1.2, alpha=0.7)
    bins = np.linspace(0, 0.10, 11)
    bidx = np.digitize(sym_arr, bins)
    bx, by, byerr = [], [], []
    for k in range(1, len(bins)):
        sel = bidx == k
        if sel.sum() > 30:
            bx.append((bins[k - 1] + bins[k]) / 2)
            by.append(pr_arr[sel].mean())
            byerr.append(pr_arr[sel].std() / np.sqrt(sel.sum()))
    axD.errorbar(bx, by, yerr=byerr, fmt="o-", color="white", mfc="white",
                 mec="#0d3a17", lw=2, ms=9, capsize=3,
                 ecolor="white", elinewidth=1.4, label=L["binmean"])
    axD.plot(bx, by, "o-", color="#0d3a17", lw=1.6, ms=6, alpha=0.9, label="_nolegend_")
    cb = fig.colorbar(hb, ax=axD, shrink=0.85, pad=0.02)
    cb.set_label("# bundles", fontsize=13)
    cb.ax.tick_params(labelsize=12)
    axD.set_xlim(0, 0.10)
    axD.set_ylim(-clip, clip)
    axD.set_xlabel(L["xlab_d"], fontsize=15)
    axD.set_ylabel(L["ylab_d"], fontsize=15)
    axD.set_title(L["title_d"], loc="left", pad=4, fontsize=18)
    axD.legend(loc="upper right", framealpha=0.95, facecolor="white", fontsize=12)

    out = FIG_DIR / "sandwich_anatomy_col_en.pdf"
    fig.savefig(out, dpi=200)
    plt.close(fig)
    print(f"  -> {out}  ({os.path.getsize(out)/1024:.1f} KB)")


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    strongs, bm = load()
    D = [per_record(r, bm) for r in strongs]
    S = [d for d in D if d["capital"]]
    print(f"loaded {len(strongs):,} strong records, {len(S):,} with SOL capital")
    make_fig(D, S)


if __name__ == "__main__":
    main()
