#!/usr/bin/env python3
"""
批量解析 sol_signatures.json 中所有 Solana 交易。

解析内容:
  - SPL Token 转账 (pre/postTokenBalances 差值)
  - SOL 转账 (pre/postBalances 差值)
  - 调用的程序 (DEX识别: Jupiter, Raydium, Orca等)

使用 Chainstack Archive RPC。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/parse_all_sol_txs.py
"""

import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path

RPC_URL = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"

DIR = Path(__file__).parent / "use"
INPUT = DIR / "signatures" / "sol_signatures.json"
OUTPUT = DIR / "parsed" / "sol_txs_parsed.json"

# 已知程序ID -> 名称
KNOWN_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter v4",
    "jupoNjAxXgZ4rjzxzPMP4oxduvQsQtZzyknqvzYNrNu": "Jupiter Limit Order",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM",
    "routeUGWgWzqBWFcrCfv8tritsqukccJPu3q5GPP3xS": "Raydium Route",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": "Orca v2",
    "DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59qw1": "Orca v1",
    "srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX": "Serum DEX",
    "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EQMQvR": "Openbook v2",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora Pools",
    "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY": "Phoenix",
    "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb": "Wormhole Portal",
    "worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth": "Wormhole Core",
    "NTtAaoDJhkeHeaVUHnyhwbPNAN6WgBpHkHBTc6d7vLu": "Wormhole NTT",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL Token",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": "Associated Token",
    "11111111111111111111111111111111": "System Program",
    "ComputeBudget111111111111111111111111111111": "Compute Budget",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr": "Memo",
}

# Token mint缓存
MINT_CACHE = {}


