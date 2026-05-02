#!/usr/bin/env python3
"""
Wormhole NTT  ETH <-> SOL  Token Pair 详细信息采集器

流程:
  1. 读取 ntt_eth_sol_pairs.json 中的 20 个 ETH<->SOL pair
  2. 先用 ETH token address (chainId=2) 查 API
  3. 如果 API 返回无 Solana peer，说明 home 在 Solana，
     从 ntt_all_contracts.json 找 Solana token address，再用 chainId=1 反查
  4. 保存完整信息到 ntt_eth_sol_pairs_full.json

API: https://api.wormholescan.io/api/v1/ntt/token/{chainId}/{tokenAddress}
  chainId=2 -> Ethereum (wormhole chain id)
  chainId=1 -> Solana  (wormhole chain id)

用法:
    python fetch_ntt_eth_sol_pairs.py
"""

import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── CONFIG ──────────────────────────────────────────────────────────────────

BASE_URL         = "https://api.wormholescan.io/api/v1/ntt/token"
INPUT_FILE       = Path(__file__).parent / "use/ntt_eth_sol_pairs.json"
CONTRACTS_FILE   = Path(__file__).parent / "use/ntt_all_contracts.json"
OUTPUT_FILE      = Path(__file__).parent / "use/ntt_eth_sol_pairs_full.json"

SLEEP_SEC   = 0.5
MAX_RETRIES = 5

# ─── HTTP ─────────────────────────────────────────────────────────────────────

session = requests.Session()
session.headers.update({"Accept": "application/json"})


