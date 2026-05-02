#!/usr/bin/env python3
"""
补全 unmatched 记录的上下文数据。

对于 sender_before/after 或 receiver_before/after 不完整的跨链记录：
1. 找到对应地址
2. 获取该地址的签名/交易列表（SOL: getSignaturesForAddress, ETH: Etherscan）
3. 定位跨链交易，取上下各10笔
4. 对窗口内交易做解析（SOL: getTransaction, ETH: eth_getTransactionReceipt）
5. 用解析后的数据补充 bridge_context_completed.json

用法:
    python wormhole_data/backfill_context.py
"""

import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent / "use"
CONTEXT_COMPLETED = DIR / "parsed" / "bridge_context_completed.json"

SOL_RPC = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"
ETH_RPC = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"
ETHERSCAN_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"

CONTEXT_SIZE = 10

# ── Solana helpers ──

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


def sol_rpc(method, params):
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


def get_sol_signatures(addr, bridge_ts, n=50):
    """获取地址在 bridge_ts 前后的签名列表。"""
    # 先获取 bridge_ts 之前的签名
    all_sigs = []
    result = sol_rpc("getSignaturesForAddress", [addr, {"limit": n}])
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

    # 如果不够早，用 before 参数继续拉
    if all_sigs and all_sigs[-1]["ts"] > bridge_ts - 86400:
        last_sig = all_sigs[-1]["sig"]
        result2 = sol_rpc("getSignaturesForAddress", [addr, {"limit": n, "before": last_sig}])
        if result2:
            for s in result2:
                bt = s.get("blockTime")
                if bt:
                    all_sigs.append({
                        "sig": s["signature"],
                        "ts": bt,
                        "slot": s.get("slot"),
                        "err": s.get("err"),
                    })

    # 按时间升序
    all_sigs.sort(key=lambda x: x["ts"])
    return all_sigs


def parse_sol_tx(sig):
    """解析单笔 SOL 交易。"""
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

    # Token 变动
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
        owner = (pre_t.get("owner") or post_t.get("owner", ""))
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

    # 程序
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
    signer = sol_changes[0]["account"] if sol_changes else ""
    summary = _sol_summary(token_changes, signer)

    return {
        "sig": sig,
        "ts": block_time,
        "slot": slot,
        "time": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else None,
        "programs": programs[:5],
        "sol_changes": sol_changes,
        "token_changes": token_changes,
        "signer": signer,
        "summary": summary,
    }


def _sol_summary(token_changes, signer):
    """SOL summary with signer fallback."""
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

    # fallback: max participant
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


# ── Ethereum helpers ──

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

ETH_TOKEN_CACHE = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "0xdac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "0x6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
}


def get_eth_token_info(addr):
    addr = addr.lower()
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
    """粗略估算 ETH 区块号（基于 2026-04-16 区块 24891861, 12s/block）。"""
    ref_ts = 1776336191   # 2026-04-16 10:43 UTC
    ref_block = 24891861
    return max(0, ref_block + int((ts - ref_ts) / 12))


def get_eth_txs(addr, bridge_ts):
    """从 Etherscan 获取地址在桥接时间附近的交易列表。

    使用 startblock/endblock 限制查询范围，避免活跃地址返回最古老的交易。
    """
    addr = addr.lower()
    all_txs = []

    # 查询桥接时间前后各 1 天的区块范围
    start_block = _estimate_block(bridge_ts - 86400)
    end_block = _estimate_block(bridge_ts + 86400)

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
        time.sleep(0.3)  # Etherscan rate limit

    # 如果结果为空，扩大范围到前后 3 天再试一次
    if not all_txs:
        start_block = _estimate_block(bridge_ts - 86400 * 3)
        end_block = _estimate_block(bridge_ts + 86400 * 3)
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

    # 去重按 hash
    seen = set()
    unique = []
    for tx in all_txs:
        if tx["hash"] not in seen:
            seen.add(tx["hash"])
            unique.append(tx)

    unique.sort(key=lambda x: x["ts"])
    return unique


def get_eth_txs_range(addr, ts_from, ts_to):
    """从 Etherscan 获取地址在指定时间范围内的交易列表。"""
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

    # 去重按 hash
    seen = set()
    unique = []
    for tx in all_txs:
        if tx["hash"] not in seen:
            seen.add(tx["hash"])
            unique.append(tx)

    unique.sort(key=lambda x: x["ts"])
    return unique


