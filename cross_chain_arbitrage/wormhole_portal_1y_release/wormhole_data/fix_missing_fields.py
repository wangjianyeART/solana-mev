#!/usr/bin/env python3
"""
补回 matched_context_parsed.json 丢失的字段:
  - time_diff_sec
  - sol_fee_sol
  - eth_fee_eth
  - eth_gas_used

从 matched.json 通过 (sol_sig, eth_hash) 作为 key 匹配。
同时顺便补到 matched_context_filtered.json（保持两个文件一致）。
"""

import json
from pathlib import Path

DIR = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
MATCHED = DIR / "matched.json"
FILTERED = DIR / "matched_context_filtered.json"
PARSED = DIR / "matched_context_parsed.json"

FIELDS = ["time_diff_sec", "sol_fee_sol", "eth_fee_eth", "eth_gas_used"]


def build_index(matched_path):
    data = json.load(open(matched_path, encoding="utf-8"))
    recs = data if isinstance(data, list) else data.get("records", [])
    idx = {}
    for r in recs:
        key = (r.get("sol_sig"), r.get("eth_hash"))
        idx[key] = {f: r.get(f) for f in FIELDS}
    return idx


def patch(path, idx):
    data = json.load(open(path, encoding="utf-8"))
    recs = data["records"] if isinstance(data, dict) and "records" in data else data

    patched = 0
    missed = 0
    for r in recs:
        key = (r.get("sol_sig"), r.get("eth_hash"))
        src = idx.get(key)
        if not src:
            missed += 1
            continue
        for f in FIELDS:
            if r.get(f) is None and src.get(f) is not None:
                r[f] = src[f]
        patched += 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  {path.name}: 已补 {patched} 条, 未找到 {missed} 条")


def main():
    print(f"从 {MATCHED.name} 建索引...")
    idx = build_index(MATCHED)
    print(f"  索引大小: {len(idx)}\n")

    print("补 matched_context_filtered.json ...")
    patch(FILTERED, idx)

    print("\n补 matched_context_parsed.json ...")
    patch(PARSED, idx)

    print("\n完成")


if __name__ == "__main__":
    main()
