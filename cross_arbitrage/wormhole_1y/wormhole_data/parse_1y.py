#!/usr/bin/env python3
"""
解析 recent_1y/ 中的 SOL 和 ETH raw 数据，输出两个 JSON:
  - recent_1y/sol_parsed.json
  - recent_1y/eth_parsed.json

复用 parse_portal_sol_raw.py 和 parse_portal_eth_raw.py 的解析逻辑。

用法:
    python wormhole_data/parse_1y.py
"""

import json
import base58
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent / "use" / "portal_full"
RECENT = DIR / "recent_1y"
SOL_RAW = RECENT / "sol_raw"
ETH_RAW = RECENT / "eth_raw"
ETH_TXS_ALL = DIR / "portal_eth_txs_all.json"

SOL_OUT = RECENT / "sol_parsed.json"
ETH_OUT = RECENT / "eth_parsed.json"

# ─── 常量 ─────────────────────────────────────────────────────────────────────

PORTAL_PROGRAM = "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb"
PORTAL_CONTRACT = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
LOG_MESSAGE_TOPIC = "0x6eb224fb001ed210e379b335e35efe88672a8ce935d981a6896b27ffdf52a3b2"
# Portal completeTransfer 事件: topic[1] = emitterChainId (source chain)
COMPLETE_TRANSFER_TOPIC = "0xcaf280c8cfeba144da67230d9b009c8f868a75bac9a528fa0474be1ba317c169"

MINT_SYMBOLS = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH(Wormhole)",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC(Wormhole)",
    "A9mUU4qviSctJVPJdBGS2hwp7P816M25QVRnRMCFNOJf": "USDT(Wormhole)",
    "FjYfhJx8BcAzeiY3APg8E2QZiLW4F4uqz9JoSBXr1BoP": "USDC(Wormhole)",
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "stSOL",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "jitoSOL",
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": "bSOL",
}

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

WORMHOLE_CHAINS = {
    1: "Solana", 2: "Ethereum", 3: "Terra", 4: "BSC", 5: "Polygon",
    6: "Avalanche", 7: "Oasis", 8: "Algorand", 9: "Aurora", 10: "Fantom",
    11: "Karura", 12: "Acala", 13: "Klaytn", 14: "Celo", 15: "NEAR",
    16: "Moonbeam", 22: "Aptos", 23: "Arbitrum", 24: "Optimism",
    28: "XPLA", 30: "Base", 32: "Sei", 34: "Scroll", 35: "Mantle",
    36: "Blast", 40: "Sui",
}

# 加载 ERC20 decimals 缓存到 TOKEN_CACHE
ERC20_DECIMALS_FILE = DIR / "erc20_decimals.json"
if ERC20_DECIMALS_FILE.exists():
    _dec_data = json.load(open(ERC20_DECIMALS_FILE, encoding="utf-8"))
    for _addr, _dec in _dec_data.items():
        _addr_low = _addr.lower()
        if _addr_low not in TOKEN_CACHE:
            TOKEN_CACHE[_addr_low] = (_addr_low, _dec)
        else:
            # 更新已有条目的 decimals（保留 symbol）
            _sym = TOKEN_CACHE[_addr_low][0]
            TOKEN_CACHE[_addr_low] = (_sym, _dec)
    print(f"已加载 ERC20 decimals: {len(_dec_data)} 个 (TOKEN_CACHE 总计 {len(TOKEN_CACHE)} 个)")


# ─── SOL 解析 ─────────────────────────────────────────────────────────────────

def get_mint_symbol(mint):
    return MINT_SYMBOLS.get(mint, mint)


