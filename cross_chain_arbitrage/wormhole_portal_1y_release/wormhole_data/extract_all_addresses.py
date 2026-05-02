#!/usr/bin/env python3
"""
从 Portal 和 NTT 数据中提取所有 src_sender 和 dst_receiver，
按 Solana / Ethereum 分类，保存到一个 JSON。

用法:
    python wormhole_data/extract_all_addresses.py
"""

import json
from pathlib import Path

DIR = Path(__file__).parent / "use"
FILES = [
    (DIR / "bridge_records" / "wormhole_portal.json", "Portal"),
    (DIR / "bridge_records" / "wormhole_ntt.json", "NTT"),
]
OUTPUT = DIR / "addresses" / "all_addresses.json"

solana = {}   # address -> info
ethereum = {}

for fpath, protocol in FILES:
    if not fpath.exists():
        print(f"跳过: {fpath.name}")
        continue
    with open(fpath, encoding="utf-8") as f:
        data = json.load(f)

    for r in data["records"]:
        direction = r.get("direction", "?")
        token = r.get("token_symbol") or "?"

        for addr, role in [(r.get("src_sender"), "sender"), (r.get("dst_receiver"), "receiver")]:
            if not addr:
                continue
            is_eth = addr.startswith("0x")
            bucket = ethereum if is_eth else solana
            # ETH地址统一小写去重
            key = addr.lower() if is_eth else addr

            if key not in bucket:
                bucket[key] = {
                    "address": key if is_eth else addr,
                    "roles": set(),
                    "protocols": set(),
                    "tokens": set(),
                    "directions": set(),
                    "tx_count": 0,
                }
            bucket[key]["roles"].add(role)
            bucket[key]["protocols"].add(protocol)
            bucket[key]["tokens"].add(token)
            bucket[key]["directions"].add(direction)
            bucket[key]["tx_count"] += 1


def to_list(bucket):
    out = []
    for a in bucket.values():
        out.append({
            "address": a["address"],
            "roles": sorted(a["roles"]),
            "protocols": sorted(a["protocols"]),
            "tokens": sorted(a["tokens"]),
            "directions": sorted(a["directions"]),
            "tx_count": a["tx_count"],
        })
    out.sort(key=lambda x: -x["tx_count"])
    return out


sol_list = to_list(solana)
eth_list = to_list(ethereum)

output = {
    "meta": {
        "solana_addresses": len(sol_list),
        "ethereum_addresses": len(eth_list),
        "total": len(sol_list) + len(eth_list),
    },
    "solana": sol_list,
    "ethereum": eth_list,
}

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"Solana: {len(sol_list)} 个地址")
print(f"Ethereum: {len(eth_list)} 个地址")
print(f"保存: {OUTPUT.name}")
