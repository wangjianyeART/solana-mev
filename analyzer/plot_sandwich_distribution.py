#!/usr/bin/env python3
"""
Plot sandwich attack count boxplot (by high/low/random category)
"""

import json
import matplotlib.pyplot as plt
import numpy as np

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Aggregate sandwich attack count by category
counts_high = []
counts_low = []
counts_random = []

for slot_range, info in data.items():
    sandwich_total = info['sandwich_success'] + info['sandwich_failed']

    if info['category'] == 'high':
        counts_high.append(sandwich_total)
    elif info['category'] == 'low':
        counts_low.append(sandwich_total)
    elif info['category'] == 'random':
        counts_random.append(sandwich_total)

print(
    f'High data points: {len(counts_high)}, mean: {np.mean(counts_high):.2f}, median: {np.median(counts_high):.2f}')
print(
    f'Low data points: {len(counts_low)}, mean: {np.mean(counts_low):.2f}, median: {np.median(counts_low):.2f}')
print(
    f'Random data points: {len(counts_random)}, mean: {np.mean(counts_random):.2f}, median: {np.median(counts_random):.2f}')

# Create figure - boxplot
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

# Boxplot
box_data = [counts_high, counts_random, counts_low]
box_labels = ['High\nVolatility', 'Random', 'Low\nVolatility']
box_colors = ['red', 'green', 'blue']

bp = ax1.boxplot(box_data, labels=box_labels, patch_artist=True)
for patch, color in zip(bp['boxes'], box_colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.6)

ax1.set_ylabel('Sandwich Attack Count', fontsize=12)
ax1.set_title('Boxplot of Sandwich Attack Count by Category', fontsize=14)
ax1.grid(True, alpha=0.3, axis='y')

# Bar chart (mean + std dev)
means = [np.mean(counts_high), np.mean(counts_random), np.mean(counts_low)]
stds = [np.std(counts_high), np.std(counts_random), np.std(counts_low)]
x_pos = [0, 1, 2]

bars = ax2.bar(x_pos, means, yerr=stds, capsize=5,
               color=box_colors, alpha=0.6, edgecolor='black')
ax2.set_xticks(x_pos)
ax2.set_xticklabels(box_labels)
ax2.set_ylabel('Average Sandwich Attack Count', fontsize=12)
ax2.set_title('Mean ± Std of Sandwich Attack Count by Category', fontsize=14)
ax2.grid(True, alpha=0.3, axis='y')

# Display values on bar chart
for i, (mean, std) in enumerate(zip(means, stds)):
    ax2.text(i, mean + std + 1, f'{mean:.1f}', ha='center', fontsize=10)

# Save figure
plt.tight_layout()
plt.savefig('sandwich_count_boxplot.png', dpi=150)
print('Figure saved as sandwich_count_boxplot.png')
plt.show()
