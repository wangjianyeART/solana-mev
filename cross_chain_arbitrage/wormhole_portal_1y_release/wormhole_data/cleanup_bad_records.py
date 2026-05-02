#!/usr/bin/env python3
"""
删掉 184 条坏记录 (20 pollution + 164 unrelayed), 所有下游 artifact 同步清理.
保留: clean_ok(3872) + hash_mismatch(235, 后续 Task #6 重解析)
原文件会备份到 *.bak_before_chain_cleanup.
"""

import json
import shutil
from pathlib import Path

BASE = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
ARB = BASE / "arbitrage"

BACKUP_SUFFIX = ".bak_before_chain_cleanup"


def load_bad_sigs():
    c = json.load(open(ARB / "chain_verification_classified.json"))
    bad = set()
    for rec in c["pollution"]:
        bad.add(rec["sig"])
    for sig in c["unrelayed"]:
        bad.add(sig)
    return bad


def clean_list_file(path: Path, bad: set, key_path=("records",), sig_field="sol_sig"):
    """records 在 d[key_path[0]]... 下, 每条有 sig_field."""
    d = json.load(open(path, encoding="utf-8"))
    shutil.copy2(path, str(path) + BACKUP_SUFFIX)

    cur = d
    for k in key_path[:-1]:
        cur = cur[k]
    recs = cur[key_path[-1]]
    before = len(recs)
    kept = [r for r in recs if r.get(sig_field) not in bad]
    removed = before - len(kept)
    cur[key_path[-1]] = kept

    # 更新 meta.total 如有
    if isinstance(d, dict) and isinstance(d.get("meta"), dict) and "total" in d["meta"]:
        d["meta"]["total"] = len(kept)

    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  {path.name}: {before} -> {len(kept)} (删 {removed})")
    return removed


def clean_categories_file(path: Path, bad: set, cats_key="categories"):
    """extreme_candidates_for_review.json: d['categories'][cat] = [records]"""
    d = json.load(open(path, encoding="utf-8"))
    shutil.copy2(path, str(path) + BACKUP_SUFFIX)
    cats = d.get(cats_key, d)  # suspicious_* has cats at top level
    total_removed = 0
    for cname, recs in cats.items():
        before = len(recs)
        kept = [r for r in recs if r.get("sol_sig") not in bad]
        cats[cname] = kept
        total_removed += before - len(kept)
    if cats_key in d:
        d[cats_key] = cats
        if "summary" in d:
            d["summary"]["unique_candidates"] = len(
                set(r.get("sol_sig") for recs in cats.values() for r in recs)
            )
    else:
        d = cats
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  {path.name}: 删 {total_removed} 条")
    return total_removed


def main():
    bad = load_bad_sigs()
    print(f"待删 sol_sig 集合: {len(bad)} (pollution+unrelayed)")
    print()

    print("== 主 pipeline 文件 (meta/records) ==")
    for fn in [
        "matched.json",
        "matched_context.json",
        "matched_context_parsed.json",
        "matched_context_filtered.json",
    ]:
        clean_list_file(BASE / fn, bad, key_path=("records",))

    print()
    print("== arbitrage candidates (meta/candidates) ==")
    for fn in [
        "arbitrage_candidates_1pct.json",
        "arbitrage_candidates_5pct.json",
        "arbitrage_candidates_10pct.json",
        "arbitrage_candidates_20pct.json",
    ]:
        clean_list_file(ARB / fn, bad, key_path=("candidates",))

    print()
    print("== review 文件 ==")
    clean_categories_file(ARB / "extreme_candidates_for_review.json", bad, cats_key="categories")
    clean_categories_file(ARB / "suspicious_for_review.json", bad, cats_key=None)
    clean_categories_file(ARB / "suspicious_after_fix.json", bad, cats_key=None)

    print()
    print("完成. 原文件已备份为 *.bak_before_chain_cleanup")


if __name__ == "__main__":
    main()