def parse_sol_tx(entry):
    data = entry.get("data")
    if not data:
        return None

    sig = entry["sig"]
    meta = data.get("meta", {})
    tx_msg = data.get("transaction", {}).get("message", {})
    block_time = data.get("blockTime", 0)
    slot = data.get("slot", 0)

    if meta.get("err"):
        return None

    fee = meta.get("fee", 0)
    logs = meta.get("logMessages", [])

    signers = [k["pubkey"] for k in tx_msg.get("accountKeys", []) if k.get("signer")]
    sender = signers[0] if signers else ""

    has_mint = any("Instruction: MintTo" in l for l in logs)
    has_burn = any("Instruction: Burn" in l for l in logs)
    has_approve = any("Instruction: Approve" in l for l in logs)
    has_transfer = any("Instruction: Transfer" in l for l in logs)

    if has_mint and not has_burn:
        direction = "inbound"
    elif has_burn and not has_mint:
        direction = "outbound"
    elif has_transfer and has_approve:
        direction = "outbound"
    elif has_transfer and not has_mint and not has_burn:
        direction = "outbound"
    elif has_mint and has_burn:
        direction = "swap"
    else:
        direction = "unknown"

    # 解析目标链：从 Portal 指令 data 最后 2 字节提取 recipient_chain
    dst_chain = None
    dst_recipient = None
    if direction == "outbound":
        for inst in tx_msg.get("instructions", []):
            if inst.get("programId") != PORTAL_PROGRAM:
                continue
            try:
                raw = base58.b58decode(inst["data"])
                if len(raw) >= 55:
                    chain_id = int.from_bytes(raw[-2:], "little")
                    dst_chain = WORMHOLE_CHAINS.get(chain_id, f"chain_{chain_id}")
                    # recipient 地址在 offset 21..53 (32 bytes, ETH地址取后20字节)
                    recip_bytes = raw[21:53]
                    if chain_id == 1:  # Solana: 32 bytes
                        dst_recipient = base58.b58encode(recip_bytes).decode()
                    else:  # EVM: 取后20字节
                        dst_recipient = "0x" + recip_bytes[-20:].hex()
            except Exception:
                pass
    elif direction == "inbound":
        dst_chain = "Solana"  # inbound = 跨链→Solana

    pre_balances = {}
    for b in meta.get("preTokenBalances", []):
        key = (b["accountIndex"], b["mint"])
        amt_str = b.get("uiTokenAmount", {}).get("uiAmountString", "0")
        pre_balances[key] = {
            "amount": float(amt_str) if amt_str else 0,
            "mint": b["mint"],
            "owner": b.get("owner", ""),
        }

    post_balances = {}
    for b in meta.get("postTokenBalances", []):
        key = (b["accountIndex"], b["mint"])
        amt_str = b.get("uiTokenAmount", {}).get("uiAmountString", "0")
        post_balances[key] = {
            "amount": float(amt_str) if amt_str else 0,
            "mint": b["mint"],
            "owner": b.get("owner", ""),
        }

    token_changes = []
    all_keys = set(pre_balances.keys()) | set(post_balances.keys())
    for k in all_keys:
        default_info = post_balances.get(k) or pre_balances.get(k)
        pre = pre_balances.get(k, {"amount": 0, "mint": default_info["mint"], "owner": default_info["owner"]})
        post = post_balances.get(k, {"amount": 0, "mint": default_info["mint"], "owner": default_info["owner"]})
        diff = post["amount"] - pre["amount"]
        if diff != 0:
            mint = pre["mint"]
            token_changes.append({
                "mint": mint,
                "symbol": get_mint_symbol(mint),
                "owner": pre.get("owner") or post.get("owner", ""),
                "change": diff,
            })

    pre_sol = meta.get("preBalances", [])
    post_sol = meta.get("postBalances", [])
    sol_changes = []
    account_keys = [k["pubkey"] for k in tx_msg.get("accountKeys", [])]
    for i in range(min(len(pre_sol), len(post_sol))):
        diff = (post_sol[i] - pre_sol[i]) / 1e9
        if abs(diff) > 0.000001 and i < len(account_keys):
            sol_changes.append({
                "account": account_keys[i],
                "change_sol": round(diff, 9),
            })

    user_changes = [tc for tc in token_changes if tc["owner"] == sender]

    result = {
        "sig": sig,
        "ts": block_time,
        "time": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else None,
        "slot": slot,
        "sender": sender,
        "fee_sol": fee / 1e9,
        "direction": direction,
        "src_chain": "Solana",
        "dst_chain": dst_chain,
        "token_changes": token_changes,
        "sol_changes": sol_changes[:5],
        "summary": {
            "user_token_changes": [{"symbol": tc["symbol"], "change": tc["change"]} for tc in user_changes],
        },
    }
    if dst_recipient:
        result["dst_recipient"] = dst_recipient
    return result


