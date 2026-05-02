#!/usr/bin/env python3
"""
批量解析 matched_context_filtered.json 中所有 context 交易。

1. 去重收集所有 SOL sig 和 ETH hash
2. 并发解析（SOL 1次RPC, ETH 2次RPC）
3. 将解析结果塞回 context，输出新 JSON

用法:
    python wormhole_data/batch_parse_context.py

输入: recent_30d/matched/matched_context_filtered.json
输出: recent_30d/matched/matched_context_parsed.json

支持断点续跑：中间结果保存在 _cache/ 目录。
"""

import json
import time
import requests
import os
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 路径 ──
DIR = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
INPUT_FILE = DIR / "matched_context_filtered.json"
OUTPUT_FILE = DIR / "matched_context_parsed.json"
CACHE_DIR = DIR / "address_txs" / "_cache"

SOL_CACHE_FILE = CACHE_DIR / "sol_parsed_cache.json"
ETH_CACHE_FILE = CACHE_DIR / "eth_parsed_cache.json"

# ── RPC ──
SOL_RPC = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"
ETH_RPC = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"

# ── Token caches ──
ERC20_CACHE_FILE = Path(__file__).parent / "use" / "portal_full" / "erc20_cache.json"

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

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# ── 全局 ERC20 缓存 ──
ETH_TOKEN_CACHE = {}


def load_erc20_cache():
    """加载 ERC20 symbol+decimals 缓存"""
    global ETH_TOKEN_CACHE
    if ERC20_CACHE_FILE.exists():
        data = json.load(open(ERC20_CACHE_FILE))
        for addr, info in data.items():
            ETH_TOKEN_CACHE[addr.lower()] = (info["symbol"], info["decimals"])
    print(f"ERC20 缓存: {len(ETH_TOKEN_CACHE)} 个")


def get_eth_token_info(addr):
    addr = addr.lower()
    if addr in ETH_TOKEN_CACHE:
        return ETH_TOKEN_CACHE[addr]
    ETH_TOKEN_CACHE[addr] = (addr, 18)
    return (addr, 18)


# ══════════════════════════════════════════════
#  SOL 解析
# ══════════════════════════════════════════════

