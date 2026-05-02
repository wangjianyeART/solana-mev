#!/usr/bin/env python3
"""
Calculate the average daily transaction count for ETH over the last two years from export-TxGrowth.csv
"""

import csv
from datetime import datetime

INPUT_FILE = 'export-TxGrowth.csv'
SECONDS_PER_DAY = 86400
DAYS_PER_TWO_YEARS = 365 * 2  # 730 days

rows = []
with open(INPUT_FILE, 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            ts = int(row['UnixTimeStamp'])
            value = int(row['Value'])
            rows.append((ts, value))
        except (ValueError, KeyError):
            pass

if not rows:
    print("No valid data")
    exit(1)

# Sort by timestamp and extract the last two years of data
rows.sort(key=lambda x: x[0])
max_ts = rows[-1][0]
cutoff_ts = max_ts - DAYS_PER_TWO_YEARS * SECONDS_PER_DAY

last_two_years = [(ts, val) for ts, val in rows if ts >= cutoff_ts]
total_tx = sum(val for _, val in last_two_years)
days = len(last_two_years)
avg_per_day = total_tx / days if days else 0

# Date range (for display)
start_dt = datetime.utcfromtimestamp(last_two_years[0][0])
end_dt = datetime.utcfromtimestamp(last_two_years[-1][0])

print("=" * 55)
print("ETH Average Daily Transactions Over Last 2 Years (export-TxGrowth.csv)")
print("=" * 55)
print(
    f"Period: {start_dt.strftime('%Y-%m-%d')} ~ {end_dt.strftime('%Y-%m-%d')} (UTC)")
print(f"Days: {days}")
print(f"Total transactions over 2 years: {total_tx:,}")
print(f"Average daily transactions: {avg_per_day:,.0f}")
print("=" * 55)
