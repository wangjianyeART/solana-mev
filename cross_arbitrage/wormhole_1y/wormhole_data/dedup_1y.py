#!/usr/bin/env python3
"""
对 wormhole_all_1y.json 按 id 去重。

用法:
    python wormhole_data/dedup_1y.py
"""

import json
from pathlib import Path

INPUT  = Path(__file__).parent / "wormhole_all_1y.json"
OUTPUT = Path(__file__).parent / "wormhole_all_1y_dedup.json"

with open(INPUT, encoding="utf-8") as f:
    data = json.load(f)

records = data["records"]
seen = set()
unique = []
for r in records:
    rid = r["id"]
    if rid not in seen:
        seen.add(rid)
        unique.append(r)

by_dir = {}
by_app = {}
by_token = {}
total_usd = 0
for r in unique:
    by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
    app = r.get("app_ids") or "NONE"
    by_app[app] = by_app.get(app, 0) + 1
    sym = r.get("token_symbol") or "?"
    by_token[sym] = by_token.get(sym, 0) + 1
    try:
        total_usd += float(r.get("usd_amount") or 0)
    except (TypeError, ValueError):
        pass

times = sorted([r["src_timestamp"] for r in unique if r.get("src_timestamp")])

output = {
    "meta": {
        "source":        INPUT.name,
        "before_dedup":  len(records),
        "after_dedup":   len(unique),
        "duplicates":    len(records) - len(unique),
        "by_direction":  by_dir,
        "by_app_ids":    dict(sorted(by_app.items(), key=lambda x: -x[1])),
        "total_usd":     round(total_usd, 2),
        "time_earliest": times[0] if times else None,
        "time_latest":   times[-1] if times else None,
        "top_tokens":    dict(sorted(by_token.items(), key=lambda x: -x[1])[:30]),
    },
    "records": unique,
}

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

size_mb = OUTPUT.stat().st_size / 1024 / 1024
print(f"去重前: {len(records):,}")
print(f"去重后: {len(unique):,}")
print(f"去掉:   {len(records) - len(unique):,} 条重复")
print(f"时间:   {times[0] if times else '?'} → {times[-1] if times else '?'}")
print(f"保存:   {OUTPUT.name} ({size_mb:.1f} MB)")

print(f"\n方向:")
for d, cnt in sorted(by_dir.items()):
    print(f"  {d}: {cnt:,}")

print(f"\nApp分布:")
for app, cnt in sorted(by_app.items(), key=lambda x: -x[1]):
    print(f"  {app:<50s} {cnt:>6}")

print(f"\nToken Top20:")
for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:20]:
    print(f"  {sym:<15s} {cnt:>6}")
