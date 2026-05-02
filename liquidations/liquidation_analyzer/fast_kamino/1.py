import requests
import pandas as pd

# Fetch all protocols
url = "https://api.llama.fi/protocols"
response = requests.get(url)
protocols = response.json()

# Filter Solana-native lending protocols (deployed only on Solana)
solana_native_lending = [
    {
        'name': p['name'],
        'tvl': p.get('tvl', 0),
    }
    for p in protocols
    if p.get('chains', []) == ['Solana']  # Only on Solana chain
    and p.get('category') == 'Lending'
    and p.get('tvl', 0) > 0  # Filter out TVL == 0
]

df = pd.DataFrame(solana_native_lending)
df = df.sort_values('tvl', ascending=False).reset_index(drop=True)

# Calculate share
total_tvl = df['tvl'].sum()
df['tvl_millions'] = (df['tvl'] / 1e6).round(2)
df['share_pct'] = (df['tvl'] / total_tvl * 100).round(2)

# Formatted output
print(f"Solana Native Lending Protocol TVL Statistics")
print(f"{'='*50}")
print(f"Total TVL: ${total_tvl/1e9:.2f}B\n")

print(df[['name', 'tvl_millions', 'share_pct']].to_string(index=False))
print(f"\n{'='*50}")
print(f"Protocol count: {len(df)}")
