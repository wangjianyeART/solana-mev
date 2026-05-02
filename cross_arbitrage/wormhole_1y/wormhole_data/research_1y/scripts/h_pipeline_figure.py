#!/usr/bin/env python3
"""Render a data-pipeline flow diagram for §4.1."""

from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import common_loader as cl

plt.rcParams.update(cl.MPL_STYLE)

FIGS = cl.FIGS
P = cl.PALETTE

fig, ax = plt.subplots(figsize=(11.5, 4.5))
ax.set_xlim(0, 10)
ax.set_ylim(0, 5.2)
ax.axis("off")

def box(x, y, w, h, text, color):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle="round,pad=0.02,rounding_size=0.12",
                       linewidth=1.1, edgecolor="black", facecolor=color)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=8.5)

def arrow(x1, y1, x2, y2, label=None):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                                 arrowstyle="->",
                                 mutation_scale=14,
                                 lw=1.1, color="#333"))
    if label:
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2
        ax.text(mx, my + 0.13, label, ha="center", va="bottom",
                fontsize=7.3, color="#555")

# stage 1: source
box(0.15, 3.6, 1.9, 1.0,
    "Wormhole API\n/operations\n(365 days)", "#FFF7E6")
box(0.15, 2.1, 1.9, 1.0,
    "Solana & Ethereum\nportal contract logs", "#E8F1FF")
box(0.15, 0.6, 1.9, 1.0,
    "Birdeye 1m /\nDeFiLlama 1m\nprice grids", "#EAF8EF")

# stage 2: matching
box(2.7, 2.5, 1.95, 1.4,
    "Pair matching\nsol_sig ↔ eth_hash\n(VAA chainId)", "#FFFFFF")

# stage 3: address / context
box(5.3, 3.6, 1.95, 1.0,
    "Extract actors\n(sol_addr, eth_addr)", "#FFFFFF")
box(5.3, 2.1, 1.95, 1.0,
    "Helius getTx\n+ Etherscan\n→ swap legs", "#FFFFFF")
box(5.3, 0.6, 1.95, 1.0,
    "Per-token price\nsharding (165\nfiles, LRU 32)", "#FFFFFF")

# stage 4: PnL + feature frame
box(7.85, 3.0, 2.0, 1.4,
    "PnL reconstruction\nentry_cost, exit_value,\ngross/net_pnl, ROI",
    "#FFE8E8")
box(7.85, 1.1, 2.0, 1.4,
    "Feature frame\n(17 dims) for\nrisk-model validation",
    "#E8FFE8")

# arrows
arrow(2.05, 4.1, 2.7, 3.6)
arrow(2.05, 2.6, 2.7, 3.2)
arrow(2.05, 1.1, 5.3, 1.1, label="price lookup")
arrow(4.65, 3.5, 5.3, 4.1, label="actors")
arrow(4.65, 2.8, 5.3, 2.6, label="swaps")
arrow(7.25, 4.1, 7.85, 3.7, label="actors + swaps")
arrow(7.25, 2.6, 7.85, 3.5)
arrow(7.25, 1.1, 7.85, 2.0, label="prices")
arrow(9.85, 3.3, 9.85, 2.5)   # top box -> bottom box

# stage labels
ax.text(1.1, 5.0, "Raw sources", ha="center",
        fontweight="bold", fontsize=9, color="#444")
ax.text(3.68, 4.1, "Pair matching", ha="center",
        fontweight="bold", fontsize=9, color="#444")
ax.text(6.28, 5.0, "Enrichment", ha="center",
        fontweight="bold", fontsize=9, color="#444")
ax.text(8.85, 5.0, "Output artefacts", ha="center",
        fontweight="bold", fontsize=9, color="#444")

fig.suptitle("Figure 9  —  Data-collection & processing pipeline for the "
             "1-year Wormhole Portal study", y=0.99, fontsize=10)
fig.tight_layout()
out = FIGS / "09_pipeline.png"
fig.savefig(out, dpi=300, bbox_inches="tight")
plt.close(fig)
print(f"[saved] {out}")
