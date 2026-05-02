#!/usr/bin/env python3
"""
解析 portal_eth_raw/ 中的 batch 文件，提取每笔 Portal 桥交易的 token 转账详情。

解析内容:
  - ERC20 Transfer: 哪个token、多少量、from/to
  - 交易方向推断: transferTokens = ETH→跨链, completeTransfer = 跨链→ETH
  - 原生ETH转账

用法:
    python wormhole_data/parse_portal_eth_raw.py
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent / "use"
RAW_DIR = DIR / "portal_full" / "portal_eth_raw"
PORTAL_TXS = DIR / "portal_full" / "portal_eth_txs_all.json"
OUTPUT = DIR / "portal_full" / "portal_eth_parsed.json"

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
LOG_MESSAGE_TOPIC = "0x6eb224fb001ed210e379b335e35efe88672a8ce935d981a6896b27ffdf52a3b2"
PORTAL_CONTRACT = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"

WORMHOLE_CHAINS = {
    1: "Solana", 2: "Ethereum", 3: "Terra", 4: "BSC", 5: "Polygon",
    6: "Avalanche", 7: "Oasis", 8: "Algorand", 9: "Aurora", 10: "Fantom",
    11: "Karura", 12: "Acala", 13: "Klaytn", 14: "Celo", 15: "NEAR",
    16: "Moonbeam", 22: "Aptos", 23: "Arbitrum", 24: "Optimism",
    28: "XPLA", 30: "Base", 32: "Sei", 34: "Scroll", 35: "Mantle",
    36: "Blast", 40: "Sui",
}

# 常见token
TOKEN_CACHE = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    "0x814e0908b12a99fecf5bc101bb5d0b8b5cdf7d26": ("MDT", 18),
    "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce": ("SHIB", 18),
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": ("UNI", 18),
    "0x514910771af9ca656af840dff83e8264ecf986ca": ("LINK", 18),
    "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": ("AAVE", 18),
    "0x85f17cf997934a597031b2e18a9ab6ebd4b9f6a4": ("NEAR", 24),
    "0xb64ef51c888972c908cfacf59b47c1afbc0ab8ac": ("STORJ", 8),
}


def get_token_info(addr):
    addr = addr.lower()
    if addr in TOKEN_CACHE:
        return TOKEN_CACHE[addr]
    TOKEN_CACHE[addr] = (addr, 18)  # 未知token用完整地址代替
    return TOKEN_CACHE[addr]


def parse_wormhole_payload(receipt):
    """从 LogMessagePublished 事件解析 Wormhole payload，提取目标链/来源链信息"""
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics or topics[0] != LOG_MESSAGE_TOPIC:
            continue
        data = log["data"][2:]
        if len(data) < 522:  # 至少 261 bytes (320 header + 202 payload min)
            continue
        payload_len = int(data[256:320], 16)
        payload = data[320:320 + payload_len * 2]
        if len(payload) < 202:
            continue
        payload_id = int(payload[0:2], 16)
        token_addr = "0x" + payload[66:130][-40:]
        token_chain = int(payload[130:134], 16)
        recipient = "0x" + payload[134:198][-40:]
        recipient_chain = int(payload[198:202], 16)
        return {
            "payload_id": payload_id,
            "token_origin_chain": token_chain,
            "token_origin_chain_name": WORMHOLE_CHAINS.get(token_chain, f"chain_{token_chain}"),
            "token_origin_addr": token_addr,
            "dst_chain": recipient_chain,
            "dst_chain_name": WORMHOLE_CHAINS.get(recipient_chain, f"chain_{recipient_chain}"),
            "dst_recipient": recipient,
        }
    return None


def parse_one(entry, tx_info):
    """解析一笔portal交易的receipt"""
    receipt = entry.get("receipt")
    if not receipt:
        return None

    txhash = entry["hash"]
    sender = (receipt.get("from") or "").lower()
    to = (receipt.get("to") or "").lower()
    status = "success" if receipt.get("status") == "0x1" else "failed"
    gas_used = int(receipt.get("gasUsed", "0x0"), 16)
    block_num = int(receipt.get("blockNumber", "0x0"), 16)

    # 从portal_eth_txs_all获取时间和method
    method = ""
    ts = 0
    if tx_info:
        method = tx_info.get("method", "")
        ts = tx_info.get("ts", 0)

    # 推断方向
    if "completeTransfer" in method:
        direction = "inbound"  # 从其他链→ETH (接收)
    elif "transferTokens" in method:
        direction = "outbound"  # 从ETH→其他链 (发送)
    elif "wrapAndTransferETH" in method:
        direction = "outbound"
    else:
        direction = "unknown"

    # 解析Wormhole payload（outbound交易有LogMessagePublished）
    wh_info = parse_wormhole_payload(receipt)
    if wh_info and direction == "outbound":
        src_chain = "Ethereum"
        dst_chain = wh_info["dst_chain_name"]
    elif direction == "inbound" and wh_info:
        src_chain = wh_info["token_origin_chain_name"]
        dst_chain = "Ethereum"
    elif direction == "inbound":
        src_chain = "unknown"
        dst_chain = "Ethereum"
    else:
        src_chain = "Ethereum"
        dst_chain = "unknown"

    # 解析Transfer事件
    transfers = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics or topics[0] != TRANSFER_TOPIC:
            continue
        if len(topics) < 3:
            continue

        frm = "0x" + topics[1][-40:]
        to_addr = "0x" + topics[2][-40:]
        raw = int(log.get("data", "0x0"), 16)
        contract = log.get("address", "").lower()
        sym, dec = get_token_info(contract)
        amount = raw / (10 ** dec) if dec else raw

        # 判断和Portal合约的关系
        role = ""
        if frm.lower() == PORTAL_CONTRACT:
            role = "portal_out"  # Portal释放token给用户 (inbound完成)
        elif to_addr.lower() == PORTAL_CONTRACT:
            role = "portal_in"  # 用户锁定token到Portal (outbound发起)

        transfers.append({
            "token": sym,
            "contract": contract,
            "from": frm,
            "to": to_addr,
            "amount": amount,
            "role": role,
        })

    # 提取关键信息：用户发了什么/收了什么
    user_sent = [t for t in transfers if t["from"].lower() == sender and t["role"] == "portal_in"]
    user_received = [t for t in transfers if t["to"].lower() == sender and t["role"] == "portal_out"]
    portal_released = [t for t in transfers if t["role"] == "portal_out"]

    result = {
        "hash": txhash,
        "ts": ts,
        "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
        "block": block_num,
        "from": sender,
        "status": status,
        "gas_used": gas_used,
        "method": method,
        "direction": direction,
        "src_chain": src_chain,
        "dst_chain": dst_chain,
        "transfers": transfers,
        "summary": {
            "user_sent": [{"token": t["token"], "amount": t["amount"]} for t in user_sent],
            "user_received": [{"token": t["token"], "amount": t["amount"]} for t in user_received],
            "portal_released": [{"token": t["token"], "amount": t["amount"], "to": t["to"]} for t in portal_released],
        },
    }
    if wh_info:
        result["wormhole"] = wh_info
    return result


def main():
    # --limit N 只解析前N笔
    limit = None
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

    # 加载tx基础信息（时间、method）
    tx_lookup = {}
    if PORTAL_TXS.exists():
        with open(PORTAL_TXS, encoding="utf-8") as f:
            portal_data = json.load(f)
        for tx in portal_data["transactions"]:
            tx_lookup[tx["hash"]] = tx

    # 读取所有batch文件
    batch_files = sorted(RAW_DIR.glob("batch_*.json"))
    print(f"Batch文件: {len(batch_files)}")
    if limit:
        print(f"限制: 只解析前 {limit} 笔")

    results = []
    errors = 0
    count = 0
    for bf in batch_files:
        with open(bf, encoding="utf-8") as f:
            batch = json.load(f)
        for entry in batch:
            if limit and count >= limit:
                break
            if entry.get("error"):
                errors += 1
                continue
            tx_info = tx_lookup.get(entry["hash"])
            parsed = parse_one(entry, tx_info)
            if parsed:
                results.append(parsed)
                count += 1
        if limit and count >= limit:
            break

    # 排序
    results.sort(key=lambda x: -(x.get("ts") or 0))

    # 统计
    directions = {}
    tokens = {}
    methods = {}
    dst_chains = {}
    src_chains = {}
    routes = {}
    for r in results:
        d = r["direction"]
        directions[d] = directions.get(d, 0) + 1
        m = r["method"][:30] if r["method"] else "unknown"
        methods[m] = methods.get(m, 0) + 1
        sc = r.get("src_chain", "unknown")
        dc = r.get("dst_chain", "unknown")
        src_chains[sc] = src_chains.get(sc, 0) + 1
        dst_chains[dc] = dst_chains.get(dc, 0) + 1
        route = f"{sc}→{dc}"
        routes[route] = routes.get(route, 0) + 1
        for t in r["transfers"]:
            sym = t["token"]
            tokens[sym] = tokens.get(sym, 0) + 1

    output = {
        "meta": {
            "total_parsed": len(results),
            "errors": errors,
            "directions": directions,
            "routes": dict(sorted(routes.items(), key=lambda x: -x[1])),
            "dst_chains": dict(sorted(dst_chains.items(), key=lambda x: -x[1])),
            "src_chains": dict(sorted(src_chains.items(), key=lambda x: -x[1])),
            "top_tokens": dict(sorted(tokens.items(), key=lambda x: -x[1])[:30]),
            "methods": methods,
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "transactions": results,
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"  解析: {len(results):,} 笔  失败: {errors}")
    print(f"  保存: {OUTPUT.name} ({size_mb:.1f} MB)")

    print(f"\n方向:")
    for d, cnt in sorted(directions.items(), key=lambda x: -x[1]):
        print(f"  {d:<12s} {cnt:>8,}")

    print(f"\n路由 (src→dst):")
    for r, cnt in sorted(routes.items(), key=lambda x: -x[1]):
        print(f"  {r:<30s} {cnt:>8,}")

    print(f"\nMethod:")
    for m, cnt in sorted(methods.items(), key=lambda x: -x[1]):
        print(f"  {m:<35s} {cnt:>8,}")

    print(f"\nTop 20 Token:")
    for sym, cnt in sorted(tokens.items(), key=lambda x: -x[1])[:20]:
        print(f"  {sym:<15s} {cnt:>8,}")

    # 打印几个样例
    print(f"\n样例 (前5笔):")
    for r in results[:5]:
        print(f"  {r['hash'][:20]}...  {r['time']}  {r['src_chain']}→{r['dst_chain']}  {r['method'][:30]}")
        for t in r["transfers"]:
            tag = f" [{t['role']}]" if t["role"] else ""
            print(f"    {t['token']:<15s} {t['amount']:>18,.4f}  {t['from'][:12]}→{t['to'][:12]}{tag}")


if __name__ == "__main__":
    main()
