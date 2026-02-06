import requests
import pandas as pd

# 获取所有协议
url = "https://api.llama.fi/protocols"
response = requests.get(url)
protocols = response.json()

# 筛选 Solana 原生借贷协议（只在 Solana 链上部署的）
solana_native_lending = [
    {
        'name': p['name'],
        'tvl': p.get('tvl', 0),
    }
    for p in protocols
    if p.get('chains', []) == ['Solana']  # 只有 Solana 一条链
    and p.get('category') == 'Lending'
    and p.get('tvl', 0) > 0  # 过滤 TVL 为 0 的
]

df = pd.DataFrame(solana_native_lending)
df = df.sort_values('tvl', ascending=False).reset_index(drop=True)

# 计算占比
total_tvl = df['tvl'].sum()
df['tvl_millions'] = (df['tvl'] / 1e6).round(2)
df['share_pct'] = (df['tvl'] / total_tvl * 100).round(2)

# 格式化输出
print(f"Solana 原生借贷协议 TVL 统计")
print(f"{'='*50}")
print(f"总 TVL: ${total_tvl/1e9:.2f}B\n")

print(df[['name', 'tvl_millions', 'share_pct']].to_string(index=False))
print(f"\n{'='*50}")
print(f"协议数量: {len(df)}")
