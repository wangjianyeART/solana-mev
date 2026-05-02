#!/usr/bin/env python3
"""
SOL / ETH 交易解析工具。

功能:
  1. 通过地址获取交易签名列表（SOL: getSignaturesForAddress, ETH: Etherscan）
  2. 解析单笔交易（SOL: getTransaction, ETH: eth_getTransactionReceipt）
  3. 从指定地址视角提取 sold/bought summary

可作为模块 import，也可命令行直接用:
    python wormhole_data/parse_tx.py sol <signature>
    python wormhole_data/parse_tx.py eth <tx_hash> [target_addr]
    python wormhole_data/parse_tx.py sol-addr <address> [--limit 20]
    python wormhole_data/parse_tx.py eth-addr <address> [--around <timestamp>] [--limit 20]
"""

import requests
import time
import json
import sys
from datetime import datetime, timezone

# ── RPC 节点 ──

SOL_RPC = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"
ETH_RPC = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"
ETHERSCAN_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"

# ── 常量 ──

KNOWN_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter v4",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora Pools",
    "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb": "Wormhole Portal",
    "NTtAaoDJhkeHeaVUHnyhwbPNAN6WgBpHkHBTc6d7vLu": "Wormhole NTT",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL Token",
}

MINT_SYMBOLS = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "jitoSOL",
}

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

ETH_TOKEN_CACHE = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
}


# ══════════════════════════════════════════════
#  RPC 通用
# ══════════════════════════════════════════════

