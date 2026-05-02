#!/usr/bin/env python3
"""
Wormhole Solana <-> Ethereum 大规模数据采集器

目标：采集 10万+ 有效（dst_status=completed）跨链记录
策略：
  - 30 天滑动时间窗口，绕开 API 分页上限（~15k/查询）
  - 双向采集：SOL→ETH (sourceChain=1,targetChain=2) + ETH→SOL (sourceChain=2,targetChain=1)
  - 不限 appId，获取所有 Wormhole 协议的跨链
  - 达到目标数量后自动停止

用法:
    pip install requests pandas
    python fetch_wormhole_100k.py
"""

import requests
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BASE_URL = "https://api.wormholescan.io/api/v1/operations"

# 目标有效记录数
TARGET_VALID = 999_999_999  # 不限制，采集全部数据

# 日期窗口大小（天）
WINDOW_DAYS = 30

# 起始日期范围（从最近往回推，直到凑够 TARGET_VALID）
END_DATE   = datetime(2026, 3, 16, tzinfo=timezone.utc)
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)

PAGE_SIZE   = 50
MAX_PAGES   = 500     # 单窗口最大页数（30天 * 420/天 = 12600 条 ≈ 252 页）
SLEEP_SEC   = 0.4
MAX_RETRIES = 5

# 双向采集
DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT_DIR = Path(__file__).parent
JSON_OUT   = OUTPUT_DIR / "wormhole_sol_eth_100k.json"
CSV_OUT    = OUTPUT_DIR / "wormhole_sol_eth_100k.csv"

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
    lat   = latency_seconds(t_src, t_dst)

    dst_status = dst_chain.get("status")
    src_status = src_chain.get("status")

    return {
        "id":               op.get("id"),
        "sequence":         op.get("sequence"),
        "direction":        direction_label,
        "src_status":       src_status,
        "dst_status":       dst_status,
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

        "latency_seconds":  lat,

        "token_symbol":     data.get("symbol"),
        "token_amount":     data.get("tokenAmount"),
        "usd_amount":       data.get("usdAmount"),

        "token_address":    std.get("tokenAddress"),
        "token_chain":      std.get("tokenChain"),
        "amount_raw":       std.get("amount"),

        "guardian_set":     op.get("vaa", {}).get("guardianSetIndex"),
    }


# ─── FETCH WITH RETRY ────────────────────────────────────────────────────────

def fetch_page(session, params, page):
    p = {**params, "page": page}
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(BASE_URL, params=p, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"\n  page {page} 连续失败 {MAX_RETRIES} 次，跳过: {e}")
                return None
            wait = 2 ** attempt
            print(f"\n  page {page} 第 {attempt} 次失败，{wait}s 后重试: {e}")
            time.sleep(wait)


# ─── FETCH ONE WINDOW ────────────────────────────────────────────────────────

def fetch_window(session, direction, win_start, win_end):
    """采集一个时间窗口 + 一个方向的所有数据"""
    records = []
    page = 0

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
            records.append(extract_record(op, direction["label"]))

        if len(ops) < PAGE_SIZE:
            break

        page += 1
        time.sleep(SLEEP_SEC)

    return records


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    all_records = []
    valid_count = 0
    session = requests.Session()

    # 生成时间窗口（从 END_DATE 往回推）
    windows = []
    win_end = END_DATE
    while win_end > START_DATE:
        win_start = max(win_end - timedelta(days=WINDOW_DAYS), START_DATE)
        windows.append((win_start, win_end))
        win_end = win_start

    windows.reverse()  # 按时间正序处理

    total_windows = len(windows) * len(DIRECTIONS)
    window_idx = 0

    print("=" * 70)
    print("Wormhole SOL <-> ETH 大规模数据采集器")
    print(f"目标: {TARGET_VALID:,} 条有效记录 (dst_status=completed)")
    print(f"日期范围: {START_DATE.date()} -> {END_DATE.date()}")
    print(f"时间窗口: {len(windows)} 个 x {len(DIRECTIONS)} 方向 = {total_windows} 批次")
    print("=" * 70)

    for win_start, win_end in windows:
        for direction in DIRECTIONS:
            window_idx += 1
            label = direction["label"]
            print(f"\n[{window_idx}/{total_windows}] {label}  {win_start.date()} ~ {win_end.date()}", end="")
            sys.stdout.flush()

            records = fetch_window(session, direction, win_start, win_end)
            new_valid = sum(1 for r in records if r["dst_status"] == "completed")

            all_records.extend(records)
            valid_count += new_valid

            print(f"  -> {len(records)} 条 (有效 {new_valid}), 累计有效: {valid_count:,}")

            if valid_count >= TARGET_VALID:
                print(f"\n已达到目标 {TARGET_VALID:,} 条有效记录！")
                return all_records, valid_count

    print(f"\n日期范围内共 {len(all_records):,} 条，有效 {valid_count:,}")
    if valid_count < TARGET_VALID:
        print(f"⚠ 未达到目标 {TARGET_VALID:,}，可考虑扩大 START_DATE")

    return all_records, valid_count


