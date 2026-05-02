#!/usr/bin/env python3
"""
对 arbitrage_candidates_*.json 的每条候选应用贪心去污染。

贪心逻辑:
  按 |Δt| 升序累加 coverage, 到 >= 0.95 停。剩下的 swap 丢弃（视为邻近 cycle 噪音）。

写回字段:
  entry_swaps_greedy       贪心选中的 entry swap 列表
  exit_swaps_greedy        贪心选中的 exit swap 列表
  entry_coverage_greedy    贪心后 entry 累计 coverage
  exit_coverage_greedy     贪心后 exit 累计 coverage
  subtype_greedy           用贪心后 coverage 重新分类（5 类）
  pnl_reliability_greedy   subtype_greedy 对应的可靠性

统计字段写进 meta:
  subtype_greedy_stats
  reliability_greedy_stats
  transition_stats         原 subtype -> 新 subtype 的迁移
"""

import json
from pathlib import Path
from collections import defaultdict

DIR = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
STOP_AT = 0.95

RELIABILITY_MAP = {
    "full_clean": "reliable",
    "multi_cycle_contamination": "reliable_after_dedup_failed",  # 贪心后仍 >1.2
    "insufficient_swap": "unreliable_partial_holdings",
    "mixed_asymmetric": "unreliable_asymmetric",
    "partial_one_side": "unreliable_asymmetric",
}


def classify_subtype(e, x):
    e_low, e_mid, e_hi = e < 0.8, 0.8 <= e <= 1.2, e > 1.2
    x_low, x_mid, x_hi = x < 0.8, 0.8 <= x <= 1.2, x > 1.2
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


def greedy(swaps, stop_at=STOP_AT):
    sorted_s = sorted(swaps or [], key=lambda s: abs(s.get("delta_bridge_sec", 0)))
    picked = []
    cov = 0.0
    for s in sorted_s:
        picked.append(s)
        cov += s.get("coverage", 0) or 0
        if cov >= stop_at:
            break
    return picked, cov


def process_file(path):
    print(f"\n处理 {path.name}...")
    data = json.load(open(path, encoding="utf-8"))
    cands = data.get("candidates", [])

    subtype_stats = defaultdict(int)
    reliability_stats = defaultdict(int)
    transition = defaultdict(int)

    for c in cands:
        e_picks, e_cov_g = greedy(c.get("entry_swaps", []))
        x_picks, x_cov_g = greedy(c.get("exit_swaps", []))
        new_subtype = classify_subtype(e_cov_g, x_cov_g)
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
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  总计 {len(cands)} 条")
    print(f"  subtype_greedy:   {dict(subtype_stats)}")
    print(f"  reliability:      {dict(reliability_stats)}")
    print(f"  主要迁移:")
    for k, v in list(data["meta"]["transition_stats"].items())[:8]:
        print(f"    {k}: {v}")


def main():
    files = sorted(DIR.glob("arbitrage_candidates_*pct.json"))
    if not files:
        print(f"未找到文件: {DIR}")
        return
    for p in files:
        process_file(p)
    print("\n完成")


if __name__ == "__main__":
    main()
