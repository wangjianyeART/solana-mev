#!/usr/bin/env python3
"""
Plot volatility vs liquidation total rate scatter chart
"""

import json
import matplotlib.pyplot as plt
import numpy as np

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract data
volatility = []
liquidation_rate = []
categories = []

for slot_range, info in data.items():
    total_tx = info['success_tx'] + info['fail_tx']
    if total_tx > 0:
        volatility.append(info['volatility'])
        rate = (info['liquidation_success'] +
                info['liquidation_fail']) / total_tx
        liquidation_rate.append(rate)
        categories.append(info['category'])

# Create figure
fig, ax = plt.subplots(figsize=(12, 8))

# Plot different colors by category
category_colors = {'high': 'red', 'low': 'blue', 'random': 'green'}
category_labels = {'high': 'High Volatility',
                   'low': 'Low Volatility', 'random': 'Random'}

for cat in ['high', 'low', 'random']:
    mask = [c == cat for c in categories]
    x = [v for v, m in zip(volatility, mask) if m]
    y = [r for r, m in zip(liquidation_rate, mask) if m]
    ax.scatter(x, y, c=category_colors[cat],
               label=category_labels[cat], alpha=0.6, s=50)

# Set labels and title
ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Liquidation Rate', fontsize=12)
ax.set_title('Volatility vs Liquidation Rate (Total)', fontsize=14)
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

# Save figure
plt.tight_layout()
plt.savefig('volatility_vs_liquidation_total_rate.png', dpi=150)
print('Figure saved as volatility_vs_liquidation_total_rate.png')
plt.show()
