#!/usr/bin/env python3
"""
把 Portal/NTT 数据中 Solana 端的 dst_receiver（token account）解析为 owner 钱包地址。

用法:
    python wormhole_data/resolve_owners.py
"""

import requests
import json
import time
from pathlib import Path

RPC_URL = "https://api.mainnet-beta.solana.com"

DIR = Path(__file__).parent / "use"
FILES = [
    DIR / "bridge_records" / "wormhole_portal.json",
    DIR / "bridge_records" / "wormhole_ntt.json",
]
OUTPUT = DIR / "addresses" / "addresses_with_owners.json"


def get_owner(address):
    """查询 token account 的 owner"""
    for attempt in range(3):
        try:
            resp = requests.post(RPC_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "jsonParsed"}],
            }, timeout=30)
            data = resp.json()
            value = data.get("result", {}).get("value")
            if not value:
                return None  # 账户不存在或已关闭
            parsed = value.get("data", {}).get("parsed", {})
            info = parsed.get("info", {})
            return info.get("owner")
        except Exception:
            time.sleep(1)
    return None


def main():
    # 收集所有Solana端的dst_receiver
    sol_receivers = set()
    all_records = []

    for fpath in FILES:
        if not fpath.exists():
            continue
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        for r in data["records"]:
            all_records.append(r)
            dst = r.get("dst_receiver", "")
            if dst and not dst.startswith("0x"):
                sol_receivers.add(dst)

    print(f"总记录: {len(all_records):,}")
    print(f"Solana dst_receiver: {len(sol_receivers)} 个")
    print()

    # 批量查owner
    owner_map = {}  # token_account -> owner
    total = len(sol_receivers)
    resolved = 0
    closed = 0

    print(f"解析owner中...", flush=True)
    for i, addr in enumerate(sorted(sol_receivers)):
        owner = get_owner(addr)
        if owner:
            owner_map[addr] = owner
            resolved += 1
        else:
            owner_map[addr] = None
            closed += 1

        if (i + 1) % 20 == 0 or i == total - 1:
            print(f"  {i+1}/{total}  已解析{resolved} 已关闭{closed}", flush=True)

        time.sleep(0.25)

    # 统计owner活跃度
    owner_stats = {}
    for r in all_records:
        dst = r.get("dst_receiver", "")
        src = r.get("src_sender", "")
        direction = r.get("direction", "")
        token = r.get("token_symbol") or "?"
        protocol = "Portal" if "PORTAL" in (r.get("app_ids") or "") else "NTT"

        # dst_receiver -> owner（Solana端）
        if dst and not dst.startswith("0x"):
            owner = owner_map.get(dst, dst)
            if owner:
                if owner not in owner_stats:
                    owner_stats[owner] = {"address": owner, "chain": "solana", "roles": set(),
                                          "tokens": set(), "directions": set(), "protocols": set(),
                                          "tx_count": 0, "token_accounts": set()}
                owner_stats[owner]["roles"].add("receiver")
                owner_stats[owner]["tokens"].add(token)
                owner_stats[owner]["directions"].add(direction)
                owner_stats[owner]["protocols"].add(protocol)
                owner_stats[owner]["tx_count"] += 1
                owner_stats[owner]["token_accounts"].add(dst)

        # src_sender（如果是Solana地址）
        if src and not src.startswith("0x"):
            if src not in owner_stats:
                owner_stats[src] = {"address": src, "chain": "solana", "roles": set(),
                                    "tokens": set(), "directions": set(), "protocols": set(),
                                    "tx_count": 0, "token_accounts": set()}
            owner_stats[src]["roles"].add("sender")
            owner_stats[src]["tokens"].add(token)
            owner_stats[src]["directions"].add(direction)
            owner_stats[src]["protocols"].add(protocol)
            owner_stats[src]["tx_count"] += 1

        # ETH地址直接用
        if dst and dst.startswith("0x"):
            if dst not in owner_stats:
                owner_stats[dst] = {"address": dst, "chain": "ethereum", "roles": set(),
                                    "tokens": set(), "directions": set(), "protocols": set(),
                                    "tx_count": 0, "token_accounts": set()}
            owner_stats[dst]["roles"].add("receiver")
            owner_stats[dst]["tokens"].add(token)
            owner_stats[dst]["directions"].add(direction)
            owner_stats[dst]["protocols"].add(protocol)
            owner_stats[dst]["tx_count"] += 1

        if src and src.startswith("0x"):
            if src not in owner_stats:
                owner_stats[src] = {"address": src, "chain": "ethereum", "roles": set(),
                                    "tokens": set(), "directions": set(), "protocols": set(),
                                    "tx_count": 0, "token_accounts": set()}
            owner_stats[src]["roles"].add("sender")
            owner_stats[src]["tokens"].add(token)
            owner_stats[src]["directions"].add(direction)
            owner_stats[src]["protocols"].add(protocol)
            owner_stats[src]["tx_count"] += 1

    # 转为可序列化
    addr_list = []
    for a in owner_stats.values():
        addr_list.append({
            "address": a["address"],
            "chain": a["chain"],
            "roles": sorted(a["roles"]),
            "protocols": sorted(a["protocols"]),
            "tokens": sorted(a["tokens"]),
            "directions": sorted(a["directions"]),
            "tx_count": a["tx_count"],
            "token_accounts": sorted(a["token_accounts"]) if a["token_accounts"] else [],
        })
    addr_list.sort(key=lambda x: -x["tx_count"])

    sol_addrs = [a for a in addr_list if a["chain"] == "solana"]
    eth_addrs = [a for a in addr_list if a["chain"] == "ethereum"]
    bidirectional = [a for a in addr_list if len(a["directions"]) > 1]

    output = {
        "meta": {
            "total_addresses": len(addr_list),
            "solana_owners": len(sol_addrs),
            "ethereum_addresses": len(eth_addrs),
            "bidirectional": len(bidirectional),
            "token_accounts_resolved": resolved,
            "token_accounts_closed": closed,
            "owner_map": owner_map,
        },
        "addresses": addr_list,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  总地址: {len(addr_list)} (Solana owner: {len(sol_addrs)}, ETH: {len(eth_addrs)})")
    print(f"  双向跨链: {len(bidirectional)}")
    print(f"  token account 解析: {resolved}成功, {closed}已关闭")
    print(f"  保存: {OUTPUT.name}")

    print(f"\nTop 20 活跃地址:")
    for a in addr_list[:20]:
        print(f"  {a['address'][:24]}...  {a['chain']:<8s}  {a['tx_count']:>3}笔  {','.join(a['roles']):<16s}  {','.join(a['tokens'][:3])}")

    print(f"\n双向跨链地址 (潜在套利者):")
    for a in bidirectional[:15]:
        print(f"  {a['address'][:24]}...  {a['chain']:<8s}  {a['tx_count']:>3}笔  {','.join(a['directions'])}  {','.join(a['tokens'][:3])}")


if __name__ == "__main__":
    main()
