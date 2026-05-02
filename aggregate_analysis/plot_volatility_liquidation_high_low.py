#!/usr/bin/env python3
"""
Plot volatility vs liquidation total rate scatter chart (high and low data only)
"""

import json
import matplotlib.pyplot as plt
import numpy as np

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract data (high and low only)
volatility_high = []
liquidation_rate_high = []
volatility_low = []
liquidation_rate_low = []

for slot_range, info in data.items():
    total_tx = info['success_tx'] + info['fail_tx']
    if total_tx == 0:
        continue
    rate = (info['liquidation_success'] + info['liquidation_fail']) / total_tx

    if info['category'] == 'high':
        volatility_high.append(info['volatility'])
        liquidation_rate_high.append(rate)
    elif info['category'] == 'low':
        volatility_low.append(info['volatility'])
        liquidation_rate_low.append(rate)

print(f'High data points: {len(volatility_high)}')
print(f'Low data points: {len(volatility_low)}')

# Create figure
fig, ax = plt.subplots(figsize=(12, 8))

# Plot scatter chart
ax.scatter(volatility_high, liquidation_rate_high, c='red',
           label='High Volatility', alpha=0.6, s=50)
ax.scatter(volatility_low, liquidation_rate_low, c='blue',
           label='Low Volatility', alpha=0.6, s=50)

# Set labels and title
ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Liquidation Rate', fontsize=12)
ax.set_title('Volatility vs Liquidation Rate (High & Low Only)', fontsize=14)
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

# Save figure
plt.tight_layout()
plt.savefig('volatility_vs_liquidation_high_low.png', dpi=150)
print('Figure saved as volatility_vs_liquidation_high_low.png')
plt.show()
