import matplotlib.pyplot as plt
import numpy as np

# MEV types as x-axis
mev_types = ['Liquidation', 'Arbitrage', 'Sandwich Attack']
x = np.arange(len(mev_types))
width = 0.25

fig, ax1 = plt.subplots(figsize=(12, 7))

# Data: each MEV type has three volatility values
# [High, Random, Low]
liquidation_data = [2.5291, 0.1938, 0.0173]
arbitrage_data = [4.8867, 0.7770, 0.5075]
sandwich_data = [0.0233, 0.0363, 0.0289]

# Transpose data so each volatility category corresponds to a group
high_values = [liquidation_data[0], arbitrage_data[0], sandwich_data[0]]
random_values = [liquidation_data[1], arbitrage_data[1], sandwich_data[1]]
low_values = [liquidation_data[2], arbitrage_data[2], sandwich_data[2]]

# Plot liquidation and arbitrage (using left Y-axis)
bars_high = ax1.bar(x - width, [high_values[0], high_values[1], 0], width,
                    label='High Volatility', color='#e74c3c', alpha=0.8)
bars_random = ax1.bar(x, [random_values[0], random_values[1], 0], width,
                      label='Random', color='#f39c12', alpha=0.8)
bars_low = ax1.bar(x + width, [low_values[0], low_values[1], 0], width,
                   label='Low Volatility', color='#3498db', alpha=0.8)

# Set left Y-axis
ax1.set_xlabel('MEV Type', fontsize=12, fontweight='bold')
ax1.set_ylabel('Liquidation & Arbitrage Density (%)',
               fontsize=11, color='black')
ax1.set_xticks(x)
ax1.set_xticklabels(mev_types)
ax1.tick_params(axis='y', labelcolor='black')
ax1.grid(True, alpha=0.3, axis='y')

# Create right Y-axis for sandwich attacks
ax2 = ax1.twinx()

# Plot sandwich attacks (using right Y-axis)
bars_high_sandwich = ax2.bar(x[2] - width, high_values[2], width,
                             color='#e74c3c', alpha=0.8)
bars_random_sandwich = ax2.bar(x[2], random_values[2], width,
                               color='#f39c12', alpha=0.8)
bars_low_sandwich = ax2.bar(x[2] + width, low_values[2], width,
                            color='#3498db', alpha=0.8)

# Set right Y-axis
ax2.set_ylabel('Sandwich Attack Density (%)', fontsize=11, color='#2ecc71')
ax2.tick_params(axis='y', labelcolor='#2ecc71')

# Add title and legend
ax1.set_title('MEV Density by Type and Volatility Category',
              fontsize=14, fontweight='bold', pad=20)
ax1.legend(loc='upper left', framealpha=0.9)

# Add average volatility info
volatility_text = (
    'Average Volatility:\n'
    'High: 3.68%\n'
    'Random: 0.10%\n'
    'Low: 0.0023%'
)
ax1.text(-0.35, 3.5, volatility_text,
         fontsize=9,
         verticalalignment='center',
         horizontalalignment='left',
         bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.8))

# Add value labels (liquidation and arbitrage)
for i in range(2):  # Only label the first two types (liquidation and arbitrage)
    # High
    if high_values[i] > 0:
        ax1.text(x[i] - width, high_values[i], f'{high_values[i]:.2f}%',
                 ha='center', va='bottom', fontsize=9, fontweight='bold')
    # Random
    if random_values[i] > 0:
        ax1.text(x[i], random_values[i], f'{random_values[i]:.2f}%',
                 ha='center', va='bottom', fontsize=9, fontweight='bold')
    # Low
    if low_values[i] > 0:
        ax1.text(x[i] + width, low_values[i], f'{low_values[i]:.2f}%',
                 ha='center', va='bottom', fontsize=9, fontweight='bold')

# Add sandwich attack value labels
ax2.text(x[2] - width, high_values[2], f'{high_values[2]:.4f}%',
         ha='center', va='bottom', fontsize=9, fontweight='bold')
ax2.text(x[2], random_values[2], f'{random_values[2]:.4f}%',
         ha='center', va='bottom', fontsize=9, fontweight='bold')
ax2.text(x[2] + width, low_values[2], f'{low_values[2]:.4f}%',
         ha='center', va='bottom', fontsize=9, fontweight='bold')

plt.tight_layout()
plt.savefig('mev_density_by_type.png', dpi=150, bbox_inches='tight')
print('Figure saved as mev_density_by_type.png')
plt.show()
