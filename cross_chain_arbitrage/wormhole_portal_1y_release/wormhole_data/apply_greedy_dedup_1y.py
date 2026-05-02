#!/usr/bin/env python3
"""
1y 版: 贪心去污染, 写入 entry/exit_swaps_greedy + greedy_subtype 等。
处理 strict + loose 全部 8 个文件。
"""

import json
import os
from pathlib import Path
from collections import defaultdict

RECENT = Path(__file__).parent / "use" / "portal_full" / "recent_1y"
RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
DIR = RECENT / (f"matched_{RUN_TAG}" if RUN_TAG else "matched") / "arbitrage"
STOP_AT = 0.95

RELIABILITY_MAP = {
    "full_clean": "reliable",
    "multi_cycle_contamination": "reliable_after_dedup_failed",
    "insufficient_swap": "unreliable_partial_holdings",
    "mixed_asymmetric": "unreliable_asymmetric",
    "partial_one_side": "unreliable_asymmetric",
    "one_way_entry_only": "unreliable_one_way",
    "one_way_exit_only": "unreliable_one_way",
}


def classify_subtype(e, x, e_present=True, x_present=True):
    if not e_present and not x_present:
        return "unclassified"
    if not e_present:
        return "one_way_exit_only"
    if not x_present:
        return "one_way_entry_only"
    e_low, e_mid, e_hi = e < 0.8, 0.8 <= e <= 1.2, e > 1.2
    x_low, x_mid, x_hi = x < 0.8, 0.8 <= x <= 1.2, x > 1.2
    if e_mid and x_mid: return "full_clean"
    if (e_mid or e_hi) and (x_mid or x_hi) and (e_hi or x_hi): return "multi_cycle_contamination"
    if e_low and x_low: return "insufficient_swap"
    if (e_low and x_hi) or (x_low and e_hi): return "mixed_asymmetric"
    if (e_mid and x_low) or (x_mid and e_low): return "partial_one_side"
    return "unclassified"


def greedy(swaps, stop_at=STOP_AT):
    sorted_s = sorted(swaps or [], key=lambda s: abs(s.get("delta_bridge_sec", 0)))
    picked, cov = [], 0.0
    for s in sorted_s:
        picked.append(s)
        cov += s.get("coverage", 0) or 0
        if cov >= stop_at:
            break
    return picked, cov


def process_file(path):
    print(f"\n处理 {path.name} ...")
    data = json.load(open(path, encoding="utf-8"))
    cands = data.get("candidates", [])

    subtype_stats = defaultdict(int)
    reliability_stats = defaultdict(int)
    transition = defaultdict(int)

    for c in cands:
        e_picks, e_cov_g = greedy(c.get("entry_swaps", []))
        x_picks, x_cov_g = greedy(c.get("exit_swaps", []))
        new_subtype = classify_subtype(e_cov_g, x_cov_g,
                                       e_present=bool(e_picks),
                                       x_present=bool(x_picks))
        new_reliability = RELIABILITY_MAP.get(new_subtype, "unknown")
        c["entry_swaps_greedy"] = e_picks
        c["exit_swaps_greedy"] = x_picks
        c["entry_coverage_greedy"] = e_cov_g
        c["exit_coverage_greedy"] = x_cov_g
        c["subtype_greedy"] = new_subtype
        c["pnl_reliability_greedy"] = new_reliability
        subtype_stats[new_subtype] += 1
        reliability_stats[new_reliability] += 1
        transition[f"{c.get('subtype','?')} -> {new_subtype}"] += 1

    data.setdefault("meta", {})["subtype_greedy_stats"] = dict(subtype_stats)
    data["meta"]["reliability_greedy_stats"] = dict(reliability_stats)
    data["meta"]["transition_stats"] = dict(sorted(transition.items(), key=lambda kv: -kv[1]))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

    print(f"  total={len(cands):,}")
    print(f"  subtype_greedy: {dict(subtype_stats)}")
    print(f"  reliability:    {dict(reliability_stats)}")
    print(f"  top transitions:")
    for k, v in list(data["meta"]["transition_stats"].items())[:6]:
        print(f"    {k}: {v}")


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
