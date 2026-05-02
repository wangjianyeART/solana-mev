#!/usr/bin/env python3
"""
解析 v2_v3_diff_review.json 中所有 src_tx_hash / dst_tx_hash，
提取 ERC20 合约地址和 SPL mint 地址。

用法:
    python wormhole_data/resolve_tx_tokens.py
"""

import json
import time
import requests
from pathlib import Path

DIR = Path(__file__).parent / "use"
INPUT = DIR / "parsed" / "token_match" / "v2_v3_diff_review.json"
OUTPUT = DIR / "parsed" / "token_match" / "v2_v3_diff_with_addresses.json"

CHAINSTACK_ETH = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"
CHAINSTACK_SOL = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# ETH token symbol 缓存
ETH_TOKEN_CACHE = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
}

# SOL mint symbol 缓存
SOL_MINT_CACHE = {}


def load_mint_cache():
    cache_path = DIR / "cache" / "mint_cache.json"
    if cache_path.exists():
        with open(cache_path) as f:
            raw = json.load(f)
        for k, v in raw.items():
            SOL_MINT_CACHE[k] = v


def eth_rpc(method, params):
    for attempt in range(3):
        try:
            resp = requests.post(CHAINSTACK_ETH, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                time.sleep(1)
                continue
            return data.get("result")
        except Exception:
            time.sleep(1)
    return None


def sol_rpc(method, params):
    for attempt in range(3):
        try:
            resp = requests.post(CHAINSTACK_SOL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                time.sleep(1)
                continue
            return data.get("result")
        except Exception:
            time.sleep(1)
    return None


def get_eth_symbol(contract_addr):
    addr = contract_addr.lower()
    if addr in ETH_TOKEN_CACHE:
        return ETH_TOKEN_CACHE[addr]
    try:
        result = eth_rpc("eth_call", [{"to": addr, "data": "0x95d89b41"}, "latest"])
        if result and len(result) > 66:
            sym = bytes.fromhex(result[130:]).decode("utf-8").rstrip("\x00").strip()
            if sym and sym.isprintable():
                ETH_TOKEN_CACHE[addr] = sym
                return sym
    except Exception:
        pass
    ETH_TOKEN_CACHE[addr] = "?"
    return "?"


def parse_eth_tx(tx_hash):
    """解析 ETH 交易，返回 ERC20 token transfers"""
    receipt = eth_rpc("eth_getTransactionReceipt", [tx_hash])
    if not receipt:
        return None

    transfers = []
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics or topics[0] != TRANSFER_TOPIC or len(topics) != 3:
            continue
        contract = log["address"].lower()
        frm = "0x" + topics[1][-40:]
        to = "0x" + topics[2][-40:]
        raw = int(log.get("data", "0x0"), 16)
        sym = get_eth_symbol(contract)

        transfers.append({
            "erc20_address": contract,
            "symbol": sym,
            "from": frm,
            "to": to,
            "raw_amount": str(raw),
        })

    return transfers


def parse_sol_tx(sig):
    """解析 SOL 交易，返回 SPL token changes"""
    result = sol_rpc("getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
    }])
    if not result:
        return None

    meta = result.get("meta", {})
    pre_map = {}
    for b in meta.get("preTokenBalances", []):
        key = (b.get("accountIndex"), b.get("mint"))
        pre_map[key] = float(b.get("uiTokenAmount", {}).get("uiAmountString", "0") or "0")

    changes = []
    for b in meta.get("postTokenBalances", []):
        key = (b.get("accountIndex"), b.get("mint"))
        post_amt = float(b.get("uiTokenAmount", {}).get("uiAmountString", "0") or "0")
        pre_amt = pre_map.get(key, 0)
        change = post_amt - pre_amt
        if abs(change) > 0.000001:
            mint = b.get("mint", "")
            sym = SOL_MINT_CACHE.get(mint, mint[:12] + "...")
            changes.append({
                "spl_mint": mint,
                "symbol": sym,
                "owner": b.get("owner", ""),
                "change": round(change, 6),
            })

    return changes


def main():
    load_mint_cache()
    print(f"SOL mint cache: {len(SOL_MINT_CACHE)} 个")

    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    total = data["v2_only"]["count"] + data["v3_only"]["count"]
    done = 0
    errors = 0

    for group in ["v2_only", "v3_only"]:
        for r in data[group]["records"]:
            src_hash = r.get("src_tx_hash", "")
            dst_hash = r.get("dst_tx_hash", "")

            # 解析 src
            if src_hash:
                if src_hash.startswith("0x"):
                    r["src_erc20_transfers"] = parse_eth_tx(src_hash)
                else:
                    r["src_spl_changes"] = parse_sol_tx(src_hash)

            # 解析 dst
            if dst_hash:
                if dst_hash.startswith("0x"):
                    r["dst_erc20_transfers"] = parse_eth_tx(dst_hash)
                else:
                    r["dst_spl_changes"] = parse_sol_tx(dst_hash)

            # 汇总 token 地址
            erc20_addr = None
            spl_mint = None

            if r.get("src_erc20_transfers"):
                # 找金额最大的 transfer
                best = max(r["src_erc20_transfers"], key=lambda x: int(x["raw_amount"]))
                erc20_addr = best["erc20_address"]
            if r.get("dst_erc20_transfers"):
                best = max(r["dst_erc20_transfers"], key=lambda x: int(x["raw_amount"]))
                erc20_addr = best["erc20_address"]

            if r.get("src_spl_changes"):
                best = max(r["src_spl_changes"], key=lambda x: abs(x["change"]))
                spl_mint = best["spl_mint"]
            if r.get("dst_spl_changes"):
                best = max(r["dst_spl_changes"], key=lambda x: abs(x["change"]))
                spl_mint = best["spl_mint"]

            r["resolved_addresses"] = {
                "erc20_address": erc20_addr,
                "spl_mint": spl_mint,
                "erc20_symbol": ETH_TOKEN_CACHE.get(erc20_addr, "?") if erc20_addr else None,
                "spl_symbol": SOL_MINT_CACHE.get(spl_mint, "?") if spl_mint else None,
            }

            done += 1
            if done % 20 == 0:
                print(f"  [{done}/{total}] 已解析  errors={errors}", flush=True)

            time.sleep(0.3)  # 避免限速

    # 保存
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    sz = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"  总记录: {total}")
    print(f"  已解析: {done}")
    print(f"  保存: {OUTPUT.name} ({sz:.1f} MB)")

    # 汇总所有解析出的 token 地址对
    pairs = {}
    for group in ["v2_only", "v3_only"]:
        for r in data[group]["records"]:
            ra = r.get("resolved_addresses", {})
            sym = r.get("token_symbol", "?")
            erc20 = ra.get("erc20_address")
            spl = ra.get("spl_mint")
            if erc20 and spl:
                if sym not in pairs:
                    pairs[sym] = {"erc20": erc20, "spl": spl,
                                  "erc20_sym": ra.get("erc20_symbol"),
                                  "spl_sym": ra.get("spl_symbol")}

    print(f"\n  解析出的 ERC20 ↔ SPL 地址对 ({len(pairs)} 个):")
    for sym, p in sorted(pairs.items()):
        print(f"    {sym:<25s} ERC20={p['erc20']}  SPL={p['spl'][:30]}...")


if __name__ == "__main__":
    main()
