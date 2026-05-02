#!/usr/bin/env python3
"""
Wormhole SOL↔ETH NATIVE_TOKEN_TRANSFER 数据采集

只采集 app_ids 包含 "NATIVE_TOKEN_TRANSFER" 的跨链记录。
这些是真正的跨链资产转移（lock & mint），适合套利研究。

用法:
    python wormhole_data/fetch_wormhole_ntt.py
"""

import requests
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BASE_URL = "https://api.wormholescan.io/api/v1/operations"

WINDOW_DAYS = 30
END_DATE   = datetime(2026, 4, 13, tzinfo=timezone.utc)
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

PAGE_SIZE   = 50
MAX_PAGES   = 500
SLEEP_SEC   = 0.4
MAX_RETRIES = 5
SAVE_EVERY  = 2000

DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT_DIR = Path(__file__).parent
JSON_OUT   = OUTPUT_DIR / "wormhole_ntt_sol_eth.json"

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def parse_ts(ts_str):
    if not ts_str:
        return None
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def latency_seconds(t_src, t_dst):
    if t_src and t_dst:
        return round((t_dst - t_src).total_seconds(), 1)
    return None


def extract_record(op, direction_label):
    content   = op.get("content", {})
    std       = content.get("standarizedProperties", {})
    src_chain = op.get("sourceChain", {})
    dst_chain = op.get("targetChain", {})
    data      = op.get("data", {})

    t_src = parse_ts(src_chain.get("timestamp"))
    t_dst = parse_ts(dst_chain.get("timestamp"))

    return {
        "id":               op.get("id"),
        "sequence":         op.get("sequence"),
        "direction":        direction_label,
        "src_status":       src_chain.get("status"),
        "dst_status":       dst_chain.get("status"),
        "app_ids":          ",".join(std.get("appIds", [])),

        "src_timestamp":    t_src.isoformat() if t_src else None,
        "src_tx_hash":      src_chain.get("transaction", {}).get("txHash"),
        "src_sender":       src_chain.get("from"),
        "src_fee":          src_chain.get("fee"),
        "src_fee_usd":      src_chain.get("feeUSD"),

        "dst_timestamp":    t_dst.isoformat() if t_dst else None,
        "dst_tx_hash":      dst_chain.get("transaction", {}).get("txHash"),
        "dst_receiver":     std.get("toAddress"),
        "dst_fee":          dst_chain.get("fee"),
        "dst_fee_usd":      dst_chain.get("feeUSD"),
        "dst_relayer":      dst_chain.get("from"),

        "latency_seconds":  latency_seconds(t_src, t_dst),

        "token_symbol":     data.get("symbol"),
        "token_amount":     data.get("tokenAmount"),
        "usd_amount":       data.get("usdAmount"),

        "token_address":    std.get("tokenAddress"),
        "token_chain":      std.get("tokenChain"),
        "amount_raw":       std.get("amount"),
    }


def has_ntt(op):
    """检查 appIds 是否包含 NATIVE_TOKEN_TRANSFER"""
    content = op.get("content", {})
    std = content.get("standarizedProperties", {})
    app_ids = std.get("appIds", [])
    return "NATIVE_TOKEN_TRANSFER" in app_ids


# ─── FETCH ───────────────────────────────────────────────────────────────────

def fetch_page(session, params, page):
    p = {**params, "page": page}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(BASE_URL, params=p, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"\n  page {page} 失败 {MAX_RETRIES} 次，跳过: {e}")
                return None
            time.sleep(2 ** attempt)


