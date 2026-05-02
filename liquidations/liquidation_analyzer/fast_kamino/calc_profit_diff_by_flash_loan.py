#!/usr/bin/env python3
"""Calculate the mean, median, and distribution of net profit for flash loan vs non-flash loan liquidations."""
import json
import os


def quartiles(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None, None, None
    q1_i = (n - 1) * 0.25
    q2_i = (n - 1) * 0.5
    q3_i = (n - 1) * 0.75

    def lerp(arr, i):
        lo = int(i)
        hi = min(lo + 1, n - 1)
        t = i - lo
        return arr[lo] * (1 - t) + arr[hi] * t
    return lerp(xs, q1_i), lerp(xs, q2_i), lerp(xs, q3_i)


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

    n_flash, n_no = len(with_flash), len(without_flash)
    mean_flash = sum(with_flash) / n_flash if n_flash else 0
    mean_no_flash = sum(without_flash) / n_no if n_no else 0
    diff = mean_flash - mean_no_flash

    neg_flash = sum(1 for x in with_flash if x < 0)
    neg_no = sum(1 for x in without_flash if x < 0)
    q1_f, med_f, q3_f = quartiles(with_flash)
    q1_n, med_n, q3_n = quartiles(without_flash)
    min_f, max_f = min(with_flash), max(with_flash)
    min_n, max_n = min(without_flash), max(without_flash)

    print("=== Flash Loan vs Non-Flash Loan Net Profit (USD) ===\n")
    print("With flash loan:     n = {}, mean = {:.4f}, median = {:.4f}".format(
        n_flash, mean_flash, med_f))
    print("  Negative profit count = {}, Q1 = {:.4f}, Q3 = {:.4f}, min = {:.4f}, max = {:.4f}".format(
        neg_flash, q1_f, q3_f, min_f, max_f))
    print("Without flash loan:  n = {}, mean = {:.4f}, median = {:.4f}".format(
        n_no, mean_no_flash, med_n))
    print("  Negative profit count = {}, Q1 = {:.4f}, Q3 = {:.4f}, min = {:.4f}, max = {:.4f}".format(
        neg_no, q1_n, q3_n, min_n, max_n))
    print("\nDifference (with - without): mean diff = {:.4f} USD, median diff = {:.4f} USD".format(
        diff, med_f - med_n))
    print("\nNote: The non-flash-loan 'mean' is skewed upward by a few extremely high-profit outliers;")
    print("  the median and box lower bound better reflect the typical case for most transactions.")
    print("  Many negative profits indicate that a significant number of liquidations are unprofitable after costs.")


if __name__ == "__main__":
    main()
