#!/usr/bin/env python3
"""
Wormhole  SOL <-> ETH  扩充版采集器（流式写入 JSONL，不爆内存）

现有数据: wormhole_sol_eth_100k.json  (42k total / 36k valid, 2026-01-01~03-16)
目标: 3 倍有效记录 (~105k valid)  →  扩大日期范围到 2025-01-01~2026-03-26

内存策略:
  - 30 天滑动时间窗口，绕开 API 单次分页上限
  - 每个窗口每页 50 条立即写盘，不累积
  - 每 10 个窗口 flush 一次
  - 峰值内存仅为单页 50 条 + 少量变量

输出:
  wormhole_sol_eth_300k.jsonl   每行一条 JSON 记录
  wormhole_sol_eth_300k_meta.json  元数据摘要

用法:
    python fetch_wormhole_300k.py
"""

import requests
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BASE_URL = "https://api.wormholescan.io/api/v1/operations"

# 原来: 2026-01-01 ~ 2026-03-16 (75 天 → 42k total / 36k valid)
# 现在: 2025-01-01 ~ 2026-03-26 (450 天 → 预估 250k+ total)
START_DATE = datetime(2025, 1, 1, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 3, 26, tzinfo=timezone.utc)

WINDOW_DAYS = 30   # 30 天一个窗口
PAGE_SIZE   = 50
SLEEP_SEC   = 0.4
MAX_RETRIES = 5
MAX_PAGES   = 500  # 单窗口单方向最大页数（50×500=25000 条，足够 30 天）

DIRECTIONS = [
    {"label": "SOL→ETH", "sourceChain": 1, "targetChain": 2},
    {"label": "ETH→SOL", "sourceChain": 2, "targetChain": 1},
]

OUTPUT_DIR = Path(__file__).parent
JSONL_OUT  = OUTPUT_DIR / "wormhole_sol_eth_300k.jsonl"
META_OUT   = OUTPUT_DIR / "wormhole_sol_eth_300k_meta.json"

# ─── RECORD EXTRACTOR ────────────────────────────────────────────────────────

