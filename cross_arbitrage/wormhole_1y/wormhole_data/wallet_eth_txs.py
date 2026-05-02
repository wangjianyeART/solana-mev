#!/usr/bin/env python3
"""
查询 Ethereum 钱包过去7天的所有交易。

使用 Etherscan 免费 API（无需key，限5次/秒）。

用法:
    python wormhole_data/wallet_eth_txs.py <钱包地址>

示例:
    python wormhole_data/wallet_eth_txs.py 0xfdff0b569f14af593d446e51b3e42f502124ac85
"""

import requests
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ETHERSCAN_URL = "https://api.etherscan.io/api"
# 免费API不需要key，但有速率限制(5/s)。如果你有key可以填这里：
API_KEY = ""

OUTPUT_DIR = Path(__file__).parent / "wallet_txs"
OUTPUT_DIR.mkdir(exist_ok=True)

DAYS = 7
CUTOFF = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())


def fetch_etherscan(module, action, address, extra_params=None):
    """通用 Etherscan 查询"""
    params = {
        "module": module,
        "action": action,
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "sort": "desc",
    }
    if API_KEY:
        params["apikey"] = API_KEY
    if extra_params:
        params.update(extra_params)

    for attempt in range(3):
        try:
            resp = requests.get(ETHERSCAN_URL, params=params, timeout=30)
            data = resp.json()
            if data.get("status") == "1":
                return data.get("result", [])
            elif "rate limit" in data.get("message", "").lower():
                time.sleep(2)
                continue
            else:
                return data.get("result", [])
        except Exception as e:
            if attempt == 2:
                print(f"  请求失败: {e}")
                return []
            time.sleep(2)


def main():
    if len(sys.argv) < 2:
        print("用法: python wormhole_data/wallet_eth_txs.py <钱包地址>")
        sys.exit(1)

    address = sys.argv[1]
    print(f"钱包: {address}")
    print(f"范围: 过去{DAYS}天")
    print()

    # 1. 普通交易
    print("获取普通交易...", flush=True)
    normal_txs = fetch_etherscan("account", "txlist", address)
    time.sleep(0.3)

    # 2. 内部交易
    print("获取内部交易...", flush=True)
    internal_txs = fetch_etherscan("account", "txlistinternal", address)
    time.sleep(0.3)

    # 3. ERC20 Token 转账
    print("获取ERC20转账...", flush=True)
    token_txs = fetch_etherscan("account", "tokentx", address)
    time.sleep(0.3)

    # 过滤7天内
    def filter_recent(txs):
        if not isinstance(txs, list):
            return []
        return [tx for tx in txs if int(tx.get("timeStamp", 0)) >= CUTOFF]

    normal_recent = filter_recent(normal_txs)
    internal_recent = filter_recent(internal_txs)
    token_recent = filter_recent(token_txs)

    print(f"\n  普通交易: {len(normal_recent)} 笔 (总{len(normal_txs) if isinstance(normal_txs, list) else 0})")
    print(f"  内部交易: {len(internal_recent)} 笔")
    print(f"  ERC20转账: {len(token_recent)} 笔")

    # 整理普通交易
    records_normal = []
    for tx in normal_recent:
        ts = int(tx.get("timeStamp", 0))
        records_normal.append({
            "hash": tx.get("hash"),
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
            "timestamp": ts,
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value_eth": int(tx.get("value", 0)) / 1e18,
            "gas_used": tx.get("gasUsed"),
            "gas_price": tx.get("gasPrice"),
            "is_error": tx.get("isError"),
            "method": tx.get("functionName", "")[:50],
        })

    # 整理ERC20转账
    records_token = []
    for tx in token_recent:
        ts = int(tx.get("timeStamp", 0))
        decimals = int(tx.get("tokenDecimal", 18))
        raw_value = int(tx.get("value", 0))
        records_token.append({
            "hash": tx.get("hash"),
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
            "timestamp": ts,
            "from": tx.get("from"),
            "to": tx.get("to"),
            "token_symbol": tx.get("tokenSymbol"),
            "token_name": tx.get("tokenName"),
            "token_address": tx.get("contractAddress"),
            "amount": raw_value / (10 ** decimals) if decimals else raw_value,
        })

    # 保存
    short_addr = address[:10]
    out_path = OUTPUT_DIR / f"eth_{short_addr}.json"
    output = {
        "meta": {
            "address": address,
            "chain": "ethereum",
            "days": DAYS,
            "normal_txs": len(records_normal),
            "token_transfers": len(records_token),
            "internal_txs": len(internal_recent),
            "time_earliest": None,
            "time_latest": None,
        },
        "normal_transactions": records_normal,
        "token_transfers": records_token,
    }

    all_times = [r["time"] for r in records_normal + records_token if r.get("time")]
    if all_times:
        output["meta"]["time_earliest"] = min(all_times)
        output["meta"]["time_latest"] = max(all_times)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n保存: {out_path.name} ({size_mb:.1f} MB)")

    # 摘要
    if records_token:
        token_counts = {}
        for r in records_token:
            sym = r.get("token_symbol") or "?"
            token_counts[sym] = token_counts.get(sym, 0) + 1
        print(f"\nERC20 Token分布:")
        for sym, cnt in sorted(token_counts.items(), key=lambda x: -x[1])[:15]:
            print(f"  {sym:<15s} {cnt:>5}")


if __name__ == "__main__":
    main()
