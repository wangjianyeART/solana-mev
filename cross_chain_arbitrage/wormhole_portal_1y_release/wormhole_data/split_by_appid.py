#!/usr/bin/env python3
"""
把 wormhole_sol_eth_100k.json 按 app_ids 分成不同的 JSON 文件。

用法:
    python wormhole_data/split_by_appid.py
"""

import json
from pathlib import Path

INPUT = Path(__file__).parent / "wormhole_sol_eth_100k.json"
OUT_DIR = Path(__file__).parent / "by_appid"
OUT_DIR.mkdir(exist_ok=True)

# app_ids → 文件名映射
NAME_MAP = {
    "PORTAL_TOKEN_BRIDGE":                              "portal",
    "NATIVE_TOKEN_TRANSFER":                            "ntt",
    "MAYAN_SWIFT":                                      "mayan_swift",
    "MAYAN":                                            "mayan",
    "MESSAGING_EXECUTOR,CCTP_V2":                       "cctp_v2",
    "MESSAGING_EXECUTOR,CCTP_V1":                       "cctp_v1",
    "NATIVE_TOKEN_TRANSFER,MESSAGING_EXECUTOR":         "ntt_executor",
    "PORTAL_TOKEN_BRIDGE,UNKNOWN":                      "portal_unknown",
    "PORTAL_TOKEN_BRIDGE,UNKNOWN,MESSAGING_EXECUTOR":   "portal_unknown_executor",
    "PORTAL_TOKEN_BRIDGE,CONNECT":                      "portal_connect",
}


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    records = data["records"]
    buckets = {}

    for r in records:
        app = r.get("app_ids") or "NONE"
        if app not in buckets:
            buckets[app] = []
        buckets[app].append(r)

    print(f"总记录: {len(records):,}\n")

    for app, recs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        name = NAME_MAP.get(app, app.lower().replace(",", "_").replace(" ", "_"))
        fname = f"wormhole_{name}.json"
        path = OUT_DIR / fname

        # 简单统计
        by_dir = {}
        by_token = {}
        total_usd = 0
        for r in recs:
            by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
            sym = r["token_symbol"] or "?"
            by_token[sym] = by_token.get(sym, 0) + 1
            try:
                total_usd += float(r["usd_amount"] or 0)
            except (TypeError, ValueError):
                pass

        output = {
            "meta": {
                "app_ids":      app,
                "total":        len(recs),
                "by_direction": by_dir,
                "total_usd":    round(total_usd, 2),
                "top_tokens":   dict(sorted(by_token.items(), key=lambda x: -x[1])[:15]),
            },
            "records": recs,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  {fname:<45s} {len(recs):>6,} 条  {size_mb:.1f} MB")
        for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:5]:
            print(f"    {sym:<12s} {cnt:>5}")
        print()

    print("完成")


if __name__ == "__main__":
    main()