def parse_ts(ts_str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def extract_record(op, direction_label):
    content   = op.get("content", {}) or {}
    std       = content.get("standarizedProperties", {}) or {}
    src_chain = op.get("sourceChain", {}) or {}
    dst_chain = op.get("targetChain", {}) or {}
    data      = op.get("data", {}) or {}

    t_src = parse_ts(src_chain.get("timestamp"))
    t_dst = parse_ts(dst_chain.get("timestamp"))
    lat   = round((t_dst - t_src).total_seconds(), 1) if t_src and t_dst else None

    return {
        "id":             op.get("id"),
        "sequence":       op.get("sequence"),
        "direction":      direction_label,
        "src_status":     src_chain.get("status"),
        "dst_status":     dst_chain.get("status"),
        "app_ids":        ",".join(std.get("appIds", [])),
        "src_timestamp":  t_src.isoformat() if t_src else None,
        "src_tx_hash":    src_chain.get("transaction", {}).get("txHash"),
        "src_sender":     src_chain.get("from"),
        "src_fee":        src_chain.get("fee"),
        "src_fee_usd":    src_chain.get("feeUSD"),
        "dst_timestamp":  t_dst.isoformat() if t_dst else None,
        "dst_tx_hash":    dst_chain.get("transaction", {}).get("txHash"),
        "dst_receiver":   std.get("toAddress"),
        "dst_fee":        dst_chain.get("fee"),
        "dst_fee_usd":    dst_chain.get("feeUSD"),
        "dst_relayer":    dst_chain.get("from"),
        "latency_seconds": lat,
        "token_symbol":   data.get("symbol"),
        "token_amount":   data.get("tokenAmount"),
        "usd_amount":     data.get("usdAmount"),
        "token_address":  std.get("tokenAddress"),
        "token_chain":    std.get("tokenChain"),
        "amount_raw":     std.get("amount"),
        "guardian_set":   op.get("vaa", {}).get("guardianSetIndex"),
    }


# ─── HTTP ─────────────────────────────────────────────────────────────────────

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


# ─── STREAMING WINDOW ────────────────────────────────────────────────────────

def stream_window(session, direction, win_start, win_end, f_out):
    """采集单个时间窗口 + 单方向，立即写盘，返回 (written, valid)"""
    written = 0
    valid   = 0
    page    = 0

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

        ops = data.get("operations") or []
        if not ops:
            break

        for op in ops:
            rec = extract_record(op, direction["label"])
            f_out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
            if rec["dst_status"] == "completed":
                valid += 1

        if len(ops) < PAGE_SIZE:
            break

        page += 1
        time.sleep(SLEEP_SEC)

    return written, valid


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    session = requests.Session()

    # 生成时间窗口（按时间正序）
    windows = []
    cur = START_DATE
    while cur < END_DATE:
        nxt = min(cur + timedelta(days=WINDOW_DAYS), END_DATE)
        windows.append((cur, nxt))
        cur = nxt

    total_tasks   = len(windows) * len(DIRECTIONS)
    total_written = 0
    total_valid   = 0
    task_idx      = 0

    print("=" * 70)
    print("Wormhole SOL <-> ETH  扩充版采集器（流式写入 JSONL）")
    print(f"日期范围: {START_DATE.date()} -> {END_DATE.date()}")
    print(f"时间窗口: {len(windows)} 个 × {len(DIRECTIONS)} 方向 = {total_tasks} 批次")
    print(f"输出: {JSONL_OUT.name}")
    print("内存策略: 每窗口每页 50 条立即写盘，无内存积累")
    print("=" * 70)

    t0 = time.time()

    with open(JSONL_OUT, "w", encoding="utf-8") as f_out:
        for win_start, win_end in windows:
            for direction in DIRECTIONS:
                task_idx += 1
                label = direction["label"]

                w, v = stream_window(session, direction, win_start, win_end, f_out)
                total_written += w
                total_valid   += v

                # 每 10 个任务刷盘
                if task_idx % 10 == 0:
                    f_out.flush()

                elapsed = time.time() - t0
                rate    = total_written / elapsed if elapsed > 0 else 0
                print(
                    f"  [{task_idx:>5}/{total_tasks}] {label} "
                    f"{win_start.strftime('%Y-%m-%d')}~{win_end.strftime('%m-%d')} "
                    f"| +{w:>5} | 总: {total_written:>7,} | "
                    f"valid: {total_valid:>7,} | {rate:.0f} rec/s",
                    end="\r"
                )
                sys.stdout.flush()

            # 每完成一个完整窗口（两方向）打印换行
            if task_idx % (10 * len(DIRECTIONS)) == 0:
                elapsed = time.time() - t0
                print(
                    f"\n  [{task_idx}/{total_tasks}] 总写入: {total_written:,} | "
                    f"valid: {total_valid:,} | 耗时: {elapsed/60:.1f}min"
                )

    elapsed = time.time() - t0

    meta = {
        "fetched_at":      datetime.now(timezone.utc).isoformat(),
        "start_date":      START_DATE.isoformat(),
        "end_date":        END_DATE.isoformat(),
        "window_days":     WINDOW_DAYS,
        "directions":      [d["label"] for d in DIRECTIONS],
        "total_records":   total_written,
        "valid_records":   total_valid,
        "elapsed_minutes": round(elapsed / 60, 1),
        "output_format":   "jsonl",
        "output_file":     JSONL_OUT.name,
    }
    with open(META_OUT, "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    size_mb = JSONL_OUT.stat().st_size / 1024 / 1024
    print(f"\n\n完成: {total_written:,} 条写入, valid {total_valid:,}  ({elapsed/60:.1f} min)")
    print(f"JSONL: {JSONL_OUT.name}  ({size_mb:.1f} MB)")
    print(f"META:  {META_OUT.name}")


if __name__ == "__main__":
    main()
