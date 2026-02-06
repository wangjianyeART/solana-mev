#!/usr/bin/env python3
"""
Plot volatility vs liquidation rate (success/fail) scatter chart
"""

import json
import matplotlib.pyplot as plt
import numpy as np

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract data
volatility = []
liquidation_success_rate = []
liquidation_fail_rate = []

for slot_range, info in data.items():
    total_tx = info['success_tx'] + info['fail_tx']
    if total_tx > 0:
        volatility.append(info['volatility'])
        liquidation_success_rate.append(info['liquidation_success'] / total_tx)
        liquidation_fail_rate.append(info['liquidation_fail'] / total_tx)

# Create figure
fig, ax = plt.subplots(figsize=(12, 8))

# Plot liquidation success rate and fail rate
ax.scatter(volatility, liquidation_success_rate, c='green',
           label='Liquidation Success Rate', alpha=0.6, s=50)
ax.scatter(volatility, liquidation_fail_rate, c='red',
           label='Liquidation Fail Rate', alpha=0.6, s=50)

# Set labels and title
ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Liquidation Rate', fontsize=12)
ax.set_title('Volatility vs Liquidation Rate (Success/Fail)', fontsize=14)
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

# Save figure
plt.tight_layout()
plt.savefig('volatility_vs_liquidation_rate.png', dpi=150)
print('Figure saved as volatility_vs_liquidation_rate.png')
plt.show()
