import matplotlib.pyplot as plt
import numpy as np

fig, axes = plt.subplots(1, 2, figsize=(8, 4.2))

categories = ['High', 'Random', 'Low']
x = np.arange(len(categories))
width = 0.35
# Legend colors: left plot has two, middle plot has two
color_empty = '#8ec8e8'   # light blue
color_fail = '#e8a0a0'    # light red
color_liquidation = '#8ed88e'  # light green
color_arbitrage = '#e8c090'    # light orange

# ========== Left plot: Network stress indicators ==========
ax1 = axes[0]
empty_rate = [3.23, 1.79, 0.10]
fail_rate = [47.41, 37.04, 20.41]

bars1 = ax1.bar(x - width/2, empty_rate, width,
                label='Empty Block Rate', color=color_empty)
bars2 = ax1.bar(x + width/2, fail_rate, width,
                label='Failure Rate', color=color_fail)

ax1.set_ylabel('Rate (%)', fontsize=14, labelpad=4)
ax1.set_title('Network Stress', fontsize=17)
ax1.set_xticks(x)
ax1.set_xticklabels(categories, fontsize=14)
ax1.legend(loc='upper right', fontsize=9)
ax1.tick_params(axis='both', width=0.5, labelsize=12)
ax1.spines['top'].set_linewidth(0.5)
ax1.spines['right'].set_linewidth(0.5)
ax1.spines['left'].set_linewidth(0.5)
ax1.spines['bottom'].set_linewidth(0.5)

# Add value labels
for bar in bars1:
    ax1.annotate(f'{bar.get_height():.1f}%', xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                 ha='center', va='bottom', fontsize=10)
for bar in bars2:
    ax1.annotate(f'{bar.get_height():.1f}%', xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                 ha='center', va='bottom', fontsize=10)

# ========== Right plot: Liquidation + Arbitrage ==========
ax2 = axes[1]
liquidation = [2.53, 0.19, 0.02]
arbitrage = [4.89, 0.78, 0.51]

bars3 = ax2.bar(x - width/2, liquidation, width,
                label='Liquidation', color=color_liquidation)
bars4 = ax2.bar(x + width/2, arbitrage, width,
                label='Arbitrage', color=color_arbitrage)

ax2.set_ylabel('MEV Density (%)', fontsize=14, labelpad=4)
ax2.set_title('Liquidation & Arbitrage', fontsize=17)
ax2.set_xticks(x)
ax2.set_xticklabels(categories, fontsize=12)
ax2.legend(loc='upper right', fontsize=9)
ax2.tick_params(axis='both', width=0.5, labelsize=12)
ax2.spines['top'].set_linewidth(0.5)
ax2.spines['right'].set_linewidth(0.5)
ax2.spines['left'].set_linewidth(0.5)
ax2.spines['bottom'].set_linewidth(0.5)

for bar in bars3:
    ax2.annotate(f'{bar.get_height():.2f}%', xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                 ha='center', va='bottom', fontsize=10)
for bar in bars4:
    ax2.annotate(f'{bar.get_height():.2f}%', xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                 ha='center', va='bottom', fontsize=10)

# Main title
# fig.suptitle('MEV Activity and Network Metrics by Volatility Category',
#              fontsize=21, fontweight='bold')

# Tight layout: subplots close together, y-axis margins to avoid overlap
plt.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.22, wspace=0.32)

# Average volatility text placed below the figure
volatility_text = 'Average Volatility:  High 3.68%  |  Random 0.10%  |  Low 0.0023%'
fig.text(0.5, 0.08, volatility_text,
         fontsize=15,
         verticalalignment='center',
         horizontalalignment='center',
         bbox=dict(boxstyle='round', facecolor='#f0f0f0', alpha=0.9, edgecolor='#ccc'))

plt.savefig('mev_complete.png', dpi=150, bbox_inches='tight')
plt.show()
