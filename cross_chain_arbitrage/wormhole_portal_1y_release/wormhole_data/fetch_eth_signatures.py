#!/usr/bin/env python3
"""
批量获取 all_addresses.json 中所有 Ethereum 地址过去7天的交易记录。

使用 Etherscan 免费 API，获取普通交易 + ERC20转账。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_eth_signatures.py
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
API_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"

DIR = Path(__file__).parent / "use"
INPUT = DIR / "addresses" / "all_addresses.json"
OUTPUT = DIR / "signatures" / "eth_signatures.json"

DAYS = 7
CUTOFF = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())


def fetch_etherscan(module, action, address):
    params = {
        "chainid": 1,
        "module": module,
        "action": action,
        "address": address,
        "startblock": 0,
        "endblock": 99999999,
        "sort": "desc",
        "apikey": API_KEY,
    }

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
                print(f"    请求失败: {e}")
                return []
            time.sleep(2)
    return []


def process_address(address):
    """获取一个地址的普通交易和ERC20转账（过去7天）"""
    # 普通交易
    normal_txs = fetch_etherscan("account", "txlist", address)
    time.sleep(0.4)

    # ERC20转账
    token_txs = fetch_etherscan("account", "tokentx", address)
    time.sleep(0.4)

    # 过滤7天内
    def filter_recent(txs):
        if not isinstance(txs, list):
            return []
        return [tx for tx in txs if int(tx.get("timeStamp", 0)) >= CUTOFF]

    normal_recent = filter_recent(normal_txs)
    token_recent = filter_recent(token_txs)

    # 简化普通交易
    normal_out = []
    for tx in normal_recent:
        ts = int(tx.get("timeStamp", 0))
        normal_out.append({
            "hash": tx.get("hash"),
            "ts": ts,
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value_eth": int(tx.get("value", 0)) / 1e18,
            "is_error": tx.get("isError"),
            "method": tx.get("functionName", "")[:50],
        })

    # 简化ERC20转账
    token_out = []
    for tx in token_recent:
        ts = int(tx.get("timeStamp", 0))
        decimals = int(tx.get("tokenDecimal", 18))
        raw_value = int(tx.get("value", 0))
        token_out.append({
            "hash": tx.get("hash"),
            "ts": ts,
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
            "from": tx.get("from"),
            "to": tx.get("to"),
            "token_symbol": tx.get("tokenSymbol"),
            "token_address": tx.get("contractAddress"),
            "amount": raw_value / (10 ** decimals) if decimals else raw_value,
        })

    return {
        "normal_count": len(normal_out),
        "token_count": len(token_out),
        "normal_txs": normal_out,
        "token_transfers": token_out,
    }


def save(results, total_normal, total_token):
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {
                "total_addresses": len(results),
                "total_normal_txs": total_normal,
                "total_token_transfers": total_token,
                "days": DAYS,
                "cutoff": datetime.fromtimestamp(CUTOFF, tz=timezone.utc).isoformat(),
                "updated": datetime.now(timezone.utc).isoformat(),
            },
            "results": results,
        }, f, indent=2, ensure_ascii=False)


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    eth_addrs = [a["address"] for a in data["ethereum"]]
    print(f"Ethereum 地址: {len(eth_addrs)} 个")
    print(f"范围: 过去{DAYS}天\n")

    # 断点续跑
    results = {}
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            results = json.load(f).get("results", {})
        print(f"已有 {len(results)} 个，跳过\n")

    total = len(eth_addrs)
    done = 0
    total_normal = sum(v.get("normal_count", 0) for v in results.values())
    total_token = sum(v.get("token_count", 0) for v in results.values())

    for i, addr in enumerate(eth_addrs):
        if addr in results:
            continue

        result = process_address(addr)
        results[addr] = result
        done += 1
        total_normal += result["normal_count"]
        total_token += result["token_count"]

        print(f"  [{i+1}/{total}]  {addr[:24]}...  普通{result['normal_count']:>3}  ERC20{result['token_count']:>3}  "
              f"(累计 普通{total_normal} ERC20{total_token})", flush=True)

        if done % 10 == 0:
            save(results, total_normal, total_token)

        time.sleep(0.3)

    save(results, total_normal, total_token)

    active = sum(1 for r in results.values() if r["normal_count"] + r["token_count"] > 0)
    print(f"\n{'=' * 60}")
    print(f"  总地址: {len(results)}")
    print(f"  有交易: {active}")
    print(f"  普通交易: {total_normal} 笔")
    print(f"  ERC20转账: {total_token} 笔")
    print(f"  保存: {OUTPUT.name}")


if __name__ == "__main__":
    main()
