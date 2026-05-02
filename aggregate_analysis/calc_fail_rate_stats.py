#!/usr/bin/env python3
"""
Calculate average transaction failure rate by volatility category
"""

import json

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Aggregate by category
high_fail_rates = []
low_fail_rates = []
random_fail_rates = []

high_volatilities = []
low_volatilities = []
random_volatilities = []

for slot_range, info in data.items():
    success_tx = info['success_tx']
    fail_tx = info['fail_tx']
    total_tx = success_tx + fail_tx

    if total_tx == 0:
        continue

    fail_rate = fail_tx / total_tx
    volatility = info.get('volatility', 0)

    if info['category'] == 'high':
        high_fail_rates.append(fail_rate)
        high_volatilities.append(volatility)
    elif info['category'] == 'low':
        low_fail_rates.append(fail_rate)
        low_volatilities.append(volatility)
    elif info['category'] == 'random':
        random_fail_rates.append(fail_rate)
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


print("=" * 60)
print("Transaction Failure Rate Statistics by Volatility Category")
print("=" * 60)

print(f"\nHigh Volatility (data points: {len(high_fail_rates)}):")
print(
    f"  Avg failure rate: {calc_mean(high_fail_rates):.4f} ({calc_mean(high_fail_rates)*100:.2f}%)")
print(
    f"  Median:           {calc_median(high_fail_rates):.4f} ({calc_median(high_fail_rates)*100:.2f}%)")
print(f"  Std dev:          {calc_std(high_fail_rates):.4f}")
print(
    f"  Min:              {min(high_fail_rates):.4f} ({min(high_fail_rates)*100:.2f}%)")
print(
    f"  Max:              {max(high_fail_rates):.4f} ({max(high_fail_rates)*100:.2f}%)")
print(f"  Avg volatility:   {calc_mean(high_volatilities):.6f}")

print(f"\nRandom (data points: {len(random_fail_rates)}):")
print(
    f"  Avg failure rate: {calc_mean(random_fail_rates):.4f} ({calc_mean(random_fail_rates)*100:.2f}%)")
print(
    f"  Median:           {calc_median(random_fail_rates):.4f} ({calc_median(random_fail_rates)*100:.2f}%)")
print(f"  Std dev:          {calc_std(random_fail_rates):.4f}")
print(
    f"  Min:              {min(random_fail_rates):.4f} ({min(random_fail_rates)*100:.2f}%)")
print(
    f"  Max:              {max(random_fail_rates):.4f} ({max(random_fail_rates)*100:.2f}%)")
print(f"  Avg volatility:   {calc_mean(random_volatilities):.6f}")

print(f"\nLow Volatility (data points: {len(low_fail_rates)}):")
print(
    f"  Avg failure rate: {calc_mean(low_fail_rates):.4f} ({calc_mean(low_fail_rates)*100:.2f}%)")
print(
    f"  Median:           {calc_median(low_fail_rates):.4f} ({calc_median(low_fail_rates)*100:.2f}%)")
print(f"  Std dev:          {calc_std(low_fail_rates):.4f}")
print(
    f"  Min:              {min(low_fail_rates):.4f} ({min(low_fail_rates)*100:.2f}%)")
print(
    f"  Max:              {max(low_fail_rates):.4f} ({max(low_fail_rates)*100:.2f}%)")
print(f"  Avg volatility:   {calc_mean(low_volatilities):.6f}")

print("\n" + "=" * 60)
print("Comparative Analysis")
print("=" * 60)

high_avg = calc_mean(high_fail_rates)
low_avg = calc_mean(low_fail_rates)
random_avg = calc_mean(random_fail_rates)

print(
    f"\nHigh vs Low failure rate ratio: {high_avg/low_avg:.2f}x" if low_avg > 0 else "N/A")
print(
    f"High vs Random failure rate ratio: {high_avg/random_avg:.2f}x" if random_avg > 0 else "N/A")
print(
    f"Random vs Low failure rate ratio: {random_avg/low_avg:.2f}x" if low_avg > 0 else "N/A")

print(f"\nAvg failure rate ranking: ", end="")
rates = [
    ('High', high_avg),
    ('Random', random_avg),
    ('Low', low_avg)
]
sorted_rates = sorted(rates, key=lambda x: x[1], reverse=True)
print(" > ".join([f"{name} ({rate*100:.2f}%)" for name, rate in sorted_rates]))
