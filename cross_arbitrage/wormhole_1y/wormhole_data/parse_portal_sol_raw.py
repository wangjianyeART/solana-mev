#!/usr/bin/env python3
"""
解析 portal_sol_raw/ 中的 batch 文件，提取每笔 Portal 桥交易的详情。

解析内容:
  - 交易方向: MintTo=inbound(跨链→Solana), Burn=outbound(Solana→跨链)
  - SPL token 变动 (mint, amount, owner)
  - SOL 变动
  - 签名者 (发起人)

用法:
    python wormhole_data/parse_portal_sol_raw.py              # 全部
    python wormhole_data/parse_portal_sol_raw.py --limit 100  # 前100笔
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent / "use"
RAW_DIR = DIR / "portal_full" / "portal_sol_raw"
OUTPUT = DIR / "portal_full" / "portal_sol_parsed.json"

PORTAL_PROGRAM = "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb"

# 常见 Solana mint → symbol
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


def get_mint_symbol(mint):
    if mint in MINT_SYMBOLS:
        return MINT_SYMBOLS[mint]
    return mint


def parse_one(entry):
    """解析一笔 Portal Solana 交易"""
    data = entry.get("data")
    if not data:
        return None

    sig = entry["sig"]
    meta = data.get("meta", {})
    tx_msg = data.get("transaction", {}).get("message", {})
    block_time = data.get("blockTime", 0)
    slot = data.get("slot", 0)

    if meta.get("err"):
        return None  # 跳过链上失败的交易

    fee = meta.get("fee", 0)
    logs = meta.get("logMessages", [])

    # 签名者
    signers = [k["pubkey"] for k in tx_msg.get("accountKeys", []) if k.get("signer")]
    sender = signers[0] if signers else ""

    # 判断方向
    has_mint = any("Instruction: MintTo" in l for l in logs)
    has_burn = any("Instruction: Burn" in l for l in logs)
    has_approve = any("Instruction: Approve" in l for l in logs)
    has_transfer = any("Instruction: Transfer" in l for l in logs)

    if has_mint and not has_burn:
        direction = "inbound"   # 跨链→Solana (Portal mint wrapped token)
    elif has_burn and not has_mint:
        direction = "outbound"  # Solana→跨链 (Portal burn wrapped token)
    elif has_transfer and has_approve:
        direction = "outbound"  # Solana→跨链 (native SOL/token lock)
    elif has_transfer and not has_mint and not has_burn:
        direction = "outbound"  # native token lock
    elif has_mint and has_burn:
        direction = "swap"      # 少见：可能是换wrapped版本
    else:
        direction = "unknown"

    # 解析 token 变动
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

    # SOL 变动
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

    # 提取用户的 token 变动
    user_changes = [tc for tc in token_changes if tc["owner"] == sender]

    return {
        "sig": sig,
        "ts": block_time,
        "time": datetime.fromtimestamp(block_time, tz=timezone.utc).isoformat() if block_time else None,
        "slot": slot,
        "sender": sender,
        "fee_sol": fee / 1e9,
        "direction": direction,
        "token_changes": token_changes,
        "sol_changes": sol_changes[:5],  # 只保留前5个SOL变动（太多了）
        "summary": {
            "user_token_changes": [{"symbol": tc["symbol"], "change": tc["change"]} for tc in user_changes],
        },
    }


def main():
    # --limit N
    limit = None
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])

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
            parsed = parse_one(entry)
            if parsed:
                results.append(parsed)
                count += 1
        if limit and count >= limit:
            break
        if len(results) % 10000 < 1000:
            print(f"  已解析 {len(results):,} 笔...", flush=True)

    # 排序
    results.sort(key=lambda x: -(x.get("ts") or 0))

    # 统计
    directions = {}
    tokens = {}
    for r in results:
        d = r["direction"]
        directions[d] = directions.get(d, 0) + 1
        for tc in r["token_changes"]:
            sym = tc["symbol"]
            tokens[sym] = tokens.get(sym, 0) + 1

    output = {
        "meta": {
            "total_parsed": len(results),
            "errors": errors,
            "directions": directions,
            "top_tokens": dict(sorted(tokens.items(), key=lambda x: -x[1])[:30]),
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "transactions": results,
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"  解析: {len(results):,} 笔  失败/跳过: {errors}")
    print(f"  保存: {OUTPUT.name} ({size_mb:.1f} MB)")

    print(f"\n方向:")
    for d, cnt in sorted(directions.items(), key=lambda x: -x[1]):
        print(f"  {d:<12s} {cnt:>8,}")

    print(f"\nTop 20 Token:")
    for sym, cnt in sorted(tokens.items(), key=lambda x: -x[1])[:20]:
        print(f"  {sym:<45s} {cnt:>8,}")

    # 样例
    print(f"\n样例 (前5笔):")
    for r in results[:5]:
        print(f"  {r['sig'][:40]}...  {r['time']}  {r['direction']}")
        for tc in r["token_changes"]:
            print(f"    {tc['symbol']:<20s} {tc['change']:>18,.4f}  owner={tc['owner'][:20]}")


if __name__ == "__main__":
    main()
