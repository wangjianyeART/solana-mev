#!/usr/bin/env python3
"""
Calculate average transaction volume by volatility category
"""

import json

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Aggregate by category
high_tx_volumes = []
low_tx_volumes = []
random_tx_volumes = []

high_volatilities = []
low_volatilities = []
random_volatilities = []

for slot_range, info in data.items():
    total_tx = info['success_tx'] + info['fail_tx']
    volatility = info.get('volatility', 0)

    if info['category'] == 'high':
        high_tx_volumes.append(total_tx)
        high_volatilities.append(volatility)
    elif info['category'] == 'low':
        low_tx_volumes.append(total_tx)
        low_volatilities.append(volatility)
    elif info['category'] == 'random':
        random_tx_volumes.append(total_tx)
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
print("Transaction Volume Statistics by Volatility Category")
print("=" * 60)

print(f"\nHigh Volatility (data points: {len(high_tx_volumes)}):")
print(f"  Total volume:    {sum(high_tx_volumes):,}")
print(f"  Avg volume:      {calc_mean(high_tx_volumes):,.2f}")
print(f"  Median:          {calc_median(high_tx_volumes):,.2f}")
print(f"  Std dev:         {calc_std(high_tx_volumes):,.2f}")
print(f"  Min:             {min(high_tx_volumes):,}")
print(f"  Max:             {max(high_tx_volumes):,}")
print(f"  Avg volatility:  {calc_mean(high_volatilities):.6f}")

print(f"\nRandom (data points: {len(random_tx_volumes)}):")
print(f"  Total volume:    {sum(random_tx_volumes):,}")
print(f"  Avg volume:      {calc_mean(random_tx_volumes):,.2f}")
print(f"  Median:          {calc_median(random_tx_volumes):,.2f}")
print(f"  Std dev:         {calc_std(random_tx_volumes):,.2f}")
print(f"  Min:             {min(random_tx_volumes):,}")
print(f"  Max:             {max(random_tx_volumes):,}")
print(f"  Avg volatility:  {calc_mean(random_volatilities):.6f}")

print(f"\nLow Volatility (data points: {len(low_tx_volumes)}):")
print(f"  Total volume:    {sum(low_tx_volumes):,}")
print(f"  Avg volume:      {calc_mean(low_tx_volumes):,.2f}")
print(f"  Median:          {calc_median(low_tx_volumes):,.2f}")
print(f"  Std dev:         {calc_std(low_tx_volumes):,.2f}")
print(f"  Min:             {min(low_tx_volumes):,}")
print(f"  Max:             {max(low_tx_volumes):,}")
print(f"  Avg volatility:  {calc_mean(low_volatilities):.6f}")

print("\n" + "=" * 60)
print("Comparative Analysis")
print("=" * 60)

high_avg = calc_mean(high_tx_volumes)
low_avg = calc_mean(low_tx_volumes)
random_avg = calc_mean(random_tx_volumes)

print(
    f"\nHigh vs Low avg volume ratio: {high_avg/low_avg:.2f}x" if low_avg > 0 else "N/A")
print(
    f"High vs Random avg volume ratio: {high_avg/random_avg:.2f}x" if random_avg > 0 else "N/A")
print(
    f"Random vs Low avg volume ratio: {random_avg/low_avg:.2f}x" if low_avg > 0 else "N/A")

print(f"\nAvg volume ranking: ", end="")
volumes = [
    ('High', high_avg),
    ('Random', random_avg),
    ('Low', low_avg)
]
sorted_volumes = sorted(volumes, key=lambda x: x[1], reverse=True)
print(" > ".join([f"{name} ({vol:,.0f})" for name, vol in sorted_volumes]))

print(f"\nTotal volume ranking: ", end="")
total_volumes = [
    ('High', sum(high_tx_volumes)),
    ('Random', sum(random_tx_volumes)),
    ('Low', sum(low_tx_volumes))
]
sorted_total = sorted(total_volumes, key=lambda x: x[1], reverse=True)
print(" > ".join([f"{name} ({vol:,})" for name, vol in sorted_total]))
