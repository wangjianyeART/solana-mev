#!/usr/bin/env python3
"""
Wormhole SOL↔ETH 最大化采集

API时间过滤无效，本程序不分窗口，直接翻页到底。
同时尝试ASC和DESC两个方向翻，合并去重，尽量多拿数据。

用法:
    python wormhole_data/fetch_wormhole_max.py
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

DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT = Path(__file__).parent / "wormhole_sol_eth_max.json"


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


def fetch_direction(session, direction, seen_ids, sort_order):
    """翻页采集一个方向，返回新记录列表"""
    label = direction["label"]
    params = {
        "pageSize":    PAGE_SIZE,
        "sortOrder":   sort_order,
        "sourceChain": direction["sourceChain"],
        "targetChain": direction["targetChain"],
    }

    new_records = []
    page = 0
    dupes = 0

    while page < MAX_PAGES:
        data = fetch_page(session, params, page)
        if data is None:
            break
        ops = data.get("operations", [])
        if not ops:
            break

        for op in ops:
            op_id = op.get("id")
            if op_id in seen_ids:
                dupes += 1
                continue
            seen_ids.add(op_id)
            new_records.append(extract_record(op, label))

        if len(ops) < PAGE_SIZE:
            break
        page += 1
        time.sleep(SLEEP_SEC)

    return new_records, page + 1, dupes


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
            "note":          "最大化采集: ASC+DESC双向翻页, 按id去重",
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
    all_records = []
    seen_ids = set()
    t0 = time.time()

    print("=" * 65)
    print("  Wormhole SOL↔ETH 最大化采集")
    print("=" * 65)
    print("  策略: ASC + DESC 双向翻页，合并去重")
    print()

    for direction in DIRECTIONS:
        label = direction["label"]

        # ASC翻页
        print(f"  {label} ASC翻页...", end="", flush=True)
        recs_asc, pages_asc, dupes_asc = fetch_direction(session, direction, seen_ids, "ASC")
        all_records.extend(recs_asc)
        elapsed = time.time() - t0
        print(f"  +{len(recs_asc):,} 新 ({pages_asc}页, {dupes_asc}重复) ({elapsed:.0f}s)")

        # DESC翻页（可能拿到不同的数据）
        print(f"  {label} DESC翻页...", end="", flush=True)
        recs_desc, pages_desc, dupes_desc = fetch_direction(session, direction, seen_ids, "DESC")
        all_records.extend(recs_desc)
        elapsed = time.time() - t0
        print(f"  +{len(recs_desc):,} 新 ({pages_desc}页, {dupes_desc}重复) ({elapsed:.0f}s)")

        dir_total = len(recs_asc) + len(recs_desc)
        print(f"  {label} 合计: {dir_total:,} 条独立记录\n")

        save_json(all_records, "  (进行中)")

    elapsed = time.time() - t0

    save_json(all_records, "  [最终]")

    times = sorted([r["src_timestamp"] for r in all_records if r["src_timestamp"]])
    by_app = {}
    by_token = {}
    for r in all_records:
        app = r["app_ids"] or "NONE"
        by_app[app] = by_app.get(app, 0) + 1
        sym = r["token_symbol"] or "?"
        by_token[sym] = by_token.get(sym, 0) + 1

    print(f"\n{'=' * 65}")
    print(f"  结果: {len(all_records):,} 条独立记录 ({elapsed:.0f}s)")
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
