#!/usr/bin/env python3
"""
从 Portal 桥接记录 + SOL 解析数据，构建 SPL mint ↔ ERC20 contract 映射表。

逻辑:
  1. bridge_records/wormhole_portal.json 已有 token_symbol + token_address(ERC20)
  2. 用 src_tx_hash 在 portal_sol_parsed.json 中查找对应 SOL 交易
  3. 从 SOL 交易的 token_changes 中提取 mint 地址
  4. 聚合去重 → 输出映射表

用法:
    python wormhole_data/build_token_pairs.py
"""

import json
from pathlib import Path
from collections import defaultdict

DIR = Path(__file__).parent / "use"

# 输入
BRIDGE_FILE = DIR / "bridge_records" / "wormhole_portal.json"
SOL_PARSED = DIR / "portal_full" / "portal_sol_parsed.json"

# 输出
OUTPUT = DIR / "portal_full" / "token_pairs.json"


def main():
    # 加载桥接记录
    bridge_data = json.load(open(BRIDGE_FILE, encoding="utf-8"))
    records = bridge_data["records"]
    print(f"桥接记录: {len(records)} 条")

    # 加载 SOL 解析结果，建 sig → transaction 索引
    sol_data = json.load(open(SOL_PARSED, encoding="utf-8"))
    sol_index = {tx["sig"]: tx for tx in sol_data["transactions"]}
    print(f"SOL 解析: {len(sol_index)} 笔")

    # 遍历桥接记录，匹配 SPL mint
    pairs = defaultdict(lambda: {
        "erc20": set(),
        "spl_mint": set(),
        "count": 0,
        "directions": defaultdict(int),
        "amounts": [],
    })

    matched = 0
    unmatched = 0
    no_sol_tx = 0

    for rec in records:
        symbol = rec.get("token_symbol", "?")
        erc20 = rec.get("token_address", "")
        direction = rec.get("direction", "")
        amount = rec.get("token_amount", "")
        src_tx = rec.get("src_tx_hash", "")
        dst_tx = rec.get("dst_tx_hash", "")

        # 在 SOL 端查找交易
        sol_tx = sol_index.get(src_tx)
        if not sol_tx:
            no_sol_tx += 1
            continue

        # 从 token_changes 中提取 mint
        # 取变动最大的（绝对值），排除 SOL 本身
        token_changes = sol_tx.get("token_changes", [])
        if not token_changes:
            unmatched += 1
            continue

        # 找变动绝对值最大的 token（通常就是跨链的那个）
        best = max(token_changes, key=lambda tc: abs(tc["change"]))
        spl_mint = best["mint"]
        sol_symbol = best["symbol"]

        # 聚合
        key = symbol if symbol != "?" else erc20
        info = pairs[key]
        if erc20 and isinstance(erc20, str):
            info["erc20"].add(erc20.lower())
        info["spl_mint"].add(spl_mint)
        info["count"] += 1
        info["directions"][direction] += 1
        try:
            info["amounts"].append(float(amount))
        except (ValueError, TypeError):
            pass
        info["sol_symbol"] = sol_symbol
        matched += 1

    print(f"\n匹配: {matched}, 无SOL交易: {no_sol_tx}, 无token变动: {unmatched}")

    # 整理输出
    result = []
    for symbol, info in sorted(pairs.items(), key=lambda x: -x[1]["count"]):
        entry = {
            "symbol": symbol,
            "erc20": sorted(x for x in info["erc20"] if x),
            "spl_mint": sorted(x for x in info["spl_mint"] if x),
            "sol_symbol": info.get("sol_symbol", ""),
            "count": info["count"],
            "directions": dict(info["directions"]),
        }
        if info["amounts"]:
            entry["total_amount"] = round(sum(info["amounts"]), 4)
        result.append(entry)

    output = {
        "meta": {
            "total_pairs": len(result),
            "total_matched": matched,
            "no_sol_tx": no_sol_tx,
            "no_token_change": unmatched,
            "source_bridge_records": len(records),
            "source_sol_parsed": len(sol_index),
        },
        "pairs": result,
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n输出: {OUTPUT}")
    print(f"共 {len(result)} 个 token pair\n")

    # 打印结果
    print(f"{'Symbol':<20s} {'ERC20':<44s} {'SPL Mint':<46s} {'Count':>5s}")
    print("-" * 120)
    for p in result:
        erc20 = str(p["erc20"][0]) if p["erc20"] else "?"
        spl = str(p["spl_mint"][0]) if p["spl_mint"] else "?"
        sym = str(p.get("symbol") or "?")
        print(f"{sym:<20s} {erc20:<44s} {spl:<46s} {p['count']:>5d}")
        for e in p["erc20"][1:]:
            print(f"{'':20s} {str(e):<44s}")
        for s in p["spl_mint"][1:]:
            print(f"{'':20s} {'':44s} {str(s):<46s}")


if __name__ == "__main__":
    main()