def parse_eth_tx(txhash, target_addr):
    """解析单笔 ETH 交易，从 target_addr 视角计算 summary。"""
    receipt = eth_rpc("eth_getTransactionReceipt", [txhash])
    if not receipt:
        return None

    # 获取交易详情（for value）
    tx_detail = eth_rpc("eth_getTransactionByHash", [txhash])
    tx_from = ((tx_detail.get("from") or "") if tx_detail else "").lower()
    tx_to = ((tx_detail.get("to") or "") if tx_detail else "").lower()
    tx_value = int(tx_detail.get("value", "0x0"), 16) if tx_detail else 0
    block_num = int(receipt.get("blockNumber", "0x0"), 16)

    status = "success" if receipt.get("status") == "0x1" else "failed"

    events = []
    # ETH transfer
    eth_value = tx_value / 1e18
    if eth_value > 0:
        events.append({
            "type": "eth_transfer", "token": "ETH",
            "from": tx_from, "to": tx_to, "amount": eth_value,
        })

    # ERC20 transfers
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

    # Summary from target_addr perspective (same logic as find_bridge_context)
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

    all_tokens = set(outflow.keys()) | set(inflow.keys())
    sold = []
    bought = []
    for t in all_tokens:
        out_amt = outflow.get(t, 0)
        in_amt = inflow.get(t, 0)
        if out_amt > 1e-8 and in_amt > 1e-8:
            sold.append({"token": t, "amount": out_amt})
        elif out_amt > 1e-8:
            sold.append({"token": t, "amount": out_amt})
        elif in_amt > 1e-8:
            bought.append({"token": t, "amount": in_amt})

    summary = {"sold": sold, "bought": bought}

    return {
        "hash": txhash,
        "ts": 0,  # will be filled from tx list
        "block": block_num,
        "status": status,
        "summary": summary,
        "events": events,
    }


def _should_replace(new_entries, old_entries, bridge_ts, direction):
    """判断是否应该用新数据替换旧数据。

    不仅看数量，还看时间proximity。如果旧数据离桥接时间很远但新数据更近，也要替换。
    direction: 'before' 表示 xxx_before (取 max ts), 'after' 表示 xxx_after (取 min ts)
    """
    if not new_entries:
        return False
    if len(new_entries) > len(old_entries):
        return True

    # 如果新旧数量相同，看谁离桥接时间更近
    def _closest_ts(entries, is_before):
        times = [e.get("ts", 0) for e in entries if e.get("ts")]
        if not times:
            return None
        return max(times) if is_before else min(times)

    is_before = direction == "before"
    old_closest = _closest_ts(old_entries, is_before)
    new_closest = _closest_ts(new_entries, is_before)

    if old_closest is None or new_closest is None or bridge_ts == 0:
        return len(new_entries) >= len(old_entries)

    if is_before:
        old_gap = abs(bridge_ts - old_closest)
        new_gap = abs(bridge_ts - new_closest)
    else:
        old_gap = abs(new_closest - bridge_ts) if new_closest else float("inf")
        new_gap_val = abs(new_closest - bridge_ts) if new_closest else float("inf")
        old_gap = abs(old_closest - bridge_ts) if old_closest else float("inf")
        new_gap = new_gap_val

    # 新数据离桥接时间更近就替换
    return new_gap < old_gap


# ── Main logic ──

def find_window(tx_list, bridge_id, bridge_ts, n=CONTEXT_SIZE):
    """在 tx_list 中找 bridge_ts 附近的上下 n 笔。
    返回 (before_inclusive, after_exclusive)
    before_inclusive: ts <= bridge_ts 的最近 n 笔
    after_exclusive: ts > bridge_ts 的最近 n 笔
    """
    before = [(tx["ts"], tx) for tx in tx_list if tx["ts"] <= bridge_ts]
    after = [(tx["ts"], tx) for tx in tx_list if tx["ts"] > bridge_ts]
    before = before[-n:]  # 最近 n 笔
    after = after[:n]
    return before, after


def is_eth_address(addr):
    return addr.startswith("0x") and len(addr) == 42


def is_sol_address(addr):
    return not addr.startswith("0x") and len(addr) >= 32


