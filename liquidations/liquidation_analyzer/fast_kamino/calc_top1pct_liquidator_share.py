#!/usr/bin/env python3
"""Calculate the share of total liquidation count held by the top X% of liquidators (by headcount)."""
import json
import math
import os


def main():
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    json_path = os.path.join(
        data_dir, "liquidation_analysis_20260204_013258.json")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    liquidators = data.get("liquidators", {})  # address -> count
    total = data.get("metadata", {}).get(
        "total_liquidations") or sum(liquidators.values())
    n_liquidators = len(liquidators)
    if n_liquidators == 0 or total == 0:
        print("No liquidator data or total liquidation count is 0")
        return

    sorted_items = sorted(liquidators.items(),
                          key=lambda x: x[1], reverse=True)

    for pct_pct in (1, 5):
        k = max(1, math.ceil(n_liquidators * pct_pct / 100))
        top_items = sorted_items[:k]
        top_count = sum(c for _, c in top_items)
        pct = 100 * top_count / total
        print("=== Top {}% of Liquidators (by headcount) Share of Total Liquidations ===\n".format(pct_pct))
        print("Total liquidators: {}, top {}% count: {}".format(n_liquidators, pct_pct, k))
        print("Total liquidation count: {}".format(total))
        print("Sum of liquidations by top {} liquidators: {}".format(k, top_count))
        print("Share: {:.2f}%\n".format(pct))
        for addr, cnt in top_items:
            print("  {}  {} times".format(addr[:20] + "...", cnt))
        print()


if __name__ == "__main__":
    main()
