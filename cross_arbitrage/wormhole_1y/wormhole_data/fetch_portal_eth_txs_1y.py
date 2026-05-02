#!/usr/bin/env python3
"""
获取 Wormhole Portal Token Bridge (ETH) 过去1年的所有交易。

合约: 0x3ee18B2214AFF97000D974cf647E7C347E8fa585
Etherscan限制 page*offset<=10000，所以用 endblock 递减来分段获取。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_portal_eth_txs_1y.py
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
API_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"
CONTRACT = "0x3ee18B2214AFF97000D974cf647E7C347E8fa585"

OUTPUT = Path(__file__).parent / "use" / "portal_full" / "portal_eth_txs_all.json"

DAYS = 0  # 0 = 全部历史数据
CUTOFF = 0  # 不做时间截断
BATCH = 10000  # 每次最多拿10000条


def fetch_txs(startblock, endblock):
    """获取指定区块范围的交易，按区块降序"""
    params = {
        "chainid": 1,
        "module": "account",
        "action": "txlist",
        "address": CONTRACT,
        "startblock": startblock,
        "endblock": endblock,
        "page": 1,
        "offset": BATCH,
        "sort": "desc",
        "apikey": API_KEY,
    }
    for attempt in range(5):
        try:
            resp = requests.get(ETHERSCAN_URL, params=params, timeout=30)
            data = resp.json()
            if data.get("status") == "1":
                return data.get("result", [])
            msg = data.get("message", "")
            if "rate limit" in msg.lower():
                time.sleep(2)
                continue
            if "no transactions found" in msg.lower():
                return []
            return []
        except Exception as e:
            print(f"    请求异常: {e}", flush=True)
            time.sleep(2)
    return []


def save(txs, last_endblock, done=False):
    output = {
        "meta": {
            "contract": CONTRACT,
            "contract_name": "Wormhole Portal Token Bridge (ETH)",
            "days": DAYS,
            "total_transactions": len(txs),
            "time_earliest": txs[-1]["time"] if txs else None,
            "time_latest": txs[0]["time"] if txs else None,
            "cutoff": datetime.fromtimestamp(CUTOFF, tz=timezone.utc).isoformat(),
            "last_endblock": last_endblock,
            "complete": done,
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "transactions": txs,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def main():
    print(f"合约: {CONTRACT}")
    print(f"范围: 过去{DAYS}天")
    print()

    # 断点续跑
    txs = []
    endblock = 99999999
    seen = set()
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("meta", {}).get("complete"):
            print(f"已完成，共 {len(data['transactions'])} 条")
            return
        txs = data.get("transactions", [])
        endblock = data["meta"].get("last_endblock", 99999999)
        seen = set(tx["hash"] for tx in txs)
        print(f"断点续跑: 已有 {len(txs)} 条, 从区块 {endblock} 继续\n")

    round_num = 0

    while True:
        result = fetch_txs(0, endblock)
        if not result or not isinstance(result, list):
            print(f"  无数据，结束")
            break

        round_num += 1
        stop = False
        new_count = 0
        min_block = endblock

        for tx in result:
            ts = int(tx.get("timeStamp", 0))
            if CUTOFF and ts and ts < CUTOFF:
                stop = True
                break

            h = tx.get("hash")
            bn = int(tx.get("blockNumber", 0))
            if bn < min_block:
                min_block = bn

            if h in seen:
                continue
            seen.add(h)
            txs.append({
                "hash": h,
                "ts": ts,
                "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                "block": bn,
                "from": tx.get("from"),
                "to": tx.get("to"),
                "value_eth": int(tx.get("value", 0)) / 1e18,
                "is_error": tx.get("isError"),
                "method": tx.get("functionName", "")[:60],
                "gas_used": tx.get("gasUsed"),
            })
            new_count += 1

        earliest = txs[-1]["time"] if txs else "?"
        print(f"  第{round_num}轮  endblock={endblock}  +{new_count}条  总{len(txs):,}条  最早: {earliest}", flush=True)

        # 下一轮从最小区块-1开始
        endblock = min_block - 1

        # 每5轮保存
        if round_num % 5 == 0:
            save(txs, endblock)
            print(f"    已保存", flush=True)

        if stop or len(result) < BATCH:
            break

        time.sleep(0.4)

    save(txs, endblock, done=True)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"  总交易: {len(txs):,} 条")
    if txs:
        print(f"  时间: {txs[-1]['time']} → {txs[0]['time']}")
    print(f"  保存: {OUTPUT.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
