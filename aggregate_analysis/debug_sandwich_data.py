#!/usr/bin/env python3
"""
Debug sandwich attack data
"""

import json

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Aggregate by category
stats = {
    'high': {'total_sandwich': 0, 'total_tx': 0, 'count': 0, 'rates': []},
    'low': {'total_sandwich': 0, 'total_tx': 0, 'count': 0, 'rates': []},
    'random': {'total_sandwich': 0, 'total_tx': 0, 'count': 0, 'rates': []}
}

for slot_range, info in data.items():
    category = info['category']
    total_tx = info['success_tx'] + info['fail_tx']
    sandwich_total = info['sandwich_success'] + info['sandwich_failed']

    if total_tx == 0:
        continue

    rate = sandwich_total / total_tx * 100

    stats[category]['total_sandwich'] += sandwich_total
    stats[category]['total_tx'] += total_tx
    stats[category]['count'] += 1
    stats[category]['rates'].append(rate)

print("=" * 70)
print("Sandwich Attack Data Analysis")
print("=" * 70)

for category in ['high', 'random', 'low']:
    s = stats[category]

    # Method 1: Overall attack rate (total attacks / total transactions)
    overall_rate = (s['total_sandwich'] / s['total_tx']
                    * 100) if s['total_tx'] > 0 else 0

    # Method 2: Average attack rate (mean of per-period attack rates)
    avg_rate = sum(s['rates']) / len(s['rates']) if s['rates'] else 0

    print(f"\n{category.upper()}:")
    print(f"  Data points: {s['count']}")
    print(f"  Total sandwich attacks: {s['total_sandwich']}")
    print(f"  Total transactions: {s['total_tx']:,}")
    print(f"  Method 1 - Overall attack rate: {overall_rate:.6f}%")
    print(f"  Method 2 - Average attack rate: {avg_rate:.6f}%")
    print(f"  Difference: {abs(overall_rate - avg_rate):.6f}%")

print("\n" + "=" * 70)
print("Comparative Analysis")
print("=" * 70)

# Using Method 1 (overall attack rate)
high_overall = (stats['high']['total_sandwich'] / stats['high']
                ['total_tx'] * 100) if stats['high']['total_tx'] > 0 else 0
random_overall = (stats['random']['total_sandwich'] / stats['random']
                  ['total_tx'] * 100) if stats['random']['total_tx'] > 0 else 0
low_overall = (stats['low']['total_sandwich'] / stats['low']
               ['total_tx'] * 100) if stats['low']['total_tx'] > 0 else 0

print(f"\nOverall attack rate ranking:")
print(f"  High: {high_overall:.6f}%")
print(f"  Random: {random_overall:.6f}%")
print(f"  Low: {low_overall:.6f}%")

# Using Method 2 (average attack rate)
high_avg = sum(stats['high']['rates']) / len(stats['high']
                                             ['rates']) if stats['high']['rates'] else 0
random_avg = sum(stats['random']['rates']) / len(stats['random']
                                                 ['rates']) if stats['random']['rates'] else 0
low_avg = sum(stats['low']['rates']) / len(stats['low']
                                           ['rates']) if stats['low']['rates'] else 0

print(f"\nAverage attack rate ranking:")
print(f"  High: {high_avg:.6f}%")
print(f"  Random: {random_avg:.6f}%")
print(f"  Low: {low_avg:.6f}%")
