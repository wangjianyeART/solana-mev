#!/usr/bin/env python3
"""
Wormhole SOL↔ETH 全量采集（去重版）

API时间过滤无效，只能通过offset翻页拿最近数据。
不分窗口，直接翻到底，按id去重。

用法:
    python wormhole_data/fetch_wormhole_dedup.py
"""

import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_URL   = "https://api.wormholescan.io/api/v1/operations"
PAGE_SIZE   = 50
MAX_PAGES   = 1000
SLEEP_SEC   = 0.3
MAX_RETRIES = 5

DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT = Path(__file__).parent / "wormhole_sol_eth_dedup.json"


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


def main():
    session = requests.Session()
    records = []
    seen_ids = set()
    t0 = time.time()

    print("=" * 65)
    print("  Wormhole SOL↔ETH 全量采集（去重）")
    print("=" * 65)
    print(f"  API时间过滤无效，直接翻页到底")
    print(f"  最大页数: {MAX_PAGES} (每页{PAGE_SIZE}条)")
    print()

    for direction in DIRECTIONS:
        label = direction["label"]
        print(f"  {label}:", flush=True)

        params = {
            "pageSize":    PAGE_SIZE,
            "sortOrder":   "ASC",
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
                break
            ops = data.get("operations", [])
            if not ops:
                break

            page_kept = 0
            page_dupes = 0
            for op in ops:
                op_id = op.get("id")
                if op_id in seen_ids:
                    page_dupes += 1
                    continue
                seen_ids.add(op_id)
                records.append(extract_record(op, label))
                page_kept += 1

            kept += page_kept
            dupes += page_dupes

            # 如果整页都是重复，连续3次就停
            if page_kept == 0:
                empty_streak += 1
                if empty_streak >= 3:
                    print(f"    连续3页全重复，停止")
                    break
            else:
                empty_streak = 0

            if (page + 1) % 10 == 0:
                elapsed = time.time() - t0
                print(f"    page {page+1}: 新增{kept} 重复{dupes} ({elapsed:.0f}s)", flush=True)

            if len(ops) < PAGE_SIZE:
                break
            page += 1
            time.sleep(SLEEP_SEC)

        elapsed = time.time() - t0
        print(f"    完成: {kept}条, 重复{dupes}条, 翻了{page+1}页 ({elapsed:.0f}s)")

    elapsed = time.time() - t0

    # 统计
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
            "note":          "API时间过滤无效，这是offset翻页能拿到的全部数据",
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

    print(f"\n{'=' * 65}")
    print(f"  结果")
    print(f"{'=' * 65}")
    print(f"  独立记录: {len(records):,}")
    print(f"  时间范围: {times[0] if times else '?'} → {times[-1] if times else '?'}")
    print(f"  总USD:    ${total_usd:,.2f}")
    print(f"  保存:     {OUTPUT.name} ({size_mb:.1f} MB)")

    print(f"\n  App分布:")
    for app, cnt in sorted(by_app.items(), key=lambda x: -x[1]):
        print(f"    {app:<50s} {cnt:>6}")

    print(f"\n  Token Top20:")
    for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:20]:
        print(f"    {sym:<15s} {cnt:>6}")

    for d, cnt in by_dir.items():
        print(f"\n  {d}: {cnt:,}")


if __name__ == "__main__":
    main()