def save(records, valid_count):
    if not records:
        print("没有数据可保存")
        return

    output = {
        "meta": {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "start_date":    START_DATE.isoformat(),
            "end_date":      END_DATE.isoformat(),
            "directions":    [d["label"] for d in DIRECTIONS],
            "app_id_filter": None,
            "total_records": len(records),
            "valid_records": valid_count,
        },
        "records": records,
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nJSON 已保存: {JSON_OUT}  ({JSON_OUT.stat().st_size / 1024 / 1024:.1f} MB)")

    if HAS_PANDAS:
        df = pd.DataFrame(records)
        df.to_csv(CSV_OUT, index=False)
        print(f"CSV 已保存: {CSV_OUT}  ({CSV_OUT.stat().st_size / 1024 / 1024:.1f} MB)")


def summary(records, valid_count):
    if not records:
        return

    completed = [r for r in records if r["dst_status"] == "completed"]
    latencies = [r["latency_seconds"] for r in completed if r["latency_seconds"] is not None]

    # 方向统计
    dir_counts = {}
    for r in records:
        d = r["direction"]
        dir_counts[d] = dir_counts.get(d, 0) + 1

    # 应用层统计
    app_counter = {}
    for r in records:
        for app in r["app_ids"].split(","):
            app = app.strip()
            if app:
                app_counter[app] = app_counter.get(app, 0) + 1

    print("\n" + "=" * 70)
    print("汇总统计")
    print("=" * 70)
    print(f"  总记录数:      {len(records):,}")
    print(f"  有效 (completed): {valid_count:,}")
    print(f"  未完成/待处理:    {len(records) - valid_count:,}")

    print(f"\n  方向分布:")
    for d, cnt in sorted(dir_counts.items()):
        print(f"    {d:<12} {cnt:>8,} 条")

    if latencies:
        avg = sum(latencies) / len(latencies)
        med = sorted(latencies)[len(latencies) // 2]
        print(f"\n  跨链延迟 (秒，已完成)")
        print(f"    平均值: {avg:.1f}")
        print(f"    中位数: {med:.1f}")
        print(f"    最小值: {min(latencies):.1f}")
        print(f"    最大值: {max(latencies):.1f}")

    if app_counter:
        print(f"\n  应用层分布:")
        for app, cnt in sorted(app_counter.items(), key=lambda x: -x[1])[:15]:
            print(f"    {app:<35} {cnt:>8,} 条")

    if HAS_PANDAS and completed:
        df = pd.DataFrame(completed)
        print(f"\n  Top 10 代币:")
        token_counts = df["token_symbol"].value_counts().head(10)
        for sym, cnt in token_counts.items():
            print(f"    {str(sym):<12} {cnt:>8,} 条")

        usd_col = pd.to_numeric(df["usd_amount"], errors="coerce")
        total_usd = usd_col.sum()
        print(f"\n  总 USD 交易量: ${total_usd:,.0f}")


if __name__ == "__main__":
    records, valid_count = main()
    save(records, valid_count)
    summary(records, valid_count)
    print("\n完成")
