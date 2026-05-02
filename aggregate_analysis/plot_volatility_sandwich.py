#!/usr/bin/env python3
"""
Plot volatility vs sandwich_success scatter chart
"""

import numpy as np
import matplotlib.pyplot as plt
import json
import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract data
volatility = []
sandwich_success = []
categories = []
symbols = []

for slot_range, info in data.items():
    volatility.append(info['volatility'])
    sandwich_success.append(info['sandwich_success'])
    categories.append(info['category'])
    symbols.append(info['symbol'])

# Create figure
fig, ax = plt.subplots(figsize=(12, 8))

# Plot different colors by category
category_colors = {'high': 'red', 'low': 'blue', 'random': 'green'}
category_labels = {'high': 'High Volatility',
                   'low': 'Low Volatility', 'random': 'Random'}

for cat in ['high', 'low', 'random']:
    mask = [c == cat for c in categories]
    x = [v for v, m in zip(volatility, mask) if m]
    y = [s for s, m in zip(sandwich_success, mask) if m]
    ax.scatter(x, y, c=category_colors[cat],
               label=category_labels[cat], alpha=0.6, s=50)

# Set labels and title
ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Sandwich Success Count', fontsize=12)
ax.set_title('Volatility vs Sandwich Success', fontsize=14)
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

# Save figure
plt.tight_layout()
plt.savefig('volatility_vs_sandwich.png', dpi=150)
print('Figure saved as volatility_vs_sandwich.png')
plt.close()
