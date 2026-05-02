#!/usr/bin/env python3
"""
Plot volatility vs sandwich attack count scatter chart (high, low, random)
"""

import json
import matplotlib.pyplot as plt
import numpy as np

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Extract data
volatility_random = []
sandwich_count_random = []
volatility_low = []
sandwich_count_low = []
volatility_high = []
sandwich_count_high = []

for slot_range, info in data.items():
    sandwich_total = info['sandwich_success'] + info['sandwich_failed']

    if info['category'] == 'random':
        volatility_random.append(info['volatility'])
        sandwich_count_random.append(sandwich_total)
    elif info['category'] == 'low':
        volatility_low.append(info['volatility'])
        sandwich_count_low.append(sandwich_total)
    elif info['category'] == 'high':
        volatility_high.append(info['volatility'])
        sandwich_count_high.append(sandwich_total)

print(f'High data points: {len(volatility_high)}')
print(f'Random data points: {len(volatility_random)}')
print(f'Low data points: {len(volatility_low)}')

# Create figure
fig, ax = plt.subplots(figsize=(12, 8))

# Plot scatter chart
ax.scatter(volatility_high, sandwich_count_high, c='red',
           label='High Volatility', alpha=0.6, s=50)
ax.scatter(volatility_random, sandwich_count_random,
           c='green', label='Random', alpha=0.6, s=50)
ax.scatter(volatility_low, sandwich_count_low, c='blue',
           label='Low Volatility', alpha=0.6, s=50)

# Set labels and title
ax.set_xlabel('Volatility', fontsize=12)
ax.set_ylabel('Sandwich Attack Count', fontsize=12)
ax.set_title(
    'Volatility vs Sandwich Attack Count (High, Random & Low)', fontsize=14)
ax.legend(loc='upper right')
ax.grid(True, alpha=0.3)

# Save figure
plt.tight_layout()
plt.savefig('volatility_vs_sandwich_count_all.png', dpi=150)
print('Figure saved as volatility_vs_sandwich_count_all.png')
plt.show()
