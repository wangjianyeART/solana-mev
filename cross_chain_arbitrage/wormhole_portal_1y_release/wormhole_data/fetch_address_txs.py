#!/usr/bin/env python3
"""
获取跨链地址的交易签名/hash 列表（轻量版，不获取详细数据）。

SOL: Chainstack getSignaturesForAddress
ETH: Etherscan txlist API

输入: recent_30d/matched/addresses.json
输出: recent_30d/matched/address_txs/
  - sol_sigs.json   — {address: [{sig, slot, blockTime, err}, ...]}
  - eth_txs.json    — {address: [{hash, blockNumber, timeStamp, from, to, value, ...}, ...]}

用法:
    python wormhole_data/fetch_address_txs.py
"""

import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

DIR = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
ADDR_FILE = DIR / "addresses.json"
OUT_DIR = DIR / "address_txs"

SOL_RPC = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"
ETHERSCAN_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"

# 40 天前的 timestamp
DAYS = 40
NOW = int(datetime.now(timezone.utc).timestamp())
SINCE_TS = NOW - DAYS * 86400
print(f"时间范围: {datetime.fromtimestamp(SINCE_TS, tz=timezone.utc).isoformat()} ~ now")


def fetch_sol_sigs(address):
    """获取 SOL 地址的所有签名（过去40天）"""
    all_sigs = []
    before = None
    while True:
        params = {
            "limit": 1000,
        }
        if before:
            params["before"] = before

        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, params]
        }

        try:
            resp = requests.post(SOL_RPC, json=payload, timeout=30)
            result = resp.json().get("result", [])
        except Exception as e:
            print(f"    ERROR: {e}")
            break

        if not result:
            break

        for sig_info in result:
            bt = sig_info.get("blockTime", 0)
            if bt and bt < SINCE_TS:
                # 已超出时间范围
                return all_sigs

            all_sigs.append({
                "sig": sig_info["signature"],
                "slot": sig_info.get("slot"),
                "blockTime": bt,
                "err": sig_info.get("err"),
            })

        if len(result) < 1000:
            break

        before = result[-1]["signature"]
        time.sleep(0.3)

    return all_sigs


def fetch_eth_txs(address):
    """获取 ETH 地址的所有交易（过去40天）"""
    all_txs = []
    page = 1
    while True:
        url = (f"https://api.etherscan.io/v2/api?chainid=1&module=account"
               f"&action=txlist&address={address}"
               f"&startblock=0&endblock=99999999"
               f"&page={page}&offset=10000&sort=desc"
               f"&apikey={ETHERSCAN_KEY}")

        try:
            resp = requests.get(url, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"    ERROR: {e}")
            break

        if data.get("status") != "1" or not data.get("result"):
            break

        records = data["result"]
        stop = False
        for tx in records:
            ts = int(tx.get("timeStamp", 0))
            if ts < SINCE_TS:
                stop = True
                break
            all_txs.append({
                "hash": tx["hash"],
                "blockNumber": int(tx.get("blockNumber", 0)),
                "timeStamp": ts,
                "from": tx.get("from", ""),
                "to": tx.get("to", ""),
                "value": tx.get("value", "0"),
                "methodId": tx.get("methodId", ""),
                "isError": tx.get("isError", "0"),
            })

        if stop or len(records) < 10000:
            break

        page += 1
        time.sleep(0.25)

    return all_txs


def main():
    addrs = json.load(open(ADDR_FILE, encoding="utf-8"))
    sol_addrs = addrs["sol_senders"]
    eth_addrs = addrs["eth_from"]

    OUT_DIR.mkdir(exist_ok=True)

    # === SOL ===
    sol_out = OUT_DIR / "sol_sigs.json"
    sol_results = {}

    # 如果已有部分结果，增量续跑
    if sol_out.exists():
        sol_results = json.load(open(sol_out, encoding="utf-8"))
        print(f"SOL 已有 {len(sol_results)} 个地址，增量续跑")

    print(f"\n=== SOL: {len(sol_addrs)} 个地址 ===")
    for i, addr in enumerate(sol_addrs):
        if addr in sol_results:
            continue
        print(f"  [{i+1}/{len(sol_addrs)}] {addr[:20]}...", end=" ", flush=True)
        sigs = fetch_sol_sigs(addr)
        sol_results[addr] = sigs
        print(f"{len(sigs)} sigs")
        time.sleep(0.3)

        # 每20个保存一次
        if (i + 1) % 20 == 0:
            with open(sol_out, "w", encoding="utf-8") as f:
                json.dump(sol_results, f, ensure_ascii=False)

    with open(sol_out, "w", encoding="utf-8") as f:
        json.dump(sol_results, f, ensure_ascii=False)

    total_sol_sigs = sum(len(v) for v in sol_results.values())
    print(f"\nSOL 完成: {len(sol_results)} 地址, {total_sol_sigs} 签名")

    # === ETH ===
    eth_out = OUT_DIR / "eth_txs.json"
    eth_results = {}

    if eth_out.exists():
        eth_results = json.load(open(eth_out, encoding="utf-8"))
        print(f"ETH 已有 {len(eth_results)} 个地址，增量续跑")

    print(f"\n=== ETH: {len(eth_addrs)} 个地址 ===")
    for i, addr in enumerate(eth_addrs):
        if addr in eth_results:
            continue
        print(f"  [{i+1}/{len(eth_addrs)}] {addr[:20]}...", end=" ", flush=True)
        txs = fetch_eth_txs(addr)
        eth_results[addr] = txs
        print(f"{len(txs)} txs")
        time.sleep(0.25)

        if (i + 1) % 20 == 0:
            with open(eth_out, "w", encoding="utf-8") as f:
                json.dump(eth_results, f, ensure_ascii=False)

    with open(eth_out, "w", encoding="utf-8") as f:
        json.dump(eth_results, f, ensure_ascii=False)

    total_eth_txs = sum(len(v) for v in eth_results.values())
    print(f"\nETH 完成: {len(eth_results)} 地址, {total_eth_txs} 交易")

    print(f"\n输出:")
    print(f"  {sol_out}")
    print(f"  {eth_out}")


if __name__ == "__main__":
    main()
