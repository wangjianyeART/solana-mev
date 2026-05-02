#!/usr/bin/env python3
"""
Wormhole 全量 SOL↔ETH 1年数据采集

不做 appId 筛选，所有协议全部保留（Portal、NTT、CCTP、Mayan等）。
30天滑动窗口，双向采集。

用法:
    python wormhole_data/fetch_wormhole_all_1y.py
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_URL   = "https://api.wormholescan.io/api/v1/operations"
END_DATE   = datetime(2026, 4, 13, tzinfo=timezone.utc)
START_DATE = datetime(2025, 4, 13, tzinfo=timezone.utc)

PAGE_SIZE   = 50
MAX_PAGES   = 500
SLEEP_SEC   = 0.4
MAX_RETRIES = 5
WINDOW_DAYS = 30
SAVE_EVERY  = 5000

DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT = Path(__file__).parent / "wormhole_all_1y.json"


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
            "start_date":    START_DATE.isoformat(),
            "end_date":      END_DATE.isoformat(),
            "filter":        "无（全量）",
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
    last_save = 0
    t0 = time.time()

    windows = []
    win_end = END_DATE
    while win_end > START_DATE:
        win_start = max(win_end - timedelta(days=WINDOW_DAYS), START_DATE)
        windows.append((win_start, win_end))
        win_end = win_start
    windows.reverse()

    total_batches = len(windows) * len(DIRECTIONS)
    batch_idx = 0

    print("=" * 65)
    print("  Wormhole 全量 SOL↔ETH 1年采集")
    print("=" * 65)
    print(f"  {START_DATE.date()} → {END_DATE.date()} ({(END_DATE-START_DATE).days}天)")
    print(f"  {len(windows)} 窗口 x 2 方向 = {total_batches} 批次")
    print(f"  不筛选 appId，全部保留")
    print()

    for win_start, win_end in windows:
        for direction in DIRECTIONS:
            batch_idx += 1
            label = direction["label"]
            print(f"  [{batch_idx}/{total_batches}] {label} {win_start.date()}~{win_end.date()}", end="", flush=True)

            params = {
                "pageSize":    PAGE_SIZE,
                "sortOrder":   "ASC",
                "sourceChain": direction["sourceChain"],
                "targetChain": direction["targetChain"],
                "startTime":   win_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "endTime":     win_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            }

            page = 0
            win_count = 0
            while page < MAX_PAGES:
                data = fetch_page(session, params, page)
                if data is None:
                    break
                ops = data.get("operations", [])
                if not ops:
                    break
                for op in ops:
                    records.append(extract_record(op, label))
                    win_count += 1
                if len(ops) < PAGE_SIZE:
                    break
                page += 1
                time.sleep(SLEEP_SEC)

            elapsed = time.time() - t0
            print(f"  +{win_count} 累计{len(records):,} ({elapsed:.0f}s)")

            if len(records) - last_save >= SAVE_EVERY:
                save_json(records, "  (进行中)")
                last_save = len(records)

    elapsed = time.time() - t0
    print(f"\n完成: {elapsed:.0f}s, 总计{len(records):,}条")
    save_json(records, "  [最终]")

    # 统计
    by_app = {}
    by_token = {}
    for r in records:
        app = r["app_ids"] or "NONE"
        by_app[app] = by_app.get(app, 0) + 1
        sym = r["token_symbol"] or "?"
        by_token[sym] = by_token.get(sym, 0) + 1

    print(f"\nApp分布:")
    for app, cnt in sorted(by_app.items(), key=lambda x: -x[1]):
        print(f"  {app:<50s} {cnt:>6}")

    print(f"\nToken分布:")
    for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:20]:
        print(f"  {sym:<15s} {cnt:>6}")


if __name__ == "__main__":
    main()