# ─── ETH 解析 ─────────────────────────────────────────────────────────────────

def get_token_info(addr):
    addr = addr.lower()
    if addr in TOKEN_CACHE:
        return TOKEN_CACHE[addr]
    TOKEN_CACHE[addr] = (addr, 18)
    return TOKEN_CACHE[addr]


def parse_wormhole_payload(receipt):
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics or topics[0] != LOG_MESSAGE_TOPIC:
            continue
        data = log["data"][2:]
        if len(data) < 522:
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


def parse_eth_tx(entry, tx_info):
    receipt = entry.get("receipt")
    if not receipt:
        return None

    txhash = entry["hash"]
    sender = (receipt.get("from") or "").lower()
    status = "success" if receipt.get("status") == "0x1" else "failed"
    gas_used = int(receipt.get("gasUsed", "0x0"), 16)
    gas_price = int(receipt.get("effectiveGasPrice", "0x0"), 16)
    fee_eth = gas_used * gas_price / 1e18
    block_num = int(receipt.get("blockNumber", "0x0"), 16)

    method = ""
    ts = 0
    if tx_info:
        method = tx_info.get("method", "")
        ts = tx_info.get("ts", 0)

    # 如果 tx_lookup 没有时间，从 receipt log 中提取
    if not ts:
        for log in receipt.get("logs", []):
            bt = log.get("blockTimestamp")
            if bt:
                ts = int(bt, 16)
                break

    if "completeTransfer" in method:
        direction = "inbound"
    elif "transferTokens" in method:
        direction = "outbound"
    elif "wrapAndTransferETH" in method:
        direction = "outbound"
    else:
        direction = "unknown"

    wh_info = parse_wormhole_payload(receipt)

    # 从 completeTransfer 事件 topic[1] 提取 source chain
    emitter_chain = None
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if topics and topics[0] == COMPLETE_TRANSFER_TOPIC and len(topics) >= 2:
            chain_id = int(topics[1], 16)
            emitter_chain = WORMHOLE_CHAINS.get(chain_id, f"chain_{chain_id}")
            break

    if wh_info and direction == "outbound":
        src_chain = "Ethereum"
        dst_chain = wh_info["dst_chain_name"]
    elif direction == "inbound" and emitter_chain:
        src_chain = emitter_chain
        dst_chain = "Ethereum"
    elif direction == "inbound" and wh_info:
        src_chain = wh_info["token_origin_chain_name"]
        dst_chain = "Ethereum"
    elif direction == "inbound":
        src_chain = "unknown"
        dst_chain = "Ethereum"
    else:
        src_chain = "Ethereum"
        dst_chain = "unknown"

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

        role = ""
        if frm.lower() == PORTAL_CONTRACT:
            role = "portal_out"
        elif to_addr.lower() == PORTAL_CONTRACT:
            role = "portal_in"

        transfers.append({
            "token": sym,
            "contract": contract,
            "from": frm,
            "to": to_addr,
            "amount": amount,
            "role": role,
        })

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
        "fee_eth": fee_eth,
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
    if direction == "unknown":
        return None  # 非跨链交易，跳过
    return result


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    # === SOL ===
    print("=" * 60)
    print("解析 SOL raw...")
    sol_batches = sorted(SOL_RAW.glob("batch_*.json"))
    sol_results = []
    sol_errors = 0
    for bf in sol_batches:
        batch = json.load(open(bf, encoding="utf-8"))
        for entry in batch:
            if entry.get("error"):
                sol_errors += 1
                continue
            parsed = parse_sol_tx(entry)
            if parsed:
                sol_results.append(parsed)
    sol_results.sort(key=lambda x: -(x.get("ts") or 0))

    # 统计
    sol_dirs = {}
    sol_tokens = {}
    sol_routes = {}
    for r in sol_results:
        sol_dirs[r["direction"]] = sol_dirs.get(r["direction"], 0) + 1
        for tc in r["token_changes"]:
            sol_tokens[tc["symbol"]] = sol_tokens.get(tc["symbol"], 0) + 1
        route = f"Solana→{r.get('dst_chain', 'unknown')}"
        sol_routes[route] = sol_routes.get(route, 0) + 1

    sol_output = {
        "meta": {
            "total_parsed": len(sol_results),
            "errors": sol_errors,
            "directions": sol_dirs,
            "routes": dict(sorted(sol_routes.items(), key=lambda x: -x[1])),
            "top_tokens": dict(sorted(sol_tokens.items(), key=lambda x: -x[1])[:30]),
            "time_range": f"{sol_results[-1]['time']} ~ {sol_results[0]['time']}" if sol_results else "",
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "transactions": sol_results,
    }
    with open(SOL_OUT, "w", encoding="utf-8") as f:
        json.dump(sol_output, f, indent=2, ensure_ascii=False)

    print(f"  SOL: {len(sol_results)} 笔, 错误: {sol_errors}")
    print(f"  方向: {sol_dirs}")
    print(f"  路由: {sol_routes}")
    print(f"  保存: {SOL_OUT.name}")

    # === ETH ===
    print()
    print("=" * 60)
    print("解析 ETH raw...")

    # 加载 tx 基础信息
    tx_lookup = {}
    if ETH_TXS_ALL.exists():
        portal_data = json.load(open(ETH_TXS_ALL, encoding="utf-8"))
        for tx in portal_data["transactions"]:
            tx_lookup[tx["hash"]] = tx
        print(f"  已加载 {len(tx_lookup)} 条 tx_info (用于获取 method/timestamp)")

    eth_batches = sorted(ETH_RAW.glob("batch_*.json"))
    eth_results = []
    eth_errors = 0
    seen_hashes = set()
    for bf in eth_batches:
        batch = json.load(open(bf, encoding="utf-8"))
        for entry in batch:
            if entry.get("error"):
                eth_errors += 1
                continue
            h = entry.get("hash")
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            tx_info = tx_lookup.get(h)
            parsed = parse_eth_tx(entry, tx_info)
            if parsed:
                eth_results.append(parsed)
    eth_results.sort(key=lambda x: -(x.get("ts") or 0))

    # 统计
    eth_dirs = {}
    eth_tokens = {}
    eth_routes = {}
    for r in eth_results:
        eth_dirs[r["direction"]] = eth_dirs.get(r["direction"], 0) + 1
        route = f"{r['src_chain']}→{r['dst_chain']}"
        eth_routes[route] = eth_routes.get(route, 0) + 1
        for t in r["transfers"]:
            eth_tokens[t["token"]] = eth_tokens.get(t["token"], 0) + 1

    eth_output = {
        "meta": {
            "total_parsed": len(eth_results),
            "errors": eth_errors,
            "directions": eth_dirs,
            "routes": dict(sorted(eth_routes.items(), key=lambda x: -x[1])),
            "top_tokens": dict(sorted(eth_tokens.items(), key=lambda x: -x[1])[:30]),
            "time_range": f"{eth_results[-1]['time']} ~ {eth_results[0]['time']}" if eth_results else "",
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "transactions": eth_results,
    }
    with open(ETH_OUT, "w", encoding="utf-8") as f:
        json.dump(eth_output, f, indent=2, ensure_ascii=False)

    print(f"  ETH: {len(eth_results)} 笔, 错误: {eth_errors}")
    print(f"  方向: {eth_dirs}")
    print(f"  路由: {eth_routes}")
    print(f"  保存: {ETH_OUT.name}")

    print()
    print("=" * 60)
    print(f"完成! 输出:")
    print(f"  {SOL_OUT}")
    print(f"  {ETH_OUT}")


if __name__ == "__main__":
    main()
