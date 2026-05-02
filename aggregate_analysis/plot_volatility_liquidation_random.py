#!/usr/bin/env python3
"""
Plot volatility vs liquidation total rate scatter chart (random data only)
"""

import json
import matplotlib.pyplot as plt
import numpy as np

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract data (random only)
volatility = []
liquidation_rate = []

for slot_range, info in data.items():
    if info['category'] != 'random':
        continue
    total_tx = info['success_tx'] + info['fail_tx']
    if total_tx > 0:
        volatility.append(info['volatility'])
        rate = (info['liquidation_success'] +
                info['liquidation_fail']) / total_tx
        liquidation_rate.append(rate)

print(f'Random data points: {len(volatility)}')

# Create figure
fig, ax = plt.subplots(figsize=(12, 8))

# Plot scatter chart
ax.scatter(volatility, liquidation_rate, c='green', alpha=0.6, s=50)

# Set labels and title
ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Liquidation Rate', fontsize=12)
ax.set_title('Volatility vs Liquidation Rate (Random Only)', fontsize=14)
ax.grid(True, alpha=0.3)

# Save figure
plt.tight_layout()
plt.savefig('volatility_vs_liquidation_random.png', dpi=150)
print('Figure saved as volatility_vs_liquidation_random.png')
plt.show()
