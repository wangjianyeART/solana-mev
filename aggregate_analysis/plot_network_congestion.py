import matplotlib.pyplot as plt
import numpy as np

categories = ['High Volatility', 'Random', 'Low Volatility']
x = np.arange(len(categories))
width = 0.35

fig, ax = plt.subplots(figsize=(9, 6))

empty_rate = [3.23, 1.79, 0.10]
fail_rate = [47.41, 37.04, 20.41]

bars1 = ax.bar(x - width/2, empty_rate, width,
               label='Empty Block Rate', color='#3498db')
bars2 = ax.bar(x + width/2, fail_rate, width,
               label='Transaction Failure Rate', color='#e74c3c')

ax.set_ylabel('Rate (%)')
ax.set_xlabel('Volatility Category')
ax.set_title('Network Congestion by Volatility')
ax.set_xticks(x)
ax.set_xticklabels(categories)
ax.legend()

# Add value labels
for bar in bars1:
    ax.annotate(f'{bar.get_height():.2f}%',
                xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                ha='center', va='bottom', fontsize=9)

for bar in bars2:
    ax.annotate(f'{bar.get_height():.1f}%',
                xy=(bar.get_x() + bar.get_width()/2, bar.get_height()),
                ha='center', va='bottom', fontsize=9)

plt.tight_layout()
plt.savefig('network_congestion.png', dpi=150)
plt.show()