def save_json(records, tag=""):
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

    times = sorted([r["src_timestamp"] for r in records if r["src_timestamp"]])

    output = {
        "meta": {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "start_date":    START_DATE.isoformat(),
            "end_date":      END_DATE.isoformat(),
            "filter":        "app_ids contains NATIVE_TOKEN_TRANSFER",
            "total_records": len(records),
            "by_direction":  by_dir,
            "total_usd":     round(total_usd, 2),
            "time_earliest": times[0] if times else None,
            "time_latest":   times[-1] if times else None,
            "top_tokens":    dict(sorted(by_token.items(), key=lambda x: -x[1])[:20]),
        },
        "records": records,
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_mb = JSON_OUT.stat().st_size / 1024 / 1024
    print(f"  已保存 {len(records):,} 条 → {JSON_OUT.name} ({size_mb:.1f} MB){tag}", flush=True)


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    session = requests.Session()
    all_records = []
    total_scanned = 0
    total_ntt = 0
    last_save = 0
    t0 = time.time()

    # 生成时间窗口
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
    print("  Wormhole NATIVE_TOKEN_TRANSFER 采集")
    print("=" * 65)
    print(f"  日期: {START_DATE.date()} → {END_DATE.date()}")
    print(f"  窗口: {len(windows)} x {len(DIRECTIONS)} 方向 = {total_batches} 批次")
    print(f"  过滤: app_ids 包含 NATIVE_TOKEN_TRANSFER")
    print()

    for win_start, win_end in windows:
        for direction in DIRECTIONS:
            batch_idx += 1
            label = direction["label"]
            print(f"  [{batch_idx}/{total_batches}] {label}  {win_start.date()} ~ {win_end.date()}", end="", flush=True)

            page = 0
            win_records = 0
            win_scanned = 0

            params = {
                "pageSize":    PAGE_SIZE,
                "sortOrder":   "ASC",
                "sourceChain": direction["sourceChain"],
                "targetChain": direction["targetChain"],
                "startTime":   win_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "endTime":     win_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            }

            while page < MAX_PAGES:
                data = fetch_page(session, params, page)
                if data is None:
                    break

                ops = data.get("operations", [])
                if not ops:
                    break

                for op in ops:
                    total_scanned += 1
                    win_scanned += 1
                    if has_ntt(op):
                        all_records.append(extract_record(op, label))
                        total_ntt += 1
                        win_records += 1

                if len(ops) < PAGE_SIZE:
                    break
                page += 1
                time.sleep(SLEEP_SEC)

            elapsed = time.time() - t0
            print(f"  扫描{win_scanned} NTT+{win_records} 累计{total_ntt:,} ({elapsed:.0f}s)")

            # 增量保存
            if total_ntt - last_save >= SAVE_EVERY:
                save_json(all_records, tag="  (进行中)")
                last_save = total_ntt

    elapsed = time.time() - t0
    print(f"\n  完成: {elapsed:.0f}s, 扫描 {total_scanned:,}, NTT {total_ntt:,}")

    # 最终保存
    save_json(all_records, tag="  [最终]")

    # 统计
    completed = [r for r in all_records if r["dst_status"] == "completed"]
    latencies = [r["latency_seconds"] for r in completed if r["latency_seconds"] is not None]

    by_dir = {}
    by_token = {}
    total_usd = 0
    for r in all_records:
        by_dir[r["direction"]] = by_dir.get(r["direction"], 0) + 1
        sym = r["token_symbol"] or "?"
        by_token[sym] = by_token.get(sym, 0) + 1
        try:
            total_usd += float(r["usd_amount"] or 0)
        except (TypeError, ValueError):
            pass

    print(f"\n{'=' * 65}")
    print(f"  统计")
    print(f"{'=' * 65}")
    print(f"  总记录:     {len(all_records):,}")
    print(f"  已完成:     {len(completed):,}")

    for d, cnt in sorted(by_dir.items()):
        print(f"  {d}:    {cnt:,}")

    print(f"  总USD:      ${total_usd:,.2f}")

    print(f"\n  Token分布:")
    for sym, cnt in sorted(by_token.items(), key=lambda x: -x[1])[:15]:
        print(f"    {sym:<15s} {cnt:>8,}")

    if latencies:
        latencies.sort()
        print(f"\n  延迟 ({len(latencies)} 条):")
        print(f"    中位数: {latencies[len(latencies)//2]:.1f}s")
        print(f"    平均:   {sum(latencies)/len(latencies):.1f}s")
        print(f"    最小:   {min(latencies):.1f}s")
        print(f"    最大:   {max(latencies):.1f}s")


if __name__ == "__main__":
    main()
