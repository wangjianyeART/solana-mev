#!/usr/bin/env python3
"""
从 Portal 和 NTT 数据中提取所有 src_sender 和 dst_receiver 地址。

用法:
    python wormhole_data/extract_addresses.py
"""

import json
from pathlib import Path

DIR = Path(__file__).parent / "use"
OUTPUT = DIR / "addresses" / "addresses.json"

portal_file = DIR / "bridge_records" / "wormhole_portal.json"
ntt_file = DIR / "bridge_records" / "wormhole_ntt.json"

all_addresses = {}  # address -> {chains, directions, tokens, tx_count, records}

for fname, protocol in [(portal_file, "Portal"), (ntt_file, "NTT")]:
    if not fname.exists():
        print(f"跳过: {fname.name} 不存在")
        continue

    with open(fname, encoding="utf-8") as f:
        data = json.load(f)

    for r in data["records"]:
        direction = r.get("direction", "?")
        token = r.get("token_symbol") or "?"
        src = r.get("src_sender")
        dst = r.get("dst_receiver")

        for addr, role in [(src, "sender"), (dst, "receiver")]:
            if not addr:
                continue
            if addr not in all_addresses:
                all_addresses[addr] = {
                    "address": addr,
                    "chain": "solana" if not addr.startswith("0x") else "ethereum",
                    "roles": set(),
                    "protocols": set(),
                    "tokens": set(),
                    "directions": set(),
                    "tx_count": 0,
                }
            all_addresses[addr]["roles"].add(role)
            all_addresses[addr]["protocols"].add(protocol)
            all_addresses[addr]["tokens"].add(token)
            all_addresses[addr]["directions"].add(direction)
            all_addresses[addr]["tx_count"] += 1

# 转成可序列化格式
addr_list = []
for a in all_addresses.values():
    addr_list.append({
        "address": a["address"],
        "chain": a["chain"],
        "roles": sorted(a["roles"]),
        "protocols": sorted(a["protocols"]),
        "tokens": sorted(a["tokens"]),
        "directions": sorted(a["directions"]),
        "tx_count": a["tx_count"],
    })

addr_list.sort(key=lambda x: -x["tx_count"])

# 统计
sol_addrs = [a for a in addr_list if a["chain"] == "solana"]
eth_addrs = [a for a in addr_list if a["chain"] == "ethereum"]
both_role = [a for a in addr_list if "sender" in a["roles"] and "receiver" in a["roles"]]
bidirectional = [a for a in addr_list if len(a["directions"]) > 1]

output = {
    "meta": {
        "total_addresses": len(addr_list),
        "solana_addresses": len(sol_addrs),
        "ethereum_addresses": len(eth_addrs),
        "both_sender_and_receiver": len(both_role),
        "bidirectional": len(bidirectional),
    },
    "addresses": addr_list,
}

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"总地址: {len(addr_list)}")
print(f"  Solana: {len(sol_addrs)}")
print(f"  Ethereum: {len(eth_addrs)}")
print(f"  同时是sender+receiver: {len(both_role)}")
print(f"  双向跨链: {len(bidirectional)}")
print(f"\n保存: {OUTPUT.name}")

print(f"\nTop 20 活跃地址:")
for a in addr_list[:20]:
    print(f"  {a['address'][:20]}...  {a['chain']:<8s}  {a['tx_count']:>3}笔  {','.join(a['roles']):<16s}  {','.join(a['tokens'][:3])}")

print(f"\n双向跨链地址 (可能套利者):")
for a in bidirectional[:20]:
    print(f"  {a['address'][:20]}...  {a['chain']:<8s}  {a['tx_count']:>3}笔  方向:{','.join(a['directions'])}  币:{','.join(a['tokens'][:3])}")