def api_get(chain_id, token_address):
    url = f"{BASE_URL}/{chain_id}/{token_address}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                wait = 10 * attempt
                print(f"\n  429 限速，等待 {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == MAX_RETRIES:
                print(f"\n  请求失败: {url} - {e}")
                return None
            time.sleep(min(2 ** attempt, 30))


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def b58encode(v: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    long_value = int.from_bytes(v, "big")
    result = ""
    while long_value >= 58:
        long_value, mod = divmod(long_value, 58)
        result = alphabet[mod] + result
    result = alphabet[long_value] + result
    pad = len(v) - len(v.lstrip(b"\x00"))
    return alphabet[0] * pad + result


def hex_to_sol_address(hex_str: str) -> str:
    """将 32-byte hex 转为 Solana base58 地址"""
    hex_str = hex_str.lstrip("0x")
    if not hex_str or len(hex_str) != 64:
        return None
    try:
        return b58encode(bytes.fromhex(hex_str))
    except Exception:
        return None


def find_sol_peer(peers):
    for peer in peers:
        if peer.get("wormholeChainId") == 1 or peer.get("blockchain") == "Solana":
            return peer
    return None


def find_eth_peer(peers):
    for peer in peers:
        if peer.get("wormholeChainId") == 2 or peer.get("blockchain") == "Ethereum":
            return peer
    return None


# ─── BUILD RECORD ─────────────────────────────────────────────────────────────

def build_record(pair, home_data, home_chain, sol_data, eth_data):
    """
    home_chain: 'eth' or 'sol'
    home_data:  API response where this token is home
    sol_data:   peer/home dict for Solana side
    eth_data:   peer/home dict for Ethereum side
    """
    eth_token   = (eth_data or {}).get("token", {})
    eth_manager = (eth_data or {}).get("manager", {})
    sol_token   = (sol_data or {}).get("token", {})
    sol_manager = (sol_data or {}).get("manager", {})

    return {
        "symbol":     eth_token.get("symbol") or sol_token.get("symbol") or pair.get("symbol"),
        "name":       eth_token.get("name")   or sol_token.get("name"),
        "home_chain": home_chain,

        "eth": {
            "wormhole_chain_id":  2,
            "token_address":      eth_token.get("address"),
            "token_name":         eth_token.get("name"),
            "token_symbol":       eth_token.get("symbol"),
            "decimals":           eth_token.get("decimals"),
            "total_supply":       eth_token.get("totalSupply"),
            "mode":               eth_data.get("mode") if eth_data else None,
            "is_canonical":       eth_data.get("isCanonical") if eth_data else None,
            "ntt_manager":        eth_manager.get("address"),
            "manager_version":    eth_manager.get("version"),
            "transceivers":       eth_manager.get("transceivers", []),
            "limits":             eth_manager.get("limits", []),
            "last_indexed_block": eth_data.get("lastIndexed") if eth_data else None,
        } if eth_data else None,

        "sol": {
            "wormhole_chain_id":  1,
            "token_address":      sol_token.get("address"),
            "token_name":         sol_token.get("name"),
            "token_symbol":       sol_token.get("symbol"),
            "decimals":           sol_token.get("decimals"),
            "total_supply":       sol_token.get("totalSupply"),
            "max_supply":         sol_token.get("maxSupply"),
            "minter":             sol_token.get("minter"),
            "mode":               sol_data.get("mode") if sol_data else None,
            "is_canonical":       sol_data.get("isCanonical") if sol_data else None,
            "ntt_manager":        sol_manager.get("address"),
            "manager_version":    sol_manager.get("version"),
            "transceivers":       sol_manager.get("transceivers", []),
            "limits":             sol_manager.get("limits", []),
            "last_indexed_block": sol_data.get("lastIndexed") if sol_data else None,
        } if sol_data else None,

        "_original": pair,
    }


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Wormhole NTT  ETH <-> SOL  Token Pair 详细信息采集")
    print("=" * 60)

    with open(INPUT_FILE, encoding="utf-8") as f:
        data = json.load(f)
    with open(CONTRACTS_FILE, encoding="utf-8") as f:
        contracts = json.load(f)

    pairs         = data["pairs"]
    sol_contracts = contracts["by_chain"].get("Solana", [])

    # 建立 sol ntt_manager_hex(suffix) -> sol token address 映射
    # pair 里的 sol_ntt_manager_hex 只是 32字节 manager 地址的后16字节
    sol_manager_suffix_to_token = {}
    for c in sol_contracts:
        mgr_hex = c.get("ntt_manager", "").lstrip("0x")
        src_hex = c.get("source_token", "").lstrip("0x")
        if src_hex:
            sol_addr = hex_to_sol_address(src_hex)
            if sol_addr:
                # 同时索引 full hex 和后32字符(16字节) suffix
                sol_manager_suffix_to_token[mgr_hex] = sol_addr
                if len(mgr_hex) > 32:
                    sol_manager_suffix_to_token[mgr_hex[-32:]] = sol_addr

    print(f"输入: {len(pairs)} 个 ETH<->SOL NTT pair")
    print("-" * 60)

    results = []

    def find_sol_token_addr(sol_mgr_hex):
        """通过 sol ntt manager hex (suffix) 找 Solana token address"""
        # 直接匹配
        addr = sol_manager_suffix_to_token.get(sol_mgr_hex)
        if addr:
            return addr
        # suffix match
        for mgr_full, a in sol_manager_suffix_to_token.items():
            if mgr_full.endswith(sol_mgr_hex) or sol_mgr_hex in mgr_full:
                return a
        return None

    for i, pair in enumerate(pairs):
        eth_addr    = pair.get("eth_token_address", "")
        symbol      = pair.get("symbol", "?")
        sol_mgr_hex = pair.get("sol_ntt_manager_hex", "").lstrip("0x")

        print(f"  [{i+1}/{len(pairs)}] {symbol}", end="\r")

        # 查 ETH 侧
        eth_api = api_get(2, eth_addr)
        time.sleep(SLEEP_SEC)
        eth_home = eth_api.get("home", {}) if eth_api else None

        # 查 SOL 侧（通过 sol manager suffix 找 token address）
        sol_token_addr = find_sol_token_addr(sol_mgr_hex)
        sol_api  = api_get(1, sol_token_addr) if sol_token_addr else None
        if sol_api:
            time.sleep(SLEEP_SEC)
        sol_home = sol_api.get("home", {}) if sol_api else None

        # 从 peers 里补充另一侧（如果 API 有的话）
        if eth_api and not sol_home:
            sol_peer = find_sol_peer(eth_api.get("peers", []))
            if sol_peer:
                sol_home = sol_peer
        if sol_api and not eth_home:
            eth_peer = find_eth_peer(sol_api.get("peers", []))
            if eth_peer:
                eth_home = eth_peer

        # 判断结果
        if eth_home and sol_home:
            home_chain = "eth" if eth_api and eth_api.get("home", {}).get("isCanonical") else "sol"
            record = build_record(pair, None, home_chain, sol_home, eth_home)
            print(f"  [{i+1}/{len(pairs)}] {symbol} -> ✓ (eth={'yes' if eth_api else 'peer'} sol={'yes' if sol_api else 'peer'})")
            results.append(record)
        elif eth_home:
            record = build_record(pair, None, "eth", None, eth_home)
            record["_note"] = "sol_side_missing"
            print(f"  [{i+1}/{len(pairs)}] {symbol} -> ETH only")
            results.append(record)
        elif sol_home:
            record = build_record(pair, None, "sol", sol_home, None)
            record["_note"] = "eth_side_missing"
            print(f"  [{i+1}/{len(pairs)}] {symbol} -> SOL only")
            results.append(record)
        else:
            print(f"  [{i+1}/{len(pairs)}] {symbol} -> 无数据")
            results.append({**pair, "api_status": "not_found", "eth": None, "sol": None})

    ok     = sum(1 for r in results if r.get("sol") and r.get("eth"))
    partial = sum(1 for r in results if r.get("_note"))
    failed = sum(1 for r in results if r.get("api_status") == "not_found")

    print(f"\n完成: 双侧完整 {ok} | partial {partial} | 失败 {failed} / 总计 {len(results)}")

    output = {
        "meta": {
            "fetched_at":  datetime.now(timezone.utc).isoformat(),
            "source":      str(INPUT_FILE),
            "total_pairs": len(results),
            "both_sides":  ok,
            "partial":     partial,
            "failed":      failed,
        },
        "pairs": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"JSON 已保存: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
