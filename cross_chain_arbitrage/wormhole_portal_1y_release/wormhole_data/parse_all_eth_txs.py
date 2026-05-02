#!/usr/bin/env python3
"""
批量解析 eth_signatures.json 中所有交易。

解析内容:
  - ERC20 Transfer 事件 (token swap/转账)
  - 原生 ETH 转账 (value + internal transactions)
  - ERC721/ERC1155 NFT 转账
  - Approval 授权事件

使用 Chainstack RPC 获取 receipt，无速率限制。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/parse_all_eth_txs.py
"""

import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path

CHAINSTACK_URL = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"

DIR = Path(__file__).parent / "use"
INPUT = DIR / "signatures" / "eth_signatures.json"
OUTPUT = DIR / "parsed" / "eth_txs_parsed.json"

# 事件签名
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
TRANSFER_SINGLE_TOPIC = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"  # ERC1155
TRANSFER_BATCH_TOPIC = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"  # ERC1155

# Token缓存
TOKEN_CACHE = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    "0x814e0908b12a99fecf5bc101bb5d0b8b5cdf7d26": ("MDT", 18),
    "0x1f573d6fb3f13d689ff844b4ce37794d79a7ff1c": ("BNT", 18),
    "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce": ("SHIB", 18),
    "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": ("AAVE", 18),
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": ("UNI", 18),
    "0x514910771af9ca656af840dff83e8264ecf986ca": ("LINK", 18),
    "0x0000000000000000000000000000000000000000": ("ETH", 18),
}


