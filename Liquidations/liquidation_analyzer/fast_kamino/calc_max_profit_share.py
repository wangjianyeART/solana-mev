#!/usr/bin/env python3
"""Calculate the share of the single largest net profit relative to the total net profit."""
import json
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
    max_profit = max(profits)
    max_sig = next(l["signature"] for l in liquidations if float(
        l.get("net_profit_usd", 0)) == max_profit)

    if total <= 0:
        print("Total net profit <= 0, cannot calculate share")
        return
    pct = 100 * max_profit / total

    print("=== Largest Single Profit Share of Total Profit ===\n")
    print("Total net profit (sum net_profit_usd): {:.4f} USD".format(total))
    print("Largest single net profit: {:.4f} USD".format(max_profit))
    print("Share: {:.2f}%".format(pct))
    print("Corresponding signature: {}...".format(max_sig[:32]))


if __name__ == "__main__":
    main()
