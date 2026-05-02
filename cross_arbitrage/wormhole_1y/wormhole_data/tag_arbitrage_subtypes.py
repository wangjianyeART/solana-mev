#!/usr/bin/env python3
"""
给 arbitrage_candidates_*.json 的每条候选加 subtype + pnl_reliability 标签。

subtype 分类（独占）:
  full_clean                    entry_cov ∈ [0.8, 1.2] AND exit_cov ∈ [0.8, 1.2]
  multi_cycle_contamination     两边都 >= 0.8 但至少一边 > 1.2（连续循环污染）
  insufficient_swap             两边都 < 0.8（用户部分持仓 + 现买）
  mixed_asymmetric              一边 < 0.8 且另一边 > 1.2（bridge 拆/合并）
  partial_one_side              一边 ∈ [0.8, 1.2] + 另一边 < 0.8（单侧数据缺失）

pnl_reliability:
  reliable                      full_clean
  reliable_after_dedup          multi_cycle_contamination（贪心去污染后可算）
  unreliable_partial_holdings   insufficient_swap（成本基础缺失）
  unreliable_asymmetric         mixed_asymmetric + partial_one_side（entry/exit 不对称）
"""

import json
from pathlib import Path

DIR = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"

RELIABILITY_MAP = {
    "full_clean": "reliable",
    "multi_cycle_contamination": "reliable_after_dedup",
    "insufficient_swap": "unreliable_partial_holdings",
    "mixed_asymmetric": "unreliable_asymmetric",
    "partial_one_side": "unreliable_asymmetric",
}


def classify_subtype(entry_cov, exit_cov):
    """根据两边覆盖率返回独占 subtype"""
    e_low = entry_cov < 0.8
    e_mid = 0.8 <= entry_cov <= 1.2
    e_hi = entry_cov > 1.2
    x_low = exit_cov < 0.8
    x_mid = 0.8 <= exit_cov <= 1.2
    x_hi = exit_cov > 1.2

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
    print(f"\n处理 {path.name}...")
    data = json.load(open(path, encoding="utf-8"))
    cands = data.get("candidates", [])

    subtype_stats = {}
    reliability_stats = {}

    for c in cands:
        e_cov = c.get("entry_coverage", 0)
        x_cov = c.get("exit_coverage", 0)
        subtype = classify_subtype(e_cov, x_cov)
        reliability = RELIABILITY_MAP.get(subtype, "unknown")

        c["subtype"] = subtype
        c["pnl_reliability"] = reliability

        subtype_stats[subtype] = subtype_stats.get(subtype, 0) + 1
        reliability_stats[reliability] = reliability_stats.get(reliability, 0) + 1

    # 把统计写进 meta
    data.setdefault("meta", {})["subtype_stats"] = subtype_stats
    data["meta"]["reliability_stats"] = reliability_stats

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  总计 {len(cands)} 条")
    print(f"  subtype: {subtype_stats}")
    print(f"  reliability: {reliability_stats}")


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
