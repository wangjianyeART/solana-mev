#!/usr/bin/env python3
"""
批量解析 SOL mint 地址 -> token symbol。

从 bridge_context_completed.json 中提取所有未解析的 SOL mint 地址，
使用 Birdeye API 查询 token metadata，更新 mint_cache.json。

用法:
    python wormhole_data/resolve_sol_mints.py
"""

import json
import time
import requests
from pathlib import Path

DIR = Path(__file__).parent / "use"
CONTEXT_PATH = DIR / "parsed" / "bridge_context_completed.json"
MINT_CACHE_PATH = DIR / "cache" / "mint_cache.json"
CROSSCHAIN_PATH = Path(__file__).parent.parent / "bridge" / "crosschain_sol_eth_tokens.json"
NTT_PAIRS_PATH = DIR / "ntt_eth_sol_pairs.json"

BIRDEYE_KEY = "4c5b54082842492b8dec85b996d101bb"
BIRDEYE_URL = "https://public-api.birdeye.so"

# Chainstack Solana RPC 备用
RPC_URL = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"


def load_known_mappings():
    """加载所有已知的 mint -> symbol 映射"""
    known = {}

    # mint_cache.json
    if MINT_CACHE_PATH.exists():
        with open(MINT_CACHE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        for mint, sym in raw.items():
            if len(sym) < 20 and sym != mint[:8] + "...":
                known[mint] = sym

    # crosschain_sol_eth_tokens.json
    if CROSSCHAIN_PATH.exists():
        with open(CROSSCHAIN_PATH, encoding="utf-8") as f:
            d = json.load(f)
        for t in d.get("confirmed_crosschain", []):
            sol_mint = t.get("sol_mint", "")
            sym = t.get("symbol", "")
            if sol_mint and sym:
                known[sol_mint] = sym

    # ntt_eth_sol_pairs.json
    if NTT_PAIRS_PATH.exists():
        with open(NTT_PAIRS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        for p in d.get("pairs", []):
            sol_mint = p.get("sol_token", "")
            sym = p.get("symbol", "")
            if sol_mint and sym:
                known[sol_mint] = sym

    # 常用 token 硬编码
    known.update({
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
        "So11111111111111111111111111111111111111112": "SOL",
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
        "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "stSOL",
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
        "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL": "JTO",
        "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3": "PYTH",
        "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ": "W",
        "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH",
        "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC",
        "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "WBTC",
        "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo": "PYUSD",
        "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN": "TRUMP",
    })

    return known


def collect_unresolved_mints():
    """从 bridge_context_completed.json 收集所有未解析的 SOL mint"""
    with open(CONTEXT_PATH, encoding="utf-8") as f:
        data = json.load(f)

    mints = {}  # mint -> count
    for r in data["records"]:
        for d in ["sender_before", "sender_after", "receiver_before", "receiver_after"]:
            for tx in r.get(d, []):
                if tx.get("chain") != "SOL":
                    continue
                for field in ["sold", "bought"]:
                    for item in tx.get("summary", {}).get(field, []):
                        t = item["token"]
                        if len(t) >= 20:  # 长字符串 = mint 地址
                            mints[t] = mints.get(t, 0) + 1

    return mints


def query_birdeye(mint):
    """用 Birdeye API v1 token_overview 查 token metadata"""
    headers = {
        "X-API-KEY": BIRDEYE_KEY,
        "x-chain": "solana",
    }
    for attempt in range(3):
        try:
            resp = requests.get(
                f"{BIRDEYE_URL}/defi/token_overview",
                params={"address": mint},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                if data:
                    symbol = data.get("symbol", "")
                    name = data.get("name", "")
                    decimals = data.get("decimals", 0)
                    if symbol:
                        return {"symbol": symbol, "name": name, "decimals": decimals}
                return None  # data 为空，token 不存在
            elif resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            else:
                return None
        except Exception:
            time.sleep(1)
    return None


def query_rpc_metadata(mint):
    """备用：用 Chainstack Solana RPC 查 token metadata (通过 getAsset DAS API)"""
    try:
        resp = requests.post(RPC_URL, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getAsset",
            "params": {"id": mint},
        }, timeout=15)
        data = resp.json()
        result = data.get("result", {})
        content = result.get("content", {})
        metadata = content.get("metadata", {})
        symbol = metadata.get("symbol", "")
        name = metadata.get("name", "")
        if symbol:
            return {"symbol": symbol, "name": name, "decimals": 0}
    except Exception:
        pass
    return None


def main():
    known = load_known_mappings()
    print(f"已知映射: {len(known)} 个")

    all_mints = collect_unresolved_mints()
    print(f"数据中 SOL mint 总数: {len(all_mints)} 个")

    # 过滤已知的
    to_resolve = {m: c for m, c in all_mints.items() if m not in known}
    print(f"待查询: {len(to_resolve)} 个")

    if not to_resolve:
        print("全部已解析，无需查询")
        return

    # 按出现次数降序排列
    sorted_mints = sorted(to_resolve.items(), key=lambda x: -x[1])

    resolved = {}
    failed = []
    total = len(sorted_mints)

    for i, (mint, count) in enumerate(sorted_mints):
        # 先试 Birdeye
        result = query_birdeye(mint)

        # Birdeye 失败则试 RPC
        if not result:
            result = query_rpc_metadata(mint)

        if result:
            symbol = result["symbol"]
            resolved[mint] = symbol
            known[mint] = symbol
            if (i + 1) % 20 == 0 or i < 5:
                print(f"  [{i+1}/{total}] {mint[:20]}... -> {symbol} (出现{count}次)")
        else:
            failed.append(mint)
            if (i + 1) % 20 == 0:
                print(f"  [{i+1}/{total}] 已解析{len(resolved)} 失败{len(failed)}")

        # Birdeye 限速 ~75/min
        time.sleep(0.85)

        # 每 50 个保存一次
        if (i + 1) % 50 == 0:
            _save_cache(known)

    _save_cache(known)

    print(f"\n{'=' * 60}")
    print(f"  总待查询: {total}")
    print(f"  成功解析: {len(resolved)}")
    print(f"  查询失败: {len(failed)}")
    print(f"  mint_cache 总量: {len(known)}")

    # 显示解析结果 top 30
    print(f"\n  新解析的 token (按出现频次):")
    for mint, count in sorted_mints[:30]:
        sym = resolved.get(mint, "FAILED")
        print(f"    {sym:<20s} {mint[:30]}... {count:>6}次")

    if failed:
        print(f"\n  失败的 mint (前10):")
        for m in failed[:10]:
            print(f"    {m}")


def _save_cache(known):
    """保存更新后的 mint_cache"""
    MINT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MINT_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(known, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
