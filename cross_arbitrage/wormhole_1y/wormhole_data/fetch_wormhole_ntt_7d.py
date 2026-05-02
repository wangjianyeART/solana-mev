#!/usr/bin/env python3
"""
Wormhole NATIVE_TOKEN_TRANSFER 过去1年 SOL↔ETH

用法:
    python wormhole_data/fetch_wormhole_ntt_7d.py
"""

import requests
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_URL = "https://api.wormholescan.io/api/v1/operations"
END_DATE   = datetime.now(timezone.utc)
START_DATE = END_DATE - timedelta(days=365)

PAGE_SIZE   = 50
MAX_PAGES   = 500
SLEEP_SEC   = 0.4
MAX_RETRIES = 5

DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT = Path(__file__).parent / "wormhole_ntt_1y.json"


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


def has_ntt(op):
    content = op.get("content", {})
    std = content.get("standarizedProperties", {})
    return "NATIVE_TOKEN_TRANSFER" in std.get("appIds", [])


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
    scanned = 0
    t0 = time.time()

    print(f"Wormhole NTT 1年: {START_DATE.date()} → {END_DATE.date()}\n")

    for direction in DIRECTIONS:
        label = direction["label"]
        print(f"  {label}", end="", flush=True)

        params = {
            "pageSize":    PAGE_SIZE,
            "sortOrder":   "ASC",
            "sourceChain": direction["sourceChain"],
            "targetChain": direction["targetChain"],
            "startTime":   START_DATE.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "endTime":     END_DATE.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }

        page = 0
        kept = 0
        while page < MAX_PAGES:
            data = fetch_page(session, params, page)
            if data is None:
                break
            ops = data.get("operations", [])
            if not ops:
                break
            for op in ops:
                scanned += 1
                if has_ntt(op):
                    records.append(extract_record(op, label))
                    kept += 1
            if len(ops) < PAGE_SIZE:
                break
            page += 1
            time.sleep(SLEEP_SEC)

        print(f"  扫描{scanned} NTT={kept}")

    elapsed = time.time() - t0

    # 保存
    by_dir = {}
    by_token = {}
    total_usd = 0
    for r in records:
        by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
        sym = r["token_symbol"] or "?"
        by_token[sym] = by_token.get(sym, 0) + 1
        try:
            total_usd += float(r["usd_amount"] or 0)
        except (TypeError, ValueError):
            pass

    output = {
        "meta": {
            "fetched_at":   datetime.now(timezone.utc).isoformat(),
            "start_date":   START_DATE.isoformat(),
            "end_date":     END_DATE.isoformat(),
            "filter":       "NATIVE_TOKEN_TRANSFER",
            "total":        len(records),
            "scanned":      scanned,
            "by_direction":  by_dir,
            "total_usd":    round(total_usd, 2),
            "top_tokens":   dict(sorted(by_token.items(), key=lambda x: -x[1])[:20]),
        },
        "records": records,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n完成: {elapsed:.0f}s, 扫描{scanned}, NTT={len(records)}")
    print(f"保存: {OUTPUT.name} ({size_mb:.1f} MB)")

    print(f"\nToken分布:")
    for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1]):
        print(f"  {sym:<15s} {cnt:>6}")


if __name__ == "__main__":
    main()
