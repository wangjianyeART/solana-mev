#!/usr/bin/env python3
"""
Wormhole Solana -> Ethereum 数据采集器

数据来源: Wormholescan API (https://api.wormholescan.io)
sourceChain=1 (Solana), targetChain=2 (Ethereum)

每条记录提取：
  - 时间戳 (源链 + 目标链)
  - 跨链延迟 (秒)
  - 交易哈希 (Solana + Ethereum)
  - 发送方 / 接收方地址
  - 代币 symbol / 金额 / USD 价值
  - 手续费 USD (两端)
  - 应用层 ID (PORTAL_TOKEN_BRIDGE / MAYAN_SWIFT / WORMHOLE_GATEWAY 等)

用法:
    pip install requests pandas
    python fetch_wormhole.py

可调参数见文件顶部 CONFIG 部分
"""

import requests
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BASE_URL   = "https://api.wormholescan.io/api/v1/operations"

# 时间范围（UTC，ISO 8601）
START_TIME = "2026-01-01T00:00:00.000Z"
END_TIME   = "2026-02-05T00:00:00.000Z"

# 只抓 Portal Token Bridge（纯 Wormhole）；改成 None 则抓全部 appId
# 可选值: "PORTAL_TOKEN_BRIDGE" | "MAYAN_SWIFT" | "WORMHOLE_GATEWAY" | None
APP_ID_FILTER = "PORTAL_TOKEN_BRIDGE"

PAGE_SIZE   = 50     # API 单页上限
MAX_PAGES   = 2000   # 安全上限，防止死循环
SLEEP_SEC   = 0.5    # 每页请求间隔（秒）
MAX_RETRIES = 5      # SSL / 网络错误最大重试次数

OUTPUT_DIR = Path(__file__).parent
_date_tag  = f"{START_TIME[:10]}_{END_TIME[:10]}"
JSON_OUT   = OUTPUT_DIR / f"wormhole_sol_to_eth_{_date_tag}.json"
CSV_OUT    = OUTPUT_DIR / f"wormhole_sol_to_eth_{_date_tag}.csv"

# ─── HELPERS ─────────────────────────────────────────────────────────────────

def parse_ts(ts_str):
    if not ts_str:
        return None
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def latency_seconds(t_src, t_dst):
    if t_src and t_dst:
        return round((t_dst - t_src).total_seconds(), 1)
    return None


def extract_record(op):
    """从单条 API 响应中抽取所有需要的字段"""

    content   = op.get("content", {})
    std       = content.get("standarizedProperties", {})
    src_chain = op.get("sourceChain", {})
    dst_chain = op.get("targetChain", {})
    data      = op.get("data", {})

    t_src = parse_ts(src_chain.get("timestamp"))
    t_dst = parse_ts(dst_chain.get("timestamp"))
    lat   = latency_seconds(t_src, t_dst)

    # status: 以目标链的状态为准（"completed" / "pending" / None）
    dst_status = dst_chain.get("status")   # "completed" 表示已完成
    src_status = src_chain.get("status")   # "confirmed"

    return {
        # 操作标识
        "id":               op.get("id"),
        "sequence":         op.get("sequence"),
        "src_status":       src_status,
        "dst_status":       dst_status,    # "completed" = 已在 ETH 完成

        # 应用层
        "app_ids":          ",".join(std.get("appIds", [])),

        # 源链 (Solana)
        "src_timestamp":    t_src.isoformat() if t_src else None,
        "src_tx_hash":      src_chain.get("transaction", {}).get("txHash"),
        "src_sender":       src_chain.get("from"),
        "src_fee_sol":      src_chain.get("fee"),
        "src_fee_usd":      src_chain.get("feeUSD"),

        # 目标链 (Ethereum)
        "dst_timestamp":    t_dst.isoformat() if t_dst else None,
        "dst_tx_hash":      dst_chain.get("transaction", {}).get("txHash"),
        "dst_receiver":     std.get("toAddress"),
        "dst_fee_eth":      dst_chain.get("fee"),
        "dst_fee_usd":      dst_chain.get("feeUSD"),
        "dst_relayer":      dst_chain.get("from"),   # 在 ETH 端提交 VAA 的地址

        # 延迟
        "latency_seconds":  lat,

        # 代币信息（直观字段）
        "token_symbol":     data.get("symbol"),
        "token_amount":     data.get("tokenAmount"),
        "usd_amount":       data.get("usdAmount"),

        # 代币信息（链上地址）
        "token_address":    std.get("tokenAddress"),
        "token_chain":      std.get("tokenChain"),
        "amount_raw":       std.get("amount"),

        # VAA
        "guardian_set":     op.get("vaa", {}).get("guardianSetIndex"),
    }


