#!/usr/bin/env python3
"""
解析 Ethereum 交易，显示所有 token 转账详情。

用法:
    python wormhole_data/parse_eth_tx.py <tx_hash>
    python wormhole_data/parse_eth_tx.py <tx_hash1> <tx_hash2> ...
"""

import requests
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CHAINSTACK_URL = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"
ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
API_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# 常见token缓存
TOKEN_CACHE = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    "0x814e0908b12a99fecf5bc101bb5d0b8b5cdf7d26": ("MDT", 18),
    "0x1f573d6fb3f13d689ff844b4ce37794d79a7ff1c": ("BNT", 18),
}


def rpc_call(method, params):
    """直接调用 Chainstack ETH RPC"""
    for attempt in range(3):
        try:
            resp = requests.post(CHAINSTACK_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            return resp.json().get("result")
        except Exception:
            time.sleep(1)
    return None


def etherscan(params):
    """Etherscan API（仅用于查token info等RPC不支持的）"""
    params["chainid"] = 1
    params["apikey"] = API_KEY
    for attempt in range(3):
        try:
            resp = requests.get(ETHERSCAN_URL, params=params, timeout=30)
            return resp.json()
        except Exception:
            time.sleep(1)
    return {}


def get_token_info(addr):
    addr = addr.lower()
    if addr in TOKEN_CACHE:
        return TOKEN_CACHE[addr]
    try:
        data = etherscan({"module": "token", "action": "tokeninfo", "contractaddress": addr})
        if data.get("status") == "1" and data.get("result"):
            r = data["result"][0] if isinstance(data["result"], list) else data["result"]
            sym = r.get("symbol", "?")
            dec = int(r.get("divisor") or r.get("decimals") or "18")
            TOKEN_CACHE[addr] = (sym, dec)
            return (sym, dec)
    except Exception:
        pass
    TOKEN_CACHE[addr] = ("?", 18)
    return ("?", 18)


def parse_tx(txhash):
    # 交易基础信息 (Chainstack RPC)
    tx = rpc_call("eth_getTransactionByHash", [txhash])
    receipt = rpc_call("eth_getTransactionReceipt", [txhash])

    if not tx or not receipt:
        print(f"获取失败: {txhash}")
        return None

    # 区块时间 (Chainstack RPC)
    block_num = int(tx.get("blockNumber", "0x0"), 16)
    block = rpc_call("eth_getBlockByNumber", [tx.get("blockNumber"), False])
    block_ts = int(block.get("timestamp", "0x0"), 16) if block else 0
    block_time = datetime.fromtimestamp(block_ts, tz=timezone.utc).isoformat() if block_ts else "?"

    sender = tx.get("from", "").lower()
    value_eth = int(tx.get("value", "0x0"), 16) / 1e18
    gas_used = int(receipt.get("gasUsed", "0x0"), 16)
    gas_price = int(tx.get("gasPrice", "0x0"), 16)
    fee_eth = gas_used * gas_price / 1e18

    print("=" * 70)
    print(f"TX: {txhash}")
    print(f"时间: {block_time}")
    print(f"区块: {block_num}")
    print(f"From: {tx.get('from')}")
    print(f"To:   {tx.get('to')}")
    print(f"Value: {value_eth} ETH")
    print(f"Gas: {gas_used:,} (fee: {fee_eth:.6f} ETH)")
    print(f"Status: {'成功' if receipt.get('status') == '0x1' else '失败'}")
    print()

    # 解析Transfer事件
    transfers = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics or topics[0] != TRANSFER_TOPIC:
            continue
        frm = "0x" + topics[1][-40:] if len(topics) > 1 else "?"
        to = "0x" + topics[2][-40:] if len(topics) > 2 else "?"
        raw = int(log.get("data", "0x0"), 16)
        contract = log.get("address", "").lower()
        sym, dec = get_token_info(contract)
        amount = raw / (10 ** dec)

        tag = ""
        if frm.lower() == sender:
            tag = " ← 卖出"
        if to.lower() == sender:
            tag = " ← 买入"

        transfers.append({
            "token": sym, "contract": contract,
            "amount": amount, "from": frm, "to": to,
            "is_sender_out": frm.lower() == sender,
            "is_sender_in": to.lower() == sender,
        })
        print(f"  {sym:<10s} {amount:>22,.4f}  {frm[:12]}... → {to[:12]}...{tag}")

    # 总结
    out_tokens = [t for t in transfers if t["is_sender_out"]]
    in_tokens = [t for t in transfers if t["is_sender_in"]]

    if out_tokens or in_tokens:
        print(f"\n总结 ({sender[:12]}...):")
        for t in out_tokens:
            print(f"  卖出: {t['amount']:>20,.4f} {t['token']}")
        for t in in_tokens:
            print(f"  买入: {t['amount']:>20,.4f} {t['token']}")
        if value_eth > 0:
            print(f"  发送: {value_eth:.6f} ETH")

    return {
        "hash": txhash,
        "time": block_time,
        "block": block_num,
        "from": tx.get("from"),
        "to": tx.get("to"),
        "value_eth": value_eth,
        "fee_eth": fee_eth,
        "status": "success" if receipt.get("status") == "0x1" else "failed",
        "transfers": transfers,
        "summary": {
            "sold": [{"token": t["token"], "amount": t["amount"]} for t in out_tokens],
            "bought": [{"token": t["token"], "amount": t["amount"]} for t in in_tokens],
        },
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python wormhole_data/parse_eth_tx.py <tx_hash> [tx_hash2 ...]")
        sys.exit(1)

    results = []
    for txhash in sys.argv[1:]:
        result = parse_tx(txhash)
        if result:
            results.append(result)
        print()

    if len(results) > 1:
        out_path = Path(__file__).parent / "use" / "parsed_eth_txs.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"保存: {out_path.name}")


if __name__ == "__main__":
    main()
