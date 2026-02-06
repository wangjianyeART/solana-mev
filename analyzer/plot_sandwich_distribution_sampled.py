#!/usr/bin/env python3
"""
Left: Sandwich attack MEV density bar chart (High/Random/Low)
Right: Sandwich attack rate cumulative distribution (x-axis: sandwich_total/total_tx, y-axis: cumulative count)
90 data points randomly sampled per category, without replacement (no duplicate points within each category).
"""

import json
import random
import matplotlib.pyplot as plt
import numpy as np

# Set random seed for reproducibility
random.seed(42)

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

rates_high = []
rates_low = []
rates_random = []

for slot_range, info in data.items():
    sandwich_total = info['sandwich_success'] + info['sandwich_failed']
    total_tx = info['success_tx'] + info['fail_tx']

    if total_tx == 0:
        continue

    rate = sandwich_total / total_tx * 100  # percentage

    if info['category'] == 'high':
        rates_high.append(rate)
    elif info['category'] == 'low':
        rates_low.append(rate)
    elif info['category'] == 'random':
        rates_random.append(rate)

print(
    f"Original data points: High={len(rates_high)}, Low={len(rates_low)}, Random={len(rates_random)}")

# Randomly sample 90 points per category, without replacement (no duplicates within each category)
target_size = 90


def sample_nodup(rates, n):
    """Randomly sample n items from list without replacement; keep all if fewer than n."""
    if len(rates) <= n:
        return list(rates)
    return random.sample(rates, n)


rates_high_sampled = sample_nodup(rates_high, target_size)
rates_low_sampled = sample_nodup(rates_low, target_size)
rates_random_sampled = sample_nodup(rates_random, target_size)

print(
    f"Sampled data points: High={len(rates_high_sampled)}, Low={len(rates_low_sampled)}, Random={len(rates_random_sampled)}")


def compute_cdf(data_list):
    """Compute cumulative distribution: for each unique x, y = number of data points <= x"""
    sorted_data = sorted(data_list)
    # Get unique values and their cumulative counts
    unique_x = []
    cumulative_y = []
    prev_val = None
    for i, val in enumerate(sorted_data):
        if val != prev_val:
            unique_x.append(val)
            cumulative_y.append(i + 1)
            prev_val = val
        else:
            # Update the cumulative value of the last point
            cumulative_y[-1] = i + 1
    return unique_x, cumulative_y


# Compute CDF for each category (based on sampled data)
x_high, y_high = compute_cdf(rates_high_sampled)
x_low, y_low = compute_cdf(rates_low_sampled)
x_random, y_random = compute_cdf(rates_random_sampled)

print(f"Unique values: High={len(x_high)}, Low={len(x_low)}, Random={len(x_random)}")

# Plotting: left bar chart + right CDF, font size 30% larger than original
FONT_LABEL = 18   # 14 * 1.3
FONT_TITLE = 21   # 16 * 1.3
FONT_TICK = 16    # 12 * 1.3
FONT_LEGEND = 16  # 12 * 1.3

fig, axes = plt.subplots(1, 2, figsize=(18, 7))

# ========== Left plot: Sandwich attack MEV density bar chart (from plot_mev_complete third panel) ==========
ax1 = axes[0]
categories = ['High', 'Random', 'Low']
x = np.arange(len(categories))
sandwich = [0.0233, 0.0363, 0.0289]
color_sandwich = '#2c5282'
bars = ax1.bar(x, sandwich, width=0.5, color=color_sandwich)

ax1.set_ylabel('MEV Density (%)', fontsize=FONT_LABEL, labelpad=6)
ax1.set_title('', fontsize=FONT_TITLE)
ax1.set_xticks(x)
ax1.set_xticklabels(categories, fontsize=FONT_TICK)
ax1.tick_params(axis='both', labelsize=FONT_TICK)

for bar in bars:
    ax1.annotate(f'{bar.get_height():.3f}%', xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                 ha='center', va='bottom', fontsize=FONT_TICK)

# ========== Right plot: Sandwich attack rate cumulative distribution ==========
ax2 = axes[1]
ax2.scatter(x_high, y_high, c='red', label='High Volatility', alpha=0.6, s=28)
ax2.scatter(x_low, y_low, c='blue', label='Low Volatility', alpha=0.6, s=28)
ax2.scatter(x_random, y_random, c='green', label='Random', alpha=0.6, s=28)

ax2.set_xlabel('Sandwich Attack Rate (%)', fontsize=FONT_LABEL)
ax2.set_ylabel('Cumulative Number of Data Points', fontsize=FONT_LABEL)
ax2.set_title(
    '', fontsize=FONT_TITLE)
ax2.legend(loc='lower right', fontsize=FONT_LEGEND)
ax2.tick_params(axis='both', labelsize=FONT_TICK)
ax2.grid(True, alpha=0.3)

plt.subplots_adjust(left=0.08, right=0.98, top=0.90, bottom=0.12, wspace=0.28)
plt.savefig('sandwich_rate_distribution_sampled_90.png',
            dpi=150, bbox_inches='tight')
print('Figure saved as sandwich_rate_distribution_sampled_90.png')

plt.show()
