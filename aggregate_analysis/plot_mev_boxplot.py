#!/usr/bin/env python3
"""
Plot boxplots of liquidation rate, sandwich attack rate, and arbitrage rate (by high/low/random category)
"""

import json
import matplotlib.pyplot as plt

# Load data
with open('merged_mev_volatility.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Calculate rates per data point by category
high_liquidation_rate = []
high_sandwich_rate = []
high_arbitrage_rate = []

low_liquidation_rate = []
low_sandwich_rate = []
low_arbitrage_rate = []

random_liquidation_rate = []
random_sandwich_rate = []
random_arbitrage_rate = []

for slot_range, info in data.items():
    total_tx = info['success_tx'] + info['fail_tx']
    if total_tx == 0:
        continue

    liq_rate = (info['liquidation_success'] +
                info['liquidation_fail']) / total_tx * 100
    sandwich_rate = (info['sandwich_success'] +
                     info['sandwich_failed']) / total_tx * 100
    arb_rate = (info['sol_arb'] + info['usdc_arb'] +
                info['usdt_arb']) / total_tx * 100

    if info['category'] == 'high':
        high_liquidation_rate.append(liq_rate)
        high_sandwich_rate.append(sandwich_rate)
        high_arbitrage_rate.append(arb_rate)
    elif info['category'] == 'low':
        low_liquidation_rate.append(liq_rate)
        low_sandwich_rate.append(sandwich_rate)
        low_arbitrage_rate.append(arb_rate)
    elif info['category'] == 'random':
        random_liquidation_rate.append(liq_rate)
        random_sandwich_rate.append(sandwich_rate)
        random_arbitrage_rate.append(arb_rate)

# Create figure - 3 subplots
fig, axes = plt.subplots(1, 3, figsize=(16, 6))

colors = ['red', 'green', 'blue']
labels = ['High\nVolatility', 'Random', 'Low\nVolatility']

# Liquidation rate boxplot
ax1 = axes[0]
bp1 = ax1.boxplot([high_liquidation_rate, random_liquidation_rate, low_liquidation_rate],
                  labels=labels, patch_artist=True)
for patch, color in zip(bp1['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.6)
ax1.set_ylabel('Rate (%)', fontsize=11)
ax1.set_title('Liquidation Rate', fontsize=12)
ax1.grid(True, alpha=0.3, axis='y')

# Sandwich attack rate boxplot
ax2 = axes[1]
bp2 = ax2.boxplot([high_sandwich_rate, random_sandwich_rate, low_sandwich_rate],
                  labels=labels, patch_artist=True)
for patch, color in zip(bp2['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.6)
ax2.set_ylabel('Rate (%)', fontsize=11)
ax2.set_title('Sandwich Attack Rate', fontsize=12)
ax2.grid(True, alpha=0.3, axis='y')

# Arbitrage rate boxplot
ax3 = axes[2]
bp3 = ax3.boxplot([high_arbitrage_rate, random_arbitrage_rate, low_arbitrage_rate],
                  labels=labels, patch_artist=True)
for patch, color in zip(bp3['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.6)
ax3.set_ylabel('Rate (%)', fontsize=11)
ax3.set_title('Arbitrage Rate', fontsize=12)
ax3.grid(True, alpha=0.3, axis='y')

# Main title
fig.suptitle('MEV Activity Rates by Volatility Category',
             fontsize=14, fontweight='bold')

# Save figure
plt.tight_layout()
plt.savefig('mev_rates_boxplot.png', dpi=150)
print('Figure saved as mev_rates_boxplot.png')

# Print statistics


def calc_mean(lst):
    return sum(lst) / len(lst) if lst else 0


print("\nStatistics (mean %):")
print(f"Liquidation rate:     High={calc_mean(high_liquidation_rate):.4f}%, Random={calc_mean(random_liquidation_rate):.4f}%, Low={calc_mean(low_liquidation_rate):.4f}%")
print(f"Sandwich attack rate: High={calc_mean(high_sandwich_rate):.6f}%, Random={calc_mean(random_sandwich_rate):.6f}%, Low={calc_mean(low_sandwich_rate):.6f}%")
print(f"Arbitrage rate:       High={calc_mean(high_arbitrage_rate):.4f}%, Random={calc_mean(random_arbitrage_rate):.4f}%, Low={calc_mean(low_arbitrage_rate):.4f}%")

plt.show()
