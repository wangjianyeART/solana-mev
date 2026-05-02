#!/usr/bin/env python3
"""Kamino liquidation analysis - box plot of net profit grouped by flash loan usage."""
import matplotlib.pyplot as plt
import json
import os

import matplotlib
matplotlib.use("Agg")


def main():
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    json_path = os.path.join(
        data_dir, "liquidation_analysis_20260204_013258.json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    liquidations = data.get("liquidations", [])
    with_flash = [float(l["net_profit_usd"]) for l in liquidations if l.get(
        "uses_flash_loan") and "net_profit_usd" in l]
    without_flash = [float(l["net_profit_usd"]) for l in liquidations if not l.get(
        "uses_flash_loan") and "net_profit_usd" in l]

    fig, ax = plt.subplots(figsize=(7, 5))
    bp = ax.boxplot(
        [with_flash, without_flash],
        labels=["Flash Loan", "No Flash Loan"],
        patch_artist=True,
        showfliers=False,
    )
    colors = ["#4A90D9", "#E8A838"]
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.8)
    ax.set_yscale("symlog", linthresh=0.1)
    ax.set_ylabel("Net Profit (USD, symlog)")
    ax.set_title("Kamino Liquidation Net Profit by Flash Loan (n_flash={}, n_no_flash={})".format(
        len(with_flash), len(without_flash)))
    plt.tight_layout()
    out_path = os.path.join(data_dir, "profit_boxplot_by_flash_loan.png")
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print("Saved:", out_path)
    plt.close()


if __name__ == "__main__":
    main()
