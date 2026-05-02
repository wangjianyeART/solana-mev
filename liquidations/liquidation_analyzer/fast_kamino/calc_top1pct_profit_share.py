#!/usr/bin/env python3
"""Calculate the share of total profit held by the top 1% of transactions (ranked by profit descending)."""
import json
import math
import os


def main():
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    json_path = os.path.join(
        data_dir, "liquidation_analysis_20260204_013258.json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    liquidations = data.get("liquidations", [])
    profits = [float(l["net_profit_usd"])
               for l in liquidations if "net_profit_usd" in l]
    total = sum(profits)
    n = len(profits)
    if n == 0 or total <= 0:
        print("No valid profit data or total profit <= 0")
        return

    # Sort by profit descending, take top 1% of transactions (at least 1)
    k = max(1, math.ceil(n * 0.01))
    sorted_profits = sorted(profits, reverse=True)
    top_profits = sorted_profits[:k]
    top_sum = sum(top_profits)
    pct_of_total = 100 * top_sum / total

    print("=== Top 1% Profit (by transaction count) Share of Total Profit ===\n")
    print("Total transactions: {}, top 1% count: {}".format(n, k))
    print("Total net profit: {:.4f} USD".format(total))
    print("Sum of top {} transactions' profit: {:.4f} USD".format(k, top_sum))
    print("Share: {:.2f}%".format(pct_of_total))


if __name__ == "__main__":
    main()