def _sol_rpc(method, params, retries=3):
    for attempt in range(retries):
        try:
            resp = requests.post(SOL_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                time.sleep(0.5 * (attempt + 1))
                continue
            return data.get("result")
        except Exception:
            time.sleep(0.5)
    return None


def parse_sol_tx(sig):
    """解析单笔 SOL 交易，返回精简结果"""
    result = _sol_rpc("getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
    }])
    if not result:
        return {"sig": sig, "error": "rpc_failed"}

    meta = result.get("meta", {})
    tx_msg = result.get("transaction", {}).get("message", {})
    block_time = result.get("blockTime", 0)
    slot = result.get("slot", 0)

    if meta.get("err"):
        return {"sig": sig, "ts": block_time, "slot": slot, "error": "tx_failed"}

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
            sol_changes.append({"account": accounts[i], "change": round(diff, 9)})

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
        owner = pre_t.get("owner") or post_t.get("owner", "")
        pre_amt = float(pre_t.get("uiTokenAmount", {}).get("uiAmount") or 0)
        post_amt = float(post_t.get("uiTokenAmount", {}).get("uiAmount") or 0)
        change = post_amt - pre_amt
        if abs(change) > 1e-10:
            token_changes.append({
                "mint": mint,
                "symbol": MINT_SYMBOLS.get(mint, mint),
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

    # Signer & summary
    signer = ""
    for ak in account_keys:
        if isinstance(ak, dict) and ak.get("signer"):
            signer = ak.get("pubkey", "")
            break
    if not signer and accounts:
        signer = accounts[0]

    sold, bought = [], []
    for tc in token_changes:
        if tc.get("owner") != signer:
            continue
        if tc["change"] < -1e-8:
            sold.append({"token": tc["symbol"], "amount": -tc["change"]})
        elif tc["change"] > 1e-8:
            bought.append({"token": tc["symbol"], "amount": tc["change"]})

    return {
        "sig": sig,
        "ts": block_time,
        "slot": slot,
        "time": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else None,
        "fee_sol": meta.get("fee", 0) / 1e9,
        "signer": signer,
        "programs": programs,
        "token_changes": token_changes,
        "sol_changes": sol_changes[:5],
        "summary": {"sold": sold, "bought": bought},
    }


# ══════════════════════════════════════════════
#  ETH 解析
# ══════════════════════════════════════════════

def _eth_rpc(method, params, retries=3):
    for attempt in range(retries):
        try:
            resp = requests.post(ETH_RPC, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                if code == 429:
                    time.sleep(1 * (attempt + 1))
                    continue
                return None
            return data.get("result")
        except Exception:
            time.sleep(0.5)
    return None


def parse_eth_tx(txhash, target_addr=None):
    """解析单笔 ETH 交易，返回精简结果"""
    # 同时发起 receipt 和 tx 查询（串行，因为共享连接）
    receipt = _eth_rpc("eth_getTransactionReceipt", [txhash])
    if not receipt:
        return {"hash": txhash, "error": "rpc_failed"}

    tx_detail = _eth_rpc("eth_getTransactionByHash", [txhash])

    tx_from = ((tx_detail.get("from") or "") if tx_detail else "").lower()
    tx_to = ((tx_detail.get("to") or "") if tx_detail else "").lower()
    tx_value = int(tx_detail.get("value", "0x0"), 16) if tx_detail else 0
    block_num = int(receipt.get("blockNumber", "0x0"), 16)
    gas_used = int(receipt.get("gasUsed", "0x0"), 16)
    gas_price = int(receipt.get("effectiveGasPrice", "0x0"), 16)
    fee_eth = gas_used * gas_price / 1e18
    status = "success" if receipt.get("status") == "0x1" else "failed"

    # 区块时间：从 receipt 的 log 中尝试获取，或者用已知的 timestamp
    block_ts = 0
    for log in receipt.get("logs", []):
        bt = log.get("blockTimestamp")
        if bt:
            block_ts = int(bt, 16)
            break

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
    sold, bought = [], []
    outflow, inflow = {}, {}
    for e in events:
        token = e.get("token", "?")
        if e.get("from", "").lower() == perspective:
            outflow[token] = outflow.get(token, 0) + e["amount"]
        if e.get("to", "").lower() == perspective:
            inflow[token] = inflow.get(token, 0) + e["amount"]
    for t in set(outflow.keys()) | set(inflow.keys()):
        if outflow.get(t, 0) > 1e-8:
            sold.append({"token": t, "amount": outflow[t]})
        if inflow.get(t, 0) > 1e-8:
            bought.append({"token": t, "amount": inflow[t]})

    return {
        "hash": txhash,
        "ts": block_ts,
        "block": block_num,
        "time": datetime.fromtimestamp(block_ts, tz=timezone.utc).isoformat() if block_ts else None,
        "status": status,
        "from": tx_from,
        "to": tx_to,
        "fee_eth": fee_eth,
        "gas_used": gas_used,
        "events": events,
        "summary": {"sold": sold, "bought": bought},
    }


# ══════════════════════════════════════════════
#  批量处理
# ══════════════════════════════════════════════

def batch_parse_sol(sigs, cache, max_workers=20):
    """并发解析 SOL 交易"""
    to_parse = [s for s in sigs if s not in cache]
    if not to_parse:
        print(f"  SOL: 全部已缓存 ({len(sigs)} 笔)")
        return

    print(f"  SOL: 需解析 {len(to_parse)} 笔 (已缓存 {len(sigs) - len(to_parse)})")
    done = 0
    errors = 0

    def _do(sig):
        return sig, parse_sol_tx(sig)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_do, sig): sig for sig in to_parse}
        for future in as_completed(futures):
            sig, result = future.result()
            cache[sig] = result
            done += 1
            if result.get("error"):
                errors += 1
            if done % 500 == 0:
                print(f"    SOL 进度: {done}/{len(to_parse)} (errors={errors})")
                # 定期保存缓存
                with open(SOL_CACHE_FILE, "w") as f:
                    json.dump(cache, f, ensure_ascii=False)

    # 最终保存
    with open(SOL_CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    print(f"  SOL 完成: {done} 笔, errors={errors}")


def batch_parse_eth(hashes, cache, max_workers=20):
    """并发解析 ETH 交易"""
    to_parse = [h for h in hashes if h not in cache]
    if not to_parse:
        print(f"  ETH: 全部已缓存 ({len(hashes)} 笔)")
        return

    print(f"  ETH: 需解析 {len(to_parse)} 笔 (已缓存 {len(hashes) - len(to_parse)})")
    done = 0
    errors = 0

    def _do(h):
        return h, parse_eth_tx(h)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_do, h): h for h in to_parse}
        for future in as_completed(futures):
            h, result = future.result()
            cache[h] = result
            done += 1
            if result.get("error"):
                errors += 1
            if done % 500 == 0:
                print(f"    ETH 进度: {done}/{len(to_parse)} (errors={errors})")
                with open(ETH_CACHE_FILE, "w") as f:
                    json.dump(cache, f, ensure_ascii=False)

    with open(ETH_CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    print(f"  ETH 完成: {done} 笔, errors={errors}")


def main():
    load_erc20_cache()

    # 加载输入
    ctx = json.load(open(INPUT_FILE, encoding="utf-8"))
    records = ctx["records"]
    print(f"输入: {len(records)} 条记录")

    # 收集所有需要解析的 sig/hash（去重）
    sol_sigs = set()
    eth_hashes = set()
    # 记录每个 sig/hash 对应的 target_addr（用于 ETH summary 视角）
    eth_target_addrs = {}

    for r in records:
        sol_sender = r.get("sol_sender", "")
        eth_from = r.get("eth_from", "")

        # 跨链交易本身
        sol_sigs.add(r["sol_sig"])
        eth_hashes.add(r["eth_hash"])
        eth_target_addrs[r["eth_hash"]] = eth_from

        # Context 交易
        for s in (r.get("sol_context") or []):
            sol_sigs.add(s["sig"])
        for e in (r.get("eth_context") or []):
            eth_hashes.add(e["hash"])
            if e["hash"] not in eth_target_addrs:
                eth_target_addrs[e["hash"]] = eth_from

    print(f"去重: SOL {len(sol_sigs)} 笔, ETH {len(eth_hashes)} 笔")

    # 加载缓存
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sol_cache = {}
    eth_cache = {}
    if SOL_CACHE_FILE.exists():
        sol_cache = json.load(open(SOL_CACHE_FILE))
        print(f"SOL 缓存已加载: {len(sol_cache)} 笔")
    if ETH_CACHE_FILE.exists():
        eth_cache = json.load(open(ETH_CACHE_FILE))
        print(f"ETH 缓存已加载: {len(eth_cache)} 笔")

    # 批量解析
    print("\n开始解析...")
    t0 = time.time()

    batch_parse_sol(list(sol_sigs), sol_cache, max_workers=20)
    batch_parse_eth(list(eth_hashes), eth_cache, max_workers=10)

    elapsed = time.time() - t0
    print(f"\n解析耗时: {elapsed:.1f}s")

    # 组装输出
    print("\n组装输出...")
    for r in records:
        # 跨链交易本身的解析结果
        r["sol_parsed"] = sol_cache.get(r["sol_sig"])
        r["eth_parsed"] = eth_cache.get(r["eth_hash"])

        # SOL context 交易的解析结果
        if r.get("sol_context"):
            for s in r["sol_context"]:
                parsed = sol_cache.get(s["sig"])
                if parsed:
                    s["parsed"] = parsed

        # ETH context 交易的解析结果
        if r.get("eth_context"):
            for e in r["eth_context"]:
                parsed = eth_cache.get(e["hash"])
                if parsed:
                    e["parsed"] = parsed

    output = {
        "meta": {
            "total": len(records),
            "sol_parsed": sum(1 for s in sol_cache.values() if not s.get("error")),
            "eth_parsed": sum(1 for e in eth_cache.values() if not e.get("error")),
            "sol_errors": sum(1 for s in sol_cache.values() if s.get("error")),
            "eth_errors": sum(1 for e in eth_cache.values() if e.get("error")),
            "generated": datetime.now(timezone.utc).isoformat(),
        },
        "records": records,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n输出: {OUTPUT_FILE}")
    print(f"  总记录: {len(records)}")
    print(f"  SOL 解析成功: {output['meta']['sol_parsed']}, 失败: {output['meta']['sol_errors']}")
    print(f"  ETH 解析成功: {output['meta']['eth_parsed']}, 失败: {output['meta']['eth_errors']}")


if __name__ == "__main__":
    main()
