#!/usr/bin/env python3
"""
从 wormhole_sol_eth_dedup.json 中提取 Portal 和 NTT 记录，分别保存。

用法:
    python wormhole_data/extract_portal_ntt.py
"""

import json
from pathlib import Path

DIR = Path(__file__).parent

# 用最新的7天数据
INPUT = DIR / "wormhole_sol_eth_max.json"

OUT_DIR = DIR / "use"
OUT_DIR.mkdir(exist_ok=True)
PORTAL_OUT = OUT_DIR / "bridge_records" / "wormhole_portal.json"
NTT_OUT    = OUT_DIR / "bridge_records" / "wormhole_ntt.json"


def stats(records):
    by_dir = {}
    by_token = {}
    total_usd = 0
    for r in records:
        by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
        sym = r.get("token_symbol") or "?"
        by_token[sym] = by_token.get(sym, 0) + 1
        try:
            total_usd += float(r.get("usd_amount") or 0)
        except (TypeError, ValueError):
            pass
    times = sorted([r["src_timestamp"] for r in records if r.get("src_timestamp")])
    return by_dir, by_token, total_usd, times


def save(records, path, label):
    by_dir, by_token, total_usd, times = stats(records)
    output = {
        "meta": {
            "source":        INPUT.name,
            "filter":        label,
            "total":         len(records),
            "by_direction":  by_dir,
            "total_usd":     round(total_usd, 2),
            "time_earliest": times[0] if times else None,
            "time_latest":   times[-1] if times else None,
            "top_tokens":    dict(sorted(by_token.items(), key=lambda x: -x[1])[:20]),
        },
        "records": records,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    size_mb = path.stat().st_size / 1024 / 1024
    return size_mb


def main():
    print(f"读取: {INPUT.name}")
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    records = data["records"]
    print(f"总记录: {len(records):,}\n")

    portal = [r for r in records if "PORTAL_TOKEN_BRIDGE" in (r.get("app_ids") or "")]
    ntt    = [r for r in records if "NATIVE_TOKEN_TRANSFER" in (r.get("app_ids") or "")]

    # Portal
    mb = save(portal, PORTAL_OUT, "PORTAL_TOKEN_BRIDGE")
    by_dir, by_token, total_usd, times = stats(portal)
    print(f"Portal: {len(portal):,} 条 ({mb:.1f} MB)")
    print(f"  时间: {times[0] if times else '?'} → {times[-1] if times else '?'}")
    for d, cnt in sorted(by_dir.items()):
        print(f"  {d}: {cnt:,}")
    print(f"  USD: ${total_usd:,.2f}")
    print(f"  Token:")
    for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:10]:
        print(f"    {sym:<15s} {cnt:>5}")

    print()

    # NTT
    mb = save(ntt, NTT_OUT, "NATIVE_TOKEN_TRANSFER")
    by_dir, by_token, total_usd, times = stats(ntt)
    print(f"NTT: {len(ntt):,} 条 ({mb:.1f} MB)")
    print(f"  时间: {times[0] if times else '?'} → {times[-1] if times else '?'}")
    for d, cnt in sorted(by_dir.items()):
        print(f"  {d}: {cnt:,}")
    print(f"  USD: ${total_usd:,.2f}")
    print(f"  Token:")
    for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:10]:
        print(f"    {sym:<15s} {cnt:>5}")


if __name__ == "__main__":
    main()