def main():
    print("加载数据...", flush=True)
    with open(CONTEXT_COMPLETED, encoding="utf-8") as f:
        data = json.load(f)

    records = data["records"]
    rec_by_id = {r["id"]: r for r in records}
    print(f"  跨链记录: {len(records)}")

    # 找需要补全的记录
    MAX_GAP = 86400  # 24小时 — context 数据离桥接时间超过这个值就认为是过期的
    to_backfill = []
    for r in records:
        sb = r.get("sender_before", [])
        sa = r.get("sender_after", [])
        rb = r.get("receiver_before", [])
        ra = r.get("receiver_after", [])

        need = False
        # 数量不足
        if len(sb) < CONTEXT_SIZE or len(ra) < CONTEXT_SIZE:
            need = True

        # 时间过期检查：即使数量够了，如果最近的 context 交易离桥接时间 > 24h 也要重查
        if not need:
            src_ts_str = r.get("src_timestamp", "")
            dst_ts_str = r.get("dst_timestamp", "")
            src_ts = int(datetime.fromisoformat(src_ts_str).timestamp()) if src_ts_str else 0
            dst_ts = int(datetime.fromisoformat(dst_ts_str).timestamp()) if dst_ts_str else 0

            # sender_before: 最近一笔应该接近 src_ts
            if src_ts and sb:
                sb_times = [tx.get("ts", 0) for tx in sb if tx.get("ts")]
                if sb_times and src_ts - max(sb_times) > MAX_GAP:
                    need = True

            # sender_after: 最早一笔应该接近 src_ts
            if not need and src_ts and sa:
                sa_times = [tx.get("ts", 0) for tx in sa if tx.get("ts")]
                if sa_times and min(sa_times) - src_ts > MAX_GAP:
                    need = True

            # receiver_before: 最近一笔应该接近 dst_ts
            if not need and dst_ts and rb:
                rb_times = [tx.get("ts", 0) for tx in rb if tx.get("ts")]
                if rb_times and dst_ts - max(rb_times) > MAX_GAP:
                    need = True

            # receiver_after: 最早一笔应该接近 dst_ts
            if not need and dst_ts and ra:
                ra_times = [tx.get("ts", 0) for tx in ra if tx.get("ts")]
                if ra_times and min(ra_times) - dst_ts > MAX_GAP:
                    need = True

        if need:
            to_backfill.append(r)

    print(f"  需要补全: {len(to_backfill)} 条")

    # 按地址分组，避免重复查询
    sol_addr_records = {}  # addr -> [(rec, role, bridge_ts)]
    eth_addr_records = {}

    for r in to_backfill:
        src = r["src_sender"]
        dst = r["dst_receiver"]
        src_ts = int(datetime.fromisoformat(r["src_timestamp"]).timestamp()) if r.get("src_timestamp") else 0
        dst_ts = int(datetime.fromisoformat(r["dst_timestamp"]).timestamp()) if r.get("dst_timestamp") else 0

        if is_sol_address(src) and src_ts:
            sol_addr_records.setdefault(src, []).append((r, "sender", src_ts))
        elif is_eth_address(src) and src_ts:
            eth_addr_records.setdefault(src.lower(), []).append((r, "sender", src_ts))

        if is_sol_address(dst) and dst_ts:
            sol_addr_records.setdefault(dst, []).append((r, "receiver", dst_ts))
        elif is_eth_address(dst) and dst_ts:
            eth_addr_records.setdefault(dst.lower(), []).append((r, "receiver", dst_ts))

    print(f"  SOL 地址: {len(sol_addr_records)}")
    print(f"  ETH 地址: {len(eth_addr_records)}")

    # ── 处理 SOL 地址 ──
    sol_done = 0
    sol_parsed_cache = {}  # sig -> parsed

    for addr, items in sol_addr_records.items():
        # 获取所有 bridge_ts 的范围
        min_ts = min(ts for _, _, ts in items) - 3600
        max_ts = max(ts for _, _, ts in items) + 3600

        # 获取签名
        sigs = get_sol_signatures(addr, min_ts)
        if not sigs:
            continue

        for rec, role, bridge_ts in items:
            # 找窗口
            sig_list = [{"ts": s["ts"], "sig": s["sig"], "slot": s["slot"]} for s in sigs]

            if role == "sender":
                # sender_before: ts <= bridge_ts, sender_after: ts > bridge_ts
                before_sigs = [s for s in sig_list if s["ts"] <= bridge_ts][-CONTEXT_SIZE:]
                after_sigs = [s for s in sig_list if s["ts"] > bridge_ts][:CONTEXT_SIZE]

                # 只补全 sender_before 不足的情况
                existing_sigs = set(tx.get("sig", "") for tx in rec.get("sender_before", []))
                need_parse = [s for s in before_sigs if s["sig"] not in existing_sigs]

                # 解析
                new_entries = []
                for s in before_sigs:
                    if s["sig"] in existing_sigs:
                        # 保留已有的
                        for existing in rec.get("sender_before", []):
                            if existing.get("sig") == s["sig"]:
                                new_entries.append(existing)
                                break
                    else:
                        # 解析新的
                        if s["sig"] not in sol_parsed_cache:
                            parsed = parse_sol_tx(s["sig"])
                            sol_parsed_cache[s["sig"]] = parsed
                            time.sleep(0.1)
                        parsed = sol_parsed_cache[s["sig"]]
                        if parsed:
                            tc = parsed.get("token_changes", [])
                            new_entries.append({
                                "chain": "SOL", "sig": s["sig"], "ts": s["ts"],
                                "time": parsed.get("time"),
                                "programs": parsed.get("programs", [])[:5],
                                "token_changes": tc,
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_entries.append({"chain": "SOL", "sig": s["sig"], "ts": s["ts"]})

                if _should_replace(new_entries, rec.get("sender_before", []), bridge_ts, "before"):
                    rec["sender_before"] = new_entries

                # sender_after 同理
                existing_sigs_a = set(tx.get("sig", "") for tx in rec.get("sender_after", []))
                new_after = []
                for s in after_sigs:
                    if s["sig"] in existing_sigs_a:
                        for existing in rec.get("sender_after", []):
                            if existing.get("sig") == s["sig"]:
                                new_after.append(existing)
                                break
                    else:
                        if s["sig"] not in sol_parsed_cache:
                            parsed = parse_sol_tx(s["sig"])
                            sol_parsed_cache[s["sig"]] = parsed
                            time.sleep(0.1)
                        parsed = sol_parsed_cache[s["sig"]]
                        if parsed:
                            tc = parsed.get("token_changes", [])
                            new_after.append({
                                "chain": "SOL", "sig": s["sig"], "ts": s["ts"],
                                "time": parsed.get("time"),
                                "programs": parsed.get("programs", [])[:5],
                                "token_changes": tc,
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_after.append({"chain": "SOL", "sig": s["sig"], "ts": s["ts"]})

                if _should_replace(new_after, rec.get("sender_after", []), bridge_ts, "after"):
                    rec["sender_after"] = new_after

            elif role == "receiver":
                # receiver_before: ts < bridge_ts, receiver_after: ts >= bridge_ts
                before_sigs = [s for s in sig_list if s["ts"] < bridge_ts][-CONTEXT_SIZE:]
                after_sigs = [s for s in sig_list if s["ts"] >= bridge_ts][:CONTEXT_SIZE]

                existing_sigs_b = set(tx.get("sig", "") for tx in rec.get("receiver_before", []))
                new_before = []
                for s in before_sigs:
                    if s["sig"] in existing_sigs_b:
                        for existing in rec.get("receiver_before", []):
                            if existing.get("sig") == s["sig"]:
                                new_before.append(existing)
                                break
                    else:
                        if s["sig"] not in sol_parsed_cache:
                            parsed = parse_sol_tx(s["sig"])
                            sol_parsed_cache[s["sig"]] = parsed
                            time.sleep(0.1)
                        parsed = sol_parsed_cache[s["sig"]]
                        if parsed:
                            tc = parsed.get("token_changes", [])
                            new_before.append({
                                "chain": "SOL", "sig": s["sig"], "ts": s["ts"],
                                "time": parsed.get("time"),
                                "programs": parsed.get("programs", [])[:5],
                                "token_changes": tc,
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_before.append({"chain": "SOL", "sig": s["sig"], "ts": s["ts"]})

                if _should_replace(new_before, rec.get("receiver_before", []), bridge_ts, "before"):
                    rec["receiver_before"] = new_before

                existing_sigs_a = set(tx.get("sig", "") for tx in rec.get("receiver_after", []))
                new_after = []
                for s in after_sigs:
                    if s["sig"] in existing_sigs_a:
                        for existing in rec.get("receiver_after", []):
                            if existing.get("sig") == s["sig"]:
                                new_after.append(existing)
                                break
                    else:
                        if s["sig"] not in sol_parsed_cache:
                            parsed = parse_sol_tx(s["sig"])
                            sol_parsed_cache[s["sig"]] = parsed
                            time.sleep(0.1)
                        parsed = sol_parsed_cache[s["sig"]]
                        if parsed:
                            tc = parsed.get("token_changes", [])
                            new_after.append({
                                "chain": "SOL", "sig": s["sig"], "ts": s["ts"],
                                "time": parsed.get("time"),
                                "programs": parsed.get("programs", [])[:5],
                                "token_changes": tc,
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_after.append({"chain": "SOL", "sig": s["sig"], "ts": s["ts"]})

                if _should_replace(new_after, rec.get("receiver_after", []), bridge_ts, "after"):
                    rec["receiver_after"] = new_after

        sol_done += 1
        if sol_done % 10 == 0:
            print(f"  SOL [{sol_done}/{len(sol_addr_records)}] parsed_cache={len(sol_parsed_cache)}", flush=True)
            # 中间保存
            _save(data)

    print(f"  SOL 完成: {sol_done} 地址, 解析 {len(sol_parsed_cache)} 笔")

    # ── 处理 ETH 地址 ──
    eth_done = 0
    eth_parsed_cache = {}

    for addr, items in eth_addr_records.items():
        min_ts = min(ts for _, _, ts in items)
        max_ts = max(ts for _, _, ts in items)

        # Etherscan 获取交易列表 — 覆盖所有桥接时间 ±1天
        tx_list = get_eth_txs_range(addr, min_ts - 86400, max_ts + 86400)
        if not tx_list:
            continue

        for rec, role, bridge_ts in items:
            target_addr = addr

            if role == "sender":
                before_txs = [t for t in tx_list if t["ts"] <= bridge_ts][-CONTEXT_SIZE:]
                after_txs = [t for t in tx_list if t["ts"] > bridge_ts][:CONTEXT_SIZE]

                existing_hashes = set(tx.get("hash", "") for tx in rec.get("sender_before", []))
                new_before = []
                for t in before_txs:
                    if t["hash"] in existing_hashes:
                        for existing in rec.get("sender_before", []):
                            if existing.get("hash") == t["hash"]:
                                new_before.append(existing)
                                break
                    else:
                        cache_key = (t["hash"], target_addr)
                        if cache_key not in eth_parsed_cache:
                            parsed = parse_eth_tx(t["hash"], target_addr)
                            eth_parsed_cache[cache_key] = parsed
                            time.sleep(0.1)
                        parsed = eth_parsed_cache[cache_key]
                        if parsed:
                            new_before.append({
                                "chain": "ETH", "hash": t["hash"], "ts": t["ts"],
                                "time": datetime.fromtimestamp(t["ts"], tz=timezone.utc).isoformat(),
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_before.append({"chain": "ETH", "hash": t["hash"], "ts": t["ts"]})

                if _should_replace(new_before, rec.get("sender_before", []), bridge_ts, "before"):
                    rec["sender_before"] = new_before

                existing_hashes_a = set(tx.get("hash", "") for tx in rec.get("sender_after", []))
                new_after = []
                for t in after_txs:
                    if t["hash"] in existing_hashes_a:
                        for existing in rec.get("sender_after", []):
                            if existing.get("hash") == t["hash"]:
                                new_after.append(existing)
                                break
                    else:
                        cache_key = (t["hash"], target_addr)
                        if cache_key not in eth_parsed_cache:
                            parsed = parse_eth_tx(t["hash"], target_addr)
                            eth_parsed_cache[cache_key] = parsed
                            time.sleep(0.1)
                        parsed = eth_parsed_cache[cache_key]
                        if parsed:
                            new_after.append({
                                "chain": "ETH", "hash": t["hash"], "ts": t["ts"],
                                "time": datetime.fromtimestamp(t["ts"], tz=timezone.utc).isoformat(),
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_after.append({"chain": "ETH", "hash": t["hash"], "ts": t["ts"]})

                if _should_replace(new_after, rec.get("sender_after", []), bridge_ts, "after"):
                    rec["sender_after"] = new_after

            elif role == "receiver":
                before_txs = [t for t in tx_list if t["ts"] < bridge_ts][-CONTEXT_SIZE:]
                after_txs = [t for t in tx_list if t["ts"] >= bridge_ts][:CONTEXT_SIZE]

                existing_hashes_b = set(tx.get("hash", "") for tx in rec.get("receiver_before", []))
                new_before = []
                for t in before_txs:
                    if t["hash"] in existing_hashes_b:
                        for existing in rec.get("receiver_before", []):
                            if existing.get("hash") == t["hash"]:
                                new_before.append(existing)
                                break
                    else:
                        cache_key = (t["hash"], target_addr)
                        if cache_key not in eth_parsed_cache:
                            parsed = parse_eth_tx(t["hash"], target_addr)
                            eth_parsed_cache[cache_key] = parsed
                            time.sleep(0.1)
                        parsed = eth_parsed_cache[cache_key]
                        if parsed:
                            new_before.append({
                                "chain": "ETH", "hash": t["hash"], "ts": t["ts"],
                                "time": datetime.fromtimestamp(t["ts"], tz=timezone.utc).isoformat(),
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_before.append({"chain": "ETH", "hash": t["hash"], "ts": t["ts"]})

                if _should_replace(new_before, rec.get("receiver_before", []), bridge_ts, "before"):
                    rec["receiver_before"] = new_before

                existing_hashes_a = set(tx.get("hash", "") for tx in rec.get("receiver_after", []))
                new_after = []
                for t in after_txs:
                    if t["hash"] in existing_hashes_a:
                        for existing in rec.get("receiver_after", []):
                            if existing.get("hash") == t["hash"]:
                                new_after.append(existing)
                                break
                    else:
                        cache_key = (t["hash"], target_addr)
                        if cache_key not in eth_parsed_cache:
                            parsed = parse_eth_tx(t["hash"], target_addr)
                            eth_parsed_cache[cache_key] = parsed
                            time.sleep(0.1)
                        parsed = eth_parsed_cache[cache_key]
                        if parsed:
                            new_after.append({
                                "chain": "ETH", "hash": t["hash"], "ts": t["ts"],
                                "time": datetime.fromtimestamp(t["ts"], tz=timezone.utc).isoformat(),
                                "summary": parsed.get("summary"),
                            })
                        else:
                            new_after.append({"chain": "ETH", "hash": t["hash"], "ts": t["ts"]})

                if _should_replace(new_after, rec.get("receiver_after", []), bridge_ts, "after"):
                    rec["receiver_after"] = new_after

        eth_done += 1
        if eth_done % 5 == 0:
            print(f"  ETH [{eth_done}/{len(eth_addr_records)}] parsed_cache={len(eth_parsed_cache)}", flush=True)
            _save(data)

    print(f"  ETH 完成: {eth_done} 地址, 解析 {len(eth_parsed_cache)} 笔")

    # 最终保存
    _save(data, done=True)

    # 统计
    sb_full = sum(1 for r in records if len(r.get("sender_before", [])) >= CONTEXT_SIZE)
    sa_full = sum(1 for r in records if len(r.get("sender_after", [])) >= CONTEXT_SIZE)
    rb_full = sum(1 for r in records if len(r.get("receiver_before", [])) >= CONTEXT_SIZE)
    ra_full = sum(1 for r in records if len(r.get("receiver_after", [])) >= CONTEXT_SIZE)
    print(f"\n{'=' * 60}")
    print(f"  sender_before >= {CONTEXT_SIZE}: {sb_full}/{len(records)}")
    print(f"  sender_after >= {CONTEXT_SIZE}: {sa_full}/{len(records)}")
    print(f"  receiver_before >= {CONTEXT_SIZE}: {rb_full}/{len(records)}")
    print(f"  receiver_after >= {CONTEXT_SIZE}: {ra_full}/{len(records)}")


def _save(data, done=False):
    data["meta"]["updated"] = datetime.now(timezone.utc).isoformat()
    data["meta"]["backfilled"] = True
    with open(CONTEXT_COMPLETED, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if done:
        size_mb = CONTEXT_COMPLETED.stat().st_size / 1024 / 1024
        print(f"  保存: {CONTEXT_COMPLETED.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