# ─── FETCH WITH RETRY ────────────────────────────────────────────────────────

def fetch_page(session, params, page):
    """带重试的单页请求"""
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


# ─── MAIN FETCH LOOP ──────────────────────────────────────────────────────────

def fetch_all():
    records = []
    page    = 0

    params = {
        "pageSize":    PAGE_SIZE,
        "sortOrder":   "ASC",
        "sourceChain": 1,
        "targetChain": 2,
        "startTime":   START_TIME,
        "endTime":     END_TIME,
    }
    if APP_ID_FILTER:
        params["appId"] = APP_ID_FILTER

    print(f"时间范围: {START_TIME}  ->  {END_TIME}")
    print(f"AppId 过滤: {APP_ID_FILTER or '全部'}")
    print("-" * 60)

    session = requests.Session()

    while page < MAX_PAGES:
        data = fetch_page(session, params, page)
        if data is None:
            break

        ops = data.get("operations", [])
        if not ops:
            print(f"\n第 {page} 页无数据，抓取完毕")
            break

        for op in ops:
            records.append(extract_record(op))

        total_api = data.get("total", "?")
        fetched   = len(records)
        print(f"  page {page:>4} | 本页 {len(ops):>3} 条 | 累计 {fetched:>6} / {total_api}", end="\r")
        sys.stdout.flush()

        if len(ops) < PAGE_SIZE:
            print(f"\n最后一页 (page={page}，共 {fetched} 条)")
            break

        page += 1
        time.sleep(SLEEP_SEC)

    return records


# ─── SAVE & SUMMARY ───────────────────────────────────────────────────────────

def save(records):
    if not records:
        print("没有数据可保存")
        return

    output = {
        "meta": {
            "fetched_at":    datetime.now(timezone.utc).isoformat(),
            "start_time":    START_TIME,
            "end_time":      END_TIME,
            "app_id_filter": APP_ID_FILTER,
            "source_chain":  "Solana (1)",
            "target_chain":  "Ethereum (2)",
            "total_records": len(records),
        },
        "records": records,
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\n JSON 已保存: {JSON_OUT}")

    if HAS_PANDAS:
        df = pd.DataFrame(records)
        df.to_csv(CSV_OUT, index=False)
        print(f" CSV 已保存: {CSV_OUT}")
    else:
        print("提示: pip install pandas 可生成 CSV")


def summary(records):
    if not records:
        return

    completed = [r for r in records if r["dst_status"] == "completed"]
    latencies = [r["latency_seconds"] for r in completed if r["latency_seconds"] is not None]

    app_counter = {}
    for r in records:
        for app in r["app_ids"].split(","):
            app = app.strip()
            if app:
                app_counter[app] = app_counter.get(app, 0) + 1

    print("\n" + "=" * 60)
    print("汇总统计")
    print("=" * 60)
    print(f"  总记录数:          {len(records)}")
    print(f"  ETH 已完成:        {len(completed)}")
    print(f"  未完成/待处理:     {len(records) - len(completed)}")

    if latencies:
        avg = sum(latencies) / len(latencies)
        med = sorted(latencies)[len(latencies) // 2]
        print(f"\n  跨链延迟 (秒，已完成交易)")
        print(f"    平均值:   {avg:.1f}")
        print(f"    中位数:   {med:.1f}")
        print(f"    最小值:   {min(latencies):.1f}")
        print(f"    最大值:   {max(latencies):.1f}")

    if app_counter:
        print(f"\n  应用层分布:")
        for app, cnt in sorted(app_counter.items(), key=lambda x: -x[1]):
            print(f"    {app:<35} {cnt:>6} 条")

    if HAS_PANDAS and records:
        df = pd.DataFrame(records)
        completed_df = df[df["dst_status"] == "completed"]
        if not completed_df.empty:
            print(f"\n  Top 10 代币 (按交易次数):")
            token_counts = completed_df["token_symbol"].value_counts().head(10)
            for sym, cnt in token_counts.items():
                print(f"    {str(sym):<12} {cnt:>6} 条")

            usd_col = pd.to_numeric(completed_df["usd_amount"], errors="coerce")
            total_usd = usd_col.sum()
            print(f"\n  总 USD 交易量: ${total_usd:,.0f}")


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Wormhole Solana -> Ethereum 数据采集器")
    print("=" * 60)

    records = fetch_all()
    save(records)
    summary(records)

    print("\n完成")