def sol_rpc(method, params):
    """调用 Solana JSON-RPC，自动重试。"""
    for attempt in range(5):
        try:
            resp = requests.post(SOL_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                time.sleep(2 ** attempt)
                continue
            return data.get("result")
        except Exception:
            time.sleep(1)
    return None


def eth_rpc(method, params):
    """调用 Ethereum JSON-RPC，自动重试。"""
    for attempt in range(5):
        try:
            resp = requests.post(ETH_RPC, json={
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


# ══════════════════════════════════════════════
#  Solana 解析
# ══════════════════════════════════════════════

def get_sol_signatures(addr, limit=50, before_sig=None):
    """获取 SOL 地址的最近交易签名列表。

    返回 [{"sig", "ts", "slot", "err"}, ...] 按时间降序（最新在前）。
    """
    all_sigs = []
    params = {"limit": limit}
    if before_sig:
        params["before"] = before_sig

    result = sol_rpc("getSignaturesForAddress", [addr, params])
    if result:
        for s in result:
            bt = s.get("blockTime")
            if bt:
                all_sigs.append({
                    "sig": s["signature"],
                    "ts": bt,
                    "slot": s.get("slot"),
                    "err": s.get("err"),
                })
    return all_sigs


def get_sol_signatures_around(addr, target_ts, n=10):
    """获取地址在 target_ts 前后各 n 笔签名。

    返回 (before_list, after_list)，before 按时间降序，after 按时间升序。
    """
    all_sigs = []
    batch = get_sol_signatures(addr, limit=50)
    all_sigs.extend(batch)

    # 如果最旧的签名还没到 target_ts - 1天，继续拉
    while batch and all_sigs[-1]["ts"] > target_ts - 86400:
        batch = get_sol_signatures(addr, limit=50, before_sig=all_sigs[-1]["sig"])
        if not batch:
            break
        all_sigs.extend(batch)

    all_sigs.sort(key=lambda x: x["ts"])
    before = [s for s in all_sigs if s["ts"] <= target_ts]
    after = [s for s in all_sigs if s["ts"] > target_ts]
    return before[-n:], after[:n]


def parse_sol_tx(sig):
    """解析单笔 SOL 交易。

    返回:
        {
            "sig": str,
            "ts": int (blockTime),
            "slot": int,
            "time": str (ISO),
            "programs": [str],          # 调用的已知程序名
            "sol_changes": [{"account", "change"}],
            "token_changes": [{"mint", "symbol", "owner", "change"}],
            "signer": str,
            "summary": {"sold": [...], "bought": [...]},
        }
    """
    result = sol_rpc("getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
    }])
    if not result:
        return None

    meta = result.get("meta", {})
    tx_msg = result.get("transaction", {}).get("message", {})
    block_time = result.get("blockTime", 0)
    slot = result.get("slot", 0)

    # 账户列表
    account_keys = tx_msg.get("accountKeys", [])
    accounts = []
    for ak in account_keys:
        if isinstance(ak, dict):
            accounts.append(ak.get("pubkey", ""))
        else:
            accounts.append(ak)

    # SOL 变动
    pre_balances = meta.get("preBalances", [])
    post_balances = meta.get("postBalances", [])
    sol_changes = []
    for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
        diff = (post - pre) / 1e9
        if abs(diff) > 1e-9 and i < len(accounts):
            sol_changes.append({"account": accounts[i], "change": diff})

    # Token 变动 (preTokenBalances / postTokenBalances 差值)
    pre_tokens = {(t["accountIndex"], t.get("mint", "")): t
                  for t in meta.get("preTokenBalances", [])}
    post_tokens = {(t["accountIndex"], t.get("mint", "")): t
                   for t in meta.get("postTokenBalances", [])}
    all_keys = set(pre_tokens.keys()) | set(post_tokens.keys())

    token_changes = []
    for key in all_keys:
        pre_t = pre_tokens.get(key, {})
        post_t = post_tokens.get(key, {})
        mint = pre_t.get("mint") or post_t.get("mint", "")
        owner = pre_t.get("owner") or post_t.get("owner", "")
        pre_amt = float(pre_t.get("uiTokenAmount", {}).get("uiAmount") or 0)
        post_amt = float(post_t.get("uiTokenAmount", {}).get("uiAmount") or 0)
        change = post_amt - pre_amt
        if abs(change) > 1e-10:
            sym = MINT_SYMBOLS.get(mint, mint)
            token_changes.append({
                "mint": mint,
                "symbol": sym,
                "owner": owner,
                "change": change,
            })

    # 调用的程序
    programs = []
    for ix in tx_msg.get("instructions", []):
        pid = ix.get("programId", "")
        name = KNOWN_PROGRAMS.get(pid, "")
        if name and name not in programs:
            programs.append(name)
    for ix in meta.get("innerInstructions", []):
        for inner in ix.get("instructions", []):
            pid = inner.get("programId", "")
            name = KNOWN_PROGRAMS.get(pid, "")
            if name and name not in programs:
                programs.append(name)

    # Summary
    signer = sol_changes[0]["account"] if sol_changes else (accounts[0] if accounts else "")
    summary = _sol_summary(token_changes, signer)

    return {
        "sig": sig,
        "ts": block_time,
        "slot": slot,
        "time": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else None,
        "programs": programs,
        "sol_changes": sol_changes,
        "token_changes": token_changes,
        "signer": signer,
        "summary": summary,
    }


def _sol_summary(token_changes, signer):
    """从 signer 视角汇总 sold/bought。"""
    def extract(addr):
        sold, bought = {}, {}
        for tc in token_changes:
            if tc.get("owner") != addr:
                continue
            sym = tc.get("symbol", "?")
            change = tc.get("change", 0)
            if change < -1e-8:
                sold[sym] = sold.get(sym, 0) + (-change)
            elif change > 1e-8:
                bought[sym] = bought.get(sym, 0) + change
        return sold, bought

    fmt = lambda s, b: {
        "sold": [{"token": t, "amount": v} for t, v in s.items()],
        "bought": [{"token": t, "amount": v} for t, v in b.items()],
    }

    sold, bought = extract(signer)
    if sold or bought:
        return fmt(sold, bought)

    # fallback: 找参与 token 最多的地址
    per_addr = {}
    for tc in token_changes:
        owner = tc.get("owner", "")
        if not owner:
            continue
        sym = tc.get("symbol", "?")
        change = tc.get("change", 0)
        if owner not in per_addr:
            per_addr[owner] = {}
        per_addr[owner][sym] = per_addr[owner].get(sym, 0) + change

    best_addr, best_score = None, 0
    for addr, tokens in per_addr.items():
        nonzero = sum(1 for v in tokens.values() if abs(v) > 1e-8)
        total_vol = sum(abs(v) for v in tokens.values())
        score = nonzero * 1e12 + total_vol
        if score > best_score:
            best_score, best_addr = score, addr

    if best_addr:
        sold, bought = extract(best_addr)
        if sold or bought:
            return fmt(sold, bought)

    return {"sold": [], "bought": []}


# ══════════════════════════════════════════════
#  Ethereum 解析
# ══════════════════════════════════════════════

def get_eth_token_info(contract_addr):
    """查询 ERC20 合约的 symbol 和 decimals。结果会缓存。"""
    addr = contract_addr.lower()
    if addr in ETH_TOKEN_CACHE:
        return ETH_TOKEN_CACHE[addr]
    try:
        dec_result = eth_rpc("eth_call", [{"to": addr, "data": "0x313ce567"}, "latest"])
        decimals = int(dec_result, 16) if dec_result and dec_result != "0x" else 18
        sym_result = eth_rpc("eth_call", [{"to": addr, "data": "0x95d89b41"}, "latest"])
        symbol = "?"
        if sym_result and len(sym_result) > 66:
            try:
                hex_str = sym_result[130:]
                symbol = bytes.fromhex(hex_str).decode("utf-8").rstrip("\x00").strip()
                if not symbol or not symbol.isprintable():
                    symbol = "?"
            except Exception:
                symbol = "?"
        ETH_TOKEN_CACHE[addr] = (symbol, decimals)
        return (symbol, decimals)
    except Exception:
        ETH_TOKEN_CACHE[addr] = ("?", 18)
        return ("?", 18)


def _estimate_block(ts):
    """粗略估算 ETH 区块号（基于已知参考点，12s/block）。"""
    ref_ts = 1776336191   # 2026-04-16 10:43 UTC
    ref_block = 24891861
    return max(0, ref_block + int((ts - ref_ts) / 12))


def get_eth_txs(addr, ts_from, ts_to):
    """从 Etherscan 获取地址在 [ts_from, ts_to] 时间范围内的交易列表。

    返回 [{"hash", "ts"}, ...] 按时间升序。
    """
    addr = addr.lower()
    all_txs = []
    start_block = _estimate_block(ts_from)
    end_block = _estimate_block(ts_to)

    for action in ["txlist", "tokentx"]:
        url = (f"https://api.etherscan.io/v2/api?chainid=1&module=account"
               f"&action={action}&address={addr}"
               f"&startblock={start_block}&endblock={end_block}"
               f"&sort=asc&apikey={ETHERSCAN_KEY}")
        try:
            resp = requests.get(url, timeout=30)
            data = resp.json()
            if data.get("status") == "1" and data.get("result"):
                for tx in data["result"]:
                    ts = int(tx.get("timeStamp", 0))
                    h = tx.get("hash", "")
                    if h and ts:
                        all_txs.append({"hash": h, "ts": ts})
        except Exception:
            pass
        time.sleep(0.3)

    seen = set()
    unique = []
    for tx in all_txs:
        if tx["hash"] not in seen:
            seen.add(tx["hash"])
            unique.append(tx)

    unique.sort(key=lambda x: x["ts"])
    return unique


def get_eth_txs_around(addr, target_ts, n=10):
    """获取地址在 target_ts 前后各 n 笔交易。

    返回 (before_list, after_list)。
    """
    tx_list = get_eth_txs(addr, target_ts - 86400, target_ts + 86400)
    if not tx_list:
        tx_list = get_eth_txs(addr, target_ts - 86400 * 3, target_ts + 86400 * 3)
    before = [t for t in tx_list if t["ts"] <= target_ts]
    after = [t for t in tx_list if t["ts"] > target_ts]
    return before[-n:], after[:n]


def parse_eth_tx(txhash, target_addr=None):
    """解析单笔 ETH 交易。

    Args:
        txhash: 交易 hash
        target_addr: 可选，从该地址视角生成 summary

    返回:
        {
            "hash": str,
            "ts": int,
            "block": int,
            "time": str (ISO),
            "status": "success" | "failed",
            "from": str,
            "to": str,
            "events": [{"type", "token", "contract", "from", "to", "amount"}],
            "summary": {"sold": [...], "bought": [...]},
        }
    """
    receipt = eth_rpc("eth_getTransactionReceipt", [txhash])
    if not receipt:
        return None

    tx_detail = eth_rpc("eth_getTransactionByHash", [txhash])
    tx_from = ((tx_detail.get("from") or "") if tx_detail else "").lower()
    tx_to = ((tx_detail.get("to") or "") if tx_detail else "").lower()
    tx_value = int(tx_detail.get("value", "0x0"), 16) if tx_detail else 0
    block_num = int(receipt.get("blockNumber", "0x0"), 16)

    # 区块时间
    block_hex = receipt.get("blockNumber", "0x0")
    block_data = eth_rpc("eth_getBlockByNumber", [block_hex, False])
    block_ts = int(block_data.get("timestamp", "0x0"), 16) if block_data else 0

    status = "success" if receipt.get("status") == "0x1" else "failed"

    events = []
    # ETH 原生转账
    eth_value = tx_value / 1e18
    if eth_value > 0:
        events.append({
            "type": "eth_transfer", "token": "ETH",
            "from": tx_from, "to": tx_to, "amount": eth_value,
        })

    # ERC20 Transfer 事件
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics or topics[0] != TRANSFER_TOPIC or len(topics) != 3:
            continue
        contract = log.get("address", "").lower()
        frm = "0x" + topics[1][-40:]
        to = "0x" + topics[2][-40:]
        raw = int(log.get("data", "0x0"), 16)
        sym, dec = get_eth_token_info(contract)
        amount = raw / (10 ** dec) if dec else raw
        events.append({
            "type": "erc20_transfer", "token": sym, "contract": contract,
            "from": frm, "to": to, "amount": amount,
        })

    # Summary
    perspective = (target_addr or tx_from).lower()
    summary = _eth_summary(events, perspective)

    return {
        "hash": txhash,
        "ts": block_ts,
        "block": block_num,
        "time": datetime.fromtimestamp(block_ts, tz=timezone.utc).isoformat() if block_ts else None,
        "status": status,
        "from": tx_from,
        "to": tx_to,
        "events": events,
        "summary": summary,
    }


def _eth_summary(events, target_addr):
    """从 target_addr 视角汇总 sold/bought。"""
    addr = target_addr.lower()
    outflow = {}
    inflow = {}
    for e in events:
        if e["type"] not in ("erc20_transfer", "eth_transfer"):
            continue
        token = e.get("token", "?")
        if e.get("from", "").lower() == addr:
            outflow[token] = outflow.get(token, 0) + e["amount"]
        if e.get("to", "").lower() == addr:
            inflow[token] = inflow.get(token, 0) + e["amount"]

    sold = []
    bought = []
    for t in set(outflow.keys()) | set(inflow.keys()):
        out_amt = outflow.get(t, 0)
        in_amt = inflow.get(t, 0)
        if out_amt > 1e-8:
            sold.append({"token": t, "amount": out_amt})
        if in_amt > 1e-8:
            bought.append({"token": t, "amount": in_amt})

    return {"sold": sold, "bought": bought}


# ══════════════════════════════════════════════
#  命令行
# ══════════════════════════════════════════════

def _pp(obj):
    """Pretty print JSON。"""
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "sol":
        sig = sys.argv[2]
        print(f"解析 SOL 交易: {sig}")
        result = parse_sol_tx(sig)
        if result:
            _pp(result)
        else:
            print("解析失败")

    elif cmd == "eth":
        txhash = sys.argv[2]
        target = sys.argv[3] if len(sys.argv) > 3 else None
        print(f"解析 ETH 交易: {txhash}")
        if target:
            print(f"视角地址: {target}")
        result = parse_eth_tx(txhash, target)
        if result:
            _pp(result)
        else:
            print("解析失败")

    elif cmd == "sol-addr":
        addr = sys.argv[2]
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        print(f"获取 SOL 地址签名: {addr} (limit={limit})")
        sigs = get_sol_signatures(addr, limit=limit)
        for s in sigs:
            t = datetime.fromtimestamp(s["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            err = " [ERR]" if s["err"] else ""
            print(f"  {t}  slot={s['slot']}  {s['sig'][:30]}...{err}")
        print(f"共 {len(sigs)} 条")

    elif cmd == "eth-addr":
        addr = sys.argv[2]
        around = None
        limit = 20
        for i, arg in enumerate(sys.argv):
            if arg == "--around" and i + 1 < len(sys.argv):
                around = int(sys.argv[i + 1])
            if arg == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        if around:
            print(f"获取 ETH 地址交易: {addr} (around={datetime.utcfromtimestamp(around).isoformat()}, ±1天)")
            txs = get_eth_txs(addr, around - 86400, around + 86400)
        else:
            print(f"获取 ETH 地址交易: {addr} (最近7天)")
            now = int(time.time())
            txs = get_eth_txs(addr, now - 86400 * 7, now)
        for t in txs[:limit]:
            ts = datetime.fromtimestamp(t["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  {ts}  {t['hash']}")
        print(f"共 {len(txs)} 条 (显示 {min(len(txs), limit)})")

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
