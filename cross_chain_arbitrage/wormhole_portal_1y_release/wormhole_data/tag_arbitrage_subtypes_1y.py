#!/usr/bin/env python3
"""
1y 版: 给 arbitrage_candidates_*.json 的每条候选加 subtype + pnl_reliability 标签。
路径指向 recent_1y, 处理 strict + loose 全部 8 个文件。
"""

import json
import os
from pathlib import Path

RECENT = Path(__file__).parent / "use" / "portal_full" / "recent_1y"
RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
DIR = RECENT / (f"matched_{RUN_TAG}" if RUN_TAG else "matched") / "arbitrage"

RELIABILITY_MAP = {
    "full_clean": "reliable",
    "multi_cycle_contamination": "reliable_after_dedup",
    "insufficient_swap": "unreliable_partial_holdings",
    "mixed_asymmetric": "unreliable_asymmetric",
    "partial_one_side": "unreliable_asymmetric",
    "one_way_entry_only": "unreliable_one_way",
    "one_way_exit_only": "unreliable_one_way",
}


def classify_subtype(entry_cov, exit_cov, entry_present=True, exit_present=True):
    if not entry_present and not exit_present:
        return "unclassified"
    if not entry_present:
        return "one_way_exit_only"
    if not exit_present:
        return "one_way_entry_only"

    e_low, e_mid, e_hi = entry_cov < 0.8, 0.8 <= entry_cov <= 1.2, entry_cov > 1.2
    x_low, x_mid, x_hi = exit_cov < 0.8, 0.8 <= exit_cov <= 1.2, exit_cov > 1.2

    if e_mid and x_mid:
        return "full_clean"
    if (e_mid or e_hi) and (x_mid or x_hi) and (e_hi or x_hi):
        return "multi_cycle_contamination"
    if e_low and x_low:
        return "insufficient_swap"
    if (e_low and x_hi) or (x_low and e_hi):
        return "mixed_asymmetric"
    if (e_mid and x_low) or (x_mid and e_low):
        return "partial_one_side"
    return "unclassified"


def process_file(path):
    print(f"\n处理 {path.name} ...")
    data = json.load(open(path, encoding="utf-8"))
    cands = data.get("candidates", [])

    subtype_stats, reliability_stats = {}, {}

    for c in cands:
        e_swaps = c.get("entry_swaps", [])
        x_swaps = c.get("exit_swaps", [])
        e_cov = c.get("entry_coverage", 0) or 0
        x_cov = c.get("exit_coverage", 0) or 0
        subtype = classify_subtype(e_cov, x_cov,
                                   entry_present=bool(e_swaps),
                                   exit_present=bool(x_swaps))
        reliability = RELIABILITY_MAP.get(subtype, "unknown")
        c["subtype"] = subtype
        c["pnl_reliability"] = reliability
        subtype_stats[subtype] = subtype_stats.get(subtype, 0) + 1
        reliability_stats[reliability] = reliability_stats.get(reliability, 0) + 1

    data.setdefault("meta", {})["subtype_stats"] = subtype_stats
    data["meta"]["reliability_stats"] = reliability_stats

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"  total={len(cands):,}")
    print(f"  subtype: {subtype_stats}")
    print(f"  reliability: {reliability_stats}")


def main():
    files = sorted(DIR.glob("arbitrage_candidates_*pct*.json"))
    files = [f for f in files if not f.name.endswith(".bak")]
    if not files:
        print(f"未找到文件: {DIR}")
        return
    print(f"找到 {len(files)} 个候选文件")
    for p in files:
        process_file(p)
    print("\n完成")


if __name__ == "__main__":
    main()
