#!/usr/bin/env python3
"""
X-axis: Volatility
Y-axis: Sandwich attack rate
Random category data only
"""

import json
import matplotlib.pyplot as plt

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

volatility = []
sandwich_rate = []

for slot_range, info in data.items():
    if info['category'] != 'random':
        continue

    total_tx = info['success_tx'] + info['fail_tx']
    if total_tx == 0:
        continue

    vol = info['volatility']
    rate = (info['sandwich_success'] +
            info['sandwich_failed']) / total_tx * 100

    volatility.append(vol)
    sandwich_rate.append(rate)

# Plot
fig, ax = plt.subplots(figsize=(10, 6))

ax.scatter(volatility, sandwich_rate, c='green',
           alpha=0.6, s=50, label='Random')

ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Sandwich Attack Rate (%)', fontsize=12)
ax.set_title('Volatility vs Sandwich Attack Rate (Random Only)', fontsize=14)
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('volatility_sandwich_rate_random.png', dpi=150)
print(f'Data points: {len(volatility)}')
print(f'Figure saved as volatility_sandwich_rate_random.png')

plt.show()