def rpc_call(method, params):
    for attempt in range(5):
        try:
            resp = requests.post(RPC_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                if code == 429 or code == -32429:
                    time.sleep(2 ** attempt)
                    continue
                return None
            return data.get("result")
        except Exception:
            time.sleep(1)
    return None


def get_mint_symbol(mint):
    """尝试获取token symbol（从缓存或已知列表）"""
    if mint in MINT_CACHE:
        return MINT_CACHE[mint]
    # 常见token
    known = {
        "So11111111111111111111111111111111111111112": "SOL",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
        "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH",
        "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC",
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
        "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "stSOL",
        "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "jitoSOL",
        "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": "WIF",
    }
    if mint in known:
        MINT_CACHE[mint] = known[mint]
        return known[mint]
    MINT_CACHE[mint] = mint
    return mint


def _sol_summary(token_changes, signer):
    """提取 signer 的 sold/bought。如果 signer 不在 owner 中，选变动最多的地址。"""
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

    # 1. signer 直接匹配
    if signer:
        sold, bought = extract(signer)
        if sold or bought:
            return fmt(sold, bought)

    # 2. 回退: 变动最多的地址
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
            best_score = score
            best_addr = addr

    if best_addr:
        sold, bought = extract(best_addr)
        if sold or bought:
            return fmt(sold, bought)

    return {"sold": [], "bought": []}


def parse_transaction(sig):
    """解析一笔Solana交易"""
    result = rpc_call("getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
    }])
    if not result:
        return None

    tx = result.get("transaction", {})
    meta = result.get("meta", {})
    block_time = result.get("blockTime", 0)

    # 1. SOL余额变化
    accounts = tx.get("message", {}).get("accountKeys", [])
    pre_sol = meta.get("preBalances", [])
    post_sol = meta.get("postBalances", [])
    sol_changes = []
    for i, (pre, post) in enumerate(zip(pre_sol, post_sol)):
        diff = (post - pre) / 1e9
        if abs(diff) > 0.000001:
            acc = accounts[i] if i < len(accounts) else f"account_{i}"
            if isinstance(acc, dict):
                acc = acc.get("pubkey", f"account_{i}")
            sol_changes.append({"account": acc, "change_sol": round(diff, 9)})

    # 2. SPL Token余额变化
    pre_tokens = {}
    post_tokens = {}
    for b in (meta.get("preTokenBalances") or []):
        owner = b.get("owner", "")
        mint = b.get("mint", "")
        amt = float(b.get("uiTokenAmount", {}).get("uiAmountString") or 0)
        key = (owner, mint)
        pre_tokens[key] = pre_tokens.get(key, 0) + amt
    for b in (meta.get("postTokenBalances") or []):
        owner = b.get("owner", "")
        mint = b.get("mint", "")
        amt = float(b.get("uiTokenAmount", {}).get("uiAmountString") or 0)
        key = (owner, mint)
        post_tokens[key] = post_tokens.get(key, 0) + amt

    token_changes = []
    for key in set(pre_tokens.keys()) | set(post_tokens.keys()):
        diff = post_tokens.get(key, 0) - pre_tokens.get(key, 0)
        if abs(diff) > 0.000001:
            sym = get_mint_symbol(key[1])
            token_changes.append({
                "owner": key[0],
                "mint": key[1],
                "symbol": sym,
                "change": round(diff, 9),
            })

    # 3. 调用的程序
    instructions = tx.get("message", {}).get("instructions", [])
    inner = meta.get("innerInstructions", [])
    program_ids = set()
    for ix in instructions:
        if isinstance(ix, dict):
            pid = ix.get("programId", "")
            if pid:
                program_ids.add(pid)
    for inner_group in (inner or []):
        for ix in inner_group.get("instructions", []):
            if isinstance(ix, dict):
                pid = ix.get("programId", "")
                if pid:
                    program_ids.add(pid)

    programs = []
    dex_used = []
    for pid in program_ids:
        name = KNOWN_PROGRAMS.get(pid, pid[:16] + "...")
        programs.append({"id": pid, "name": name})
        # 识别DEX
        if any(dex in name for dex in ["Jupiter", "Raydium", "Orca", "Serum", "Openbook", "Meteora", "Phoenix"]):
            dex_used.append(name)

    # 4. 交易类型推断
    tx_type = "unknown"
    if dex_used:
        tx_type = "dex_swap"
    elif any("Wormhole" in p["name"] for p in programs):
        tx_type = "bridge"
    elif token_changes and not dex_used:
        tx_type = "token_transfer"
    elif sol_changes and not token_changes:
        tx_type = "sol_transfer"

    fee = (meta.get("fee") or 0) / 1e9

    # 5. Summary: signer 的 sold/bought
    signer = sol_changes[0]["account"] if sol_changes else ""
    summary = _sol_summary(token_changes, signer)

    return {
        "sig": sig,
        "ts": block_time,
        "time": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else None,
        "status": "failed" if meta.get("err") else "success",
        "fee_sol": fee,
        "tx_type": tx_type,
        "dex": dex_used if dex_used else None,
        "programs": [p["name"] for p in programs],
        "signer": signer,
        "sol_changes": sol_changes,
        "token_changes": token_changes,
        "summary": summary,
    }


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


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    # 收集唯一签名
    all_sigs = set()
    for info in data["results"].values():
        for s in info.get("signatures", []):
            all_sigs.add(s["sig"])

    print(f"唯一签名: {len(all_sigs):,} 笔")
    print()

    # 断点续跑
    results = {}
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            existing = json.load(f)
        results = {r["sig"]: r for r in existing.get("transactions", [])}
        print(f"已有 {len(results)} 笔，跳过\n")

    # 加载mint缓存
    mint_cache_path = DIR / "cache" / "mint_cache.json"
    if mint_cache_path.exists():
        with open(mint_cache_path, encoding="utf-8") as f:
            saved = json.load(f)
        MINT_CACHE.update(saved)
        print(f"Mint缓存: {len(MINT_CACHE)} 个\n")

    total = len(all_sigs)
    to_process = sorted(all_sigs - set(results.keys()))
    done = 0
    errors = 0
    print(f"待处理: {len(to_process):,} 笔")

    for sig in to_process:
        parsed = parse_transaction(sig)
        if parsed:
            results[sig] = parsed
        else:
            errors += 1
        done += 1

        if done % 100 == 0:
            print(f"  [{len(results)}/{total}]  解析{done}笔  失败{errors}  "
                  f"mint缓存{len(MINT_CACHE)}", flush=True)
        if done % 500 == 0:
            _save(results, total)
            with open(mint_cache_path, "w", encoding="utf-8") as f:
                json.dump(MINT_CACHE, f, ensure_ascii=False)

    _save(results, total, done=True)
    with open(mint_cache_path, "w", encoding="utf-8") as f:
        json.dump(MINT_CACHE, f, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  总交易: {len(results):,} 笔")
    print(f"  失败: {errors}")

    # 统计交易类型
    type_counts = {}
    dex_counts = {}
    for r in results.values():
        t = r.get("tx_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
        for d in (r.get("dex") or []):
            dex_counts[d] = dex_counts.get(d, 0) + 1

    print(f"\n交易类型:")
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t:<20s} {cnt:>8,}")

    if dex_counts:
        print(f"\nDEX分布:")
        for d, cnt in sorted(dex_counts.items(), key=lambda x: -x[1]):
            print(f"  {d:<25s} {cnt:>8,}")

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n保存: {OUTPUT.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