def rpc_call(method, params):
    for attempt in range(5):
        try:
            resp = requests.post(CHAINSTACK_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                if code == 429:
                    time.sleep(2 ** attempt)
                    continue
                return None
            return data.get("result")
        except Exception:
            time.sleep(1)
    return None


def get_token_info(addr):
    addr = addr.lower()
    if addr in TOKEN_CACHE:
        return TOKEN_CACHE[addr]
    # 查合约的name和decimals
    try:
        # decimals()
        dec_result = rpc_call("eth_call", [{"to": addr, "data": "0x313ce567"}, "latest"])
        decimals = int(dec_result, 16) if dec_result and dec_result != "0x" else 18

        # symbol()
        sym_result = rpc_call("eth_call", [{"to": addr, "data": "0x95d89b41"}, "latest"])
        symbol = "?"
        if sym_result and len(sym_result) > 66:
            # ABI编码的string
            try:
                hex_str = sym_result[130:]  # 跳过offset和length
                symbol = bytes.fromhex(hex_str).decode("utf-8").rstrip("\x00").strip()
                if not symbol or not symbol.isprintable():
                    symbol = "?"
            except Exception:
                symbol = "?"

        TOKEN_CACHE[addr] = (symbol, decimals)
        return (symbol, decimals)
    except Exception:
        TOKEN_CACHE[addr] = ("?", 18)
        return ("?", 18)


def parse_receipt(txhash, tx_from, tx_to, tx_value_wei, block_ts):
    """解析一笔交易的receipt，返回结构化结果"""
    receipt = rpc_call("eth_getTransactionReceipt", [txhash])
    if not receipt:
        return None

    sender = (tx_from or "").lower()
    status = "success" if receipt.get("status") == "0x1" else "failed"
    gas_used = int(receipt.get("gasUsed", "0x0"), 16)

    events = []

    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics:
            continue
        contract = log.get("address", "").lower()
        topic0 = topics[0]

        # ERC20 Transfer
        if topic0 == TRANSFER_TOPIC and len(topics) == 3:
            frm = "0x" + topics[1][-40:]
            to = "0x" + topics[2][-40:]
            raw = int(log.get("data", "0x0"), 16)
            sym, dec = get_token_info(contract)
            amount = raw / (10 ** dec) if dec else raw
            events.append({
                "type": "erc20_transfer",
                "token": sym,
                "contract": contract,
                "from": frm,
                "to": to,
                "amount": amount,
            })

        # ERC721 Transfer (same topic but topics[3] = tokenId, data = 0x)
        elif topic0 == TRANSFER_TOPIC and len(topics) == 4:
            frm = "0x" + topics[1][-40:]
            to = "0x" + topics[2][-40:]
            token_id = int(topics[3], 16)
            events.append({
                "type": "erc721_transfer",
                "contract": contract,
                "from": frm,
                "to": to,
                "token_id": token_id,
            })

        # ERC1155 TransferSingle
        elif topic0 == TRANSFER_SINGLE_TOPIC and len(topics) >= 4:
            frm = "0x" + topics[2][-40:]
            to = "0x" + topics[3][-40:]
            data = log.get("data", "0x")
            if len(data) >= 130:
                token_id = int(data[2:66], 16)
                amount = int(data[66:130], 16)
            else:
                token_id = 0
                amount = 0
            events.append({
                "type": "erc1155_transfer",
                "contract": contract,
                "from": frm,
                "to": to,
                "token_id": token_id,
                "amount": amount,
            })

        # Approval
        elif topic0 == APPROVAL_TOPIC and len(topics) >= 3:
            owner = "0x" + topics[1][-40:]
            spender = "0x" + topics[2][-40:]
            raw = int(log.get("data", "0x0"), 16)
            sym, dec = get_token_info(contract)
            events.append({
                "type": "approval",
                "token": sym,
                "contract": contract,
                "owner": owner,
                "spender": spender,
                "amount": raw / (10 ** dec) if dec and raw < 2**128 else "unlimited",
            })

    # 原生ETH转账
    eth_value = int(tx_value_wei, 16) / 1e18 if isinstance(tx_value_wei, str) else tx_value_wei / 1e18
    if eth_value > 0:
        events.insert(0, {
            "type": "eth_transfer",
            "token": "ETH",
            "from": sender,
            "to": (tx_to or "").lower(),
            "amount": eth_value,
        })

    # 汇总sender的净买卖
    # 1. 计算每个地址每个token的净变动
    net = {}  # addr -> {token: change}
    for e in events:
        if e["type"] not in ("erc20_transfer", "eth_transfer"):
            continue
        token = e.get("token", "?")
        if e["from"] not in net:
            net[e["from"]] = {}
        net[e["from"]][token] = net[e["from"]].get(token, 0) - e["amount"]
        if e["to"] not in net:
            net[e["to"]] = {}
        net[e["to"]][token] = net[e["to"]].get(token, 0) + e["amount"]

    # 2. sender直接的净变动
    sold = {}
    bought = {}
    for t, v in net.get(sender, {}).items():
        if v < -1e-8:
            sold[t] = sold.get(t, 0) + (-v)
        elif v > 1e-8:
            bought[t] = bought.get(t, 0) + v

    # 3. 如果sender没有直接的sold或bought，找变动最多的地址
    if not sold and not bought:
        best_addr, best_score = None, 0
        for addr, tokens in net.items():
            if addr == sender:
                continue
            nonzero = sum(1 for v in tokens.values() if abs(v) > 1e-8)
            total_vol = sum(abs(v) for v in tokens.values())
            score = nonzero * 1e12 + total_vol
            if score > best_score:
                best_score = score
                best_addr = addr
        if best_addr:
            for t, v in net[best_addr].items():
                if v < -1e-8:
                    sold[t] = sold.get(t, 0) + (-v)
                elif v > 1e-8:
                    bought[t] = bought.get(t, 0) + v

    summary_sold = [{"token": t, "amount": v} for t, v in sold.items()]
    summary_bought = [{"token": t, "amount": v} for t, v in bought.items()]

    return {
        "hash": txhash,
        "ts": block_ts,
        "time": datetime.fromtimestamp(block_ts, tz=timezone.utc).isoformat() if block_ts else None,
        "from": sender,
        "to": (tx_to or "").lower(),
        "status": status,
        "gas_used": gas_used,
        "events": events,
        "summary": {"sold": summary_sold, "bought": summary_bought},
    }


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    # 收集所有唯一tx hash + 基础信息
    tx_map = {}  # hash -> {from, to, value, ts}
    for addr, info in data["results"].items():
        for tx in info.get("normal_txs", []):
            h = tx.get("hash")
            if h and h not in tx_map:
                tx_map[h] = {
                    "from": tx.get("from", ""),
                    "to": tx.get("to", ""),
                    "value": tx.get("value_eth", 0),
                    "ts": tx.get("ts", 0),
                }
        for tx in info.get("token_transfers", []):
            h = tx.get("hash")
            if h and h not in tx_map:
                tx_map[h] = {
                    "from": tx.get("from", ""),
                    "to": tx.get("to", ""),
                    "value": 0,
                    "ts": tx.get("ts", 0),
                }

    print(f"唯一交易: {len(tx_map):,} 笔")
    print()

    # 断点续跑
    results = {}
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            existing = json.load(f)
        results = {r["hash"]: r for r in existing.get("transactions", [])}
        print(f"已有 {len(results)} 笔，跳过\n")

    total = len(tx_map)
    done = 0
    errors = 0
    token_cache_path = DIR / "cache" / "token_cache.json"

    # 加载token缓存
    if token_cache_path.exists():
        with open(token_cache_path, encoding="utf-8") as f:
            saved_cache = json.load(f)
        for k, v in saved_cache.items():
            TOKEN_CACHE[k] = tuple(v)
        print(f"Token缓存: {len(TOKEN_CACHE)} 个\n")

    to_process = sorted(set(tx_map.keys()) - set(results.keys()))
    done = 0
    errors = 0
    print(f"待处理: {len(to_process):,} 笔")

    for txhash in to_process:
        info = tx_map[txhash]
        value_hex = hex(int(info["value"] * 1e18)) if info["value"] else "0x0"
        parsed = parse_receipt(txhash, info["from"], info["to"], value_hex, info["ts"])
        if parsed:
            results[txhash] = parsed
        else:
            errors += 1
        done += 1

        if done % 100 == 0:
            print(f"  [{len(results)}/{total}]  解析{done}笔  失败{errors}  "
                  f"token缓存{len(TOKEN_CACHE)}", flush=True)
        if done % 500 == 0:
            _save(results, total)
            with open(token_cache_path, "w", encoding="utf-8") as f:
                json.dump({k: list(v) for k, v in TOKEN_CACHE.items()}, f, ensure_ascii=False)

    _save(results, total, done=True)
    with open(token_cache_path, "w", encoding="utf-8") as f:
        json.dump({k: list(v) for k, v in TOKEN_CACHE.items()}, f, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  总交易: {len(results):,} 笔")
    print(f"  失败: {errors}")
    print(f"  Token种类: {len(TOKEN_CACHE)}")
    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"  保存: {OUTPUT.name} ({size_mb:.1f} MB)")

    # 统计事件类型
    type_counts = {}
    for r in results.values():
        for e in r.get("events", []):
            t = e["type"]
            type_counts[t] = type_counts.get(t, 0) + 1
    print(f"\n事件统计:")
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t:<20s} {cnt:>8,}")


def _save(results, total, done=False):
    tx_list = sorted(results.values(), key=lambda x: -(x.get("ts") or 0))
    output = {
        "meta": {
            "total_parsed": len(results),
            "total_expected": total,
            "complete": done,
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "transactions": tx_list,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
