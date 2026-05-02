#!/usr/bin/env python3
"""
Calculate the total ETH transaction count (sum of Value column) from export-TxGrowth.csv
"""

import csv

INPUT_FILE = 'export-TxGrowth.csv'

total = 0
row_count = 0

with open(INPUT_FILE, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            value = int(row['Value'])
            total += value
            row_count += 1
        except (ValueError, KeyError) as e:
            print(f"Skipping row: {row} -> {e}")

print("=" * 50)
print("ETH Transaction Count Statistics (export-TxGrowth.csv)")
print("=" * 50)
print(f"Data rows (days): {row_count}")
print(f"Total transactions: {total:,}")
print("=" * 50)
