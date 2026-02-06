#!/usr/bin/env python3
"""
Plot volatility vs liquidation (success/fail) scatter chart
"""

import json
import matplotlib.pyplot as plt
import numpy as np

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract data
volatility = []
liquidation_success = []
liquidation_fail = []
categories = []

for slot_range, info in data.items():
    volatility.append(info['volatility'])
    liquidation_success.append(info['liquidation_success'])
    liquidation_fail.append(info['liquidation_fail'])
    categories.append(info['category'])

# Create figure
fig, ax = plt.subplots(figsize=(12, 8))

# Plot liquidation success and fail
ax.scatter(volatility, liquidation_success, c='green',
           label='Liquidation Success', alpha=0.6, s=50)
ax.scatter(volatility, liquidation_fail, c='red',
           label='Liquidation Fail', alpha=0.6, s=50)

# Set labels and title
ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Liquidation Count', fontsize=12)
ax.set_title('Volatility vs Liquidation (Success/Fail)', fontsize=14)
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

# Save figure
plt.tight_layout()
plt.savefig('volatility_vs_liquidation.png', dpi=150)
print('Figure saved as volatility_vs_liquidation.png')
plt.show()
