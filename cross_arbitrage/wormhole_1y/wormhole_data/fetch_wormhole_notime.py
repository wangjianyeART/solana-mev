#!/usr/bin/env python3
"""
Wormhole SOL↔ETH 全量采集（带chain过滤，不带时间，去重）

翻页到底，按id去重，连续3页全重复则停止。

用法:
    python wormhole_data/fetch_wormhole_notime.py
"""

import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_URL    = "https://api.wormholescan.io/api/v1/operations"
PAGE_SIZE   = 50
MAX_PAGES   = 5000
SLEEP_SEC   = 0.3
MAX_RETRIES = 5
SAVE_EVERY  = 2000

DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT = Path(__file__).parent / "wormhole_sol_eth_notime.json"


def parse_ts(ts_str):
    if not ts_str:
        return None
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def extract_record(op, label):
    content   = op.get("content", {})
    std       = content.get("standarizedProperties", {})
    src_chain = op.get("sourceChain", {})
    dst_chain = op.get("targetChain", {})
    data      = op.get("data", {})
    t_src = parse_ts(src_chain.get("timestamp"))
    t_dst = parse_ts(dst_chain.get("timestamp"))
    lat = round((t_dst - t_src).total_seconds(), 1) if t_src and t_dst else None

    return {
        "id":              op.get("id"),
        "direction":       label,
        "dst_status":      dst_chain.get("status"),
        "app_ids":         ",".join(std.get("appIds", [])),
        "src_timestamp":   t_src.isoformat() if t_src else None,
        "src_tx_hash":     src_chain.get("transaction", {}).get("txHash"),
        "src_sender":      src_chain.get("from"),
        "dst_timestamp":   t_dst.isoformat() if t_dst else None,
        "dst_tx_hash":     dst_chain.get("transaction", {}).get("txHash"),
        "dst_receiver":    std.get("toAddress"),
        "latency_seconds": lat,
        "token_symbol":    data.get("symbol"),
        "token_amount":    data.get("tokenAmount"),
        "usd_amount":      data.get("usdAmount"),
        "token_address":   std.get("tokenAddress"),
    }


def fetch_page(session, params, page):
    p = {**params, "page": page}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(BASE_URL, params=p, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRIES:
                return None
            time.sleep(2 ** attempt)


def save_json(records, tag=""):
    by_dir = {}
    by_token = {}
    by_app = {}
    total_usd = 0
    for r in records:
        by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
        sym = r["token_symbol"] or "?"
        by_token[sym] = by_token.get(sym, 0) + 1
        app = r["app_ids"] or "NONE"
        by_app[app] = by_app.get(app, 0) + 1
        try:
            total_usd += float(r["usd_amount"] or 0)
        except (TypeError, ValueError):
            pass

    times = sorted([r["src_timestamp"] for r in records if r["src_timestamp"]])
    output = {
        "meta": {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "note":          "带chain过滤，不带时间限制，按id去重",
            "total":         len(records),
            "by_direction":  by_dir,
            "by_app_ids":    dict(sorted(by_app.items(), key=lambda x: -x[1])),
            "total_usd":     round(total_usd, 2),
            "time_earliest": times[0] if times else None,
            "time_latest":   times[-1] if times else None,
            "top_tokens":    dict(sorted(by_token.items(), key=lambda x: -x[1])[:30]),
        },
        "records": records,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"  保存 {len(records):,} 条 → {OUTPUT.name} ({size_mb:.1f} MB){tag}", flush=True)


def main():
    session = requests.Session()
    records = []
    seen_ids = set()
    last_save = 0
    t0 = time.time()

    print("=" * 65)
    print("  Wormhole SOL↔ETH 全量（带chain，无时间，去重）")
    print("=" * 65)
    print(f"  MAX_PAGES={MAX_PAGES}, PAGE_SIZE={PAGE_SIZE}")
    print()

    for direction in DIRECTIONS:
        label = direction["label"]
        print(f"  === {label} ===", flush=True)

        params = {
            "pageSize":    PAGE_SIZE,
            "sortOrder":   "DESC",
            "sourceChain": direction["sourceChain"],
            "targetChain": direction["targetChain"],
        }

        page = 0
        kept = 0
        dupes = 0
        empty_streak = 0

        while page < MAX_PAGES:
            data = fetch_page(session, params, page)
            if data is None:
                print(f"    page {page}: 请求失败，停止")
                break
            ops = data.get("operations", [])
            if not ops:
                print(f"    page {page}: 空，停止")
                break

            page_kept = 0
            for op in ops:
                op_id = op.get("id")
                if op_id in seen_ids:
                    page_kept += 0  # dupe
                    dupes += 1
                else:
                    seen_ids.add(op_id)
                    records.append(extract_record(op, label))
                    page_kept += 1
                    kept += 1

            if page_kept == 0:
                empty_streak += 1
                if empty_streak >= 3:
                    print(f"    连续3页全重复，停止 (page {page})")
                    break
            else:
                empty_streak = 0

            if (page + 1) % 20 == 0:
                elapsed = time.time() - t0
                # 显示当前页最老的时间戳
                last_ts = ""
                for op in ops:
                    ts = op.get("sourceChain", {}).get("timestamp", "")
                    if ts:
                        last_ts = ts
                print(f"    page {page+1:>5}: 新增{kept:,} 重复{dupes:,} 最老={last_ts} ({elapsed:.0f}s)", flush=True)

            if len(records) - last_save >= SAVE_EVERY:
                save_json(records, "  (进行中)")
                last_save = len(records)

            if len(ops) < PAGE_SIZE:
                print(f"    page {page}: 不满页({len(ops)}条)，停止")
                break

            page += 1
            time.sleep(SLEEP_SEC)

        elapsed = time.time() - t0
        print(f"    {label} 完成: 新增{kept:,} 重复{dupes:,} 翻了{page+1}页 ({elapsed:.0f}s)\n")

    elapsed = time.time() - t0
    save_json(records, "  [最终]")

    # 统计
    times = sorted([r["src_timestamp"] for r in records if r["src_timestamp"]])
    by_app = {}
    by_token = {}
    for r in records:
        app = r["app_ids"] or "NONE"
        by_app[app] = by_app.get(app, 0) + 1
        sym = r["token_symbol"] or "?"
        by_token[sym] = by_token.get(sym, 0) + 1

    print(f"\n{'=' * 65}")
    print(f"  结果: {len(records):,} 条独立记录 ({elapsed:.0f}s)")
    print(f"  时间: {times[0] if times else '?'} → {times[-1] if times else '?'}")
    print(f"{'=' * 65}")

    print(f"\n  App分布:")
    for app, cnt in sorted(by_app.items(), key=lambda x: -x[1]):
        print(f"    {app:<50s} {cnt:>6}")

    print(f"\n  Token Top20:")
    for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:20]:
        print(f"    {sym:<15s} {cnt:>6}")


if __name__ == "__main__":
    main()
