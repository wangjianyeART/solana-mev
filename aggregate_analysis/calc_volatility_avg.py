#!/usr/bin/env python3
"""
Calculate average volatility for the three categories
"""

import json

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Aggregate by category
high_volatilities = []
low_volatilities = []
random_volatilities = []

for slot_range, info in data.items():
    volatility = info.get('volatility', 0)

    if info['category'] == 'high':
        high_volatilities.append(volatility)
    elif info['category'] == 'low':
        low_volatilities.append(volatility)
    elif info['category'] == 'random':
        random_volatilities.append(volatility)


def calc_mean(lst):
    return sum(lst) / len(lst) if lst else 0


def calc_median(lst):
    if not lst:
        return 0
    sorted_lst = sorted(lst)
    n = len(sorted_lst)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_lst[mid-1] + sorted_lst[mid]) / 2
    else:
        return sorted_lst[mid]


def calc_std(lst):
    if not lst:
        return 0
    mean = calc_mean(lst)
    variance = sum((x - mean) ** 2 for x in lst) / len(lst)
    return variance ** 0.5


print("=" * 70)
print("Volatility Statistics for Three Categories")
print("=" * 70)

print(f"\nHigh Volatility (data points: {len(high_volatilities)}):")
print(
    f"  Avg volatility:  {calc_mean(high_volatilities):.6f} ({calc_mean(high_volatilities)*100:.4f}%)")
print(f"  Median:          {calc_median(high_volatilities):.6f}")
print(f"  Std dev:         {calc_std(high_volatilities):.6f}")
print(f"  Min:             {min(high_volatilities):.6f}")
print(f"  Max:             {max(high_volatilities):.6f}")

print(f"\nRandom (data points: {len(random_volatilities)}):")
print(
    f"  Avg volatility:  {calc_mean(random_volatilities):.6f} ({calc_mean(random_volatilities)*100:.4f}%)")
print(f"  Median:          {calc_median(random_volatilities):.6f}")
print(f"  Std dev:         {calc_std(random_volatilities):.6f}")
print(f"  Min:             {min(random_volatilities):.6f}")
print(f"  Max:             {max(random_volatilities):.6f}")

print(f"\nLow Volatility (data points: {len(low_volatilities)}):")
print(
    f"  Avg volatility:  {calc_mean(low_volatilities):.6f} ({calc_mean(low_volatilities)*100:.4f}%)")
print(f"  Median:          {calc_median(low_volatilities):.6f}")
print(f"  Std dev:         {calc_std(low_volatilities):.6f}")
print(f"  Min:             {min(low_volatilities):.6f}")
print(f"  Max:             {max(low_volatilities):.6f}")

print("\n" + "=" * 70)
print("Comparative Analysis")
print("=" * 70)

high_avg = calc_mean(high_volatilities)
low_avg = calc_mean(low_volatilities)
random_avg = calc_mean(random_volatilities)

print(
    f"\nHigh vs Low volatility ratio: {high_avg/low_avg:.2f}x" if low_avg > 0 else "N/A")
print(
    f"High vs Random volatility ratio: {high_avg/random_avg:.2f}x" if random_avg > 0 else "N/A")
print(
    f"Random vs Low volatility ratio: {random_avg/low_avg:.2f}x" if low_avg > 0 else "N/A")

print(
    f"\nVolatility ranking: High ({high_avg:.6f}) > Random ({random_avg:.6f}) > Low ({low_avg:.6f})")
