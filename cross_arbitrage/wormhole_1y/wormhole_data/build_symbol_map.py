#!/usr/bin/env python3
"""
扫 matched_context_parsed.json 的 ETH erc20_transfer events, 建 symbol->address 映射。
SOL 侧已知 symbol 少, 硬编码。

输出:
  prices/symbol_map.json
    {
      "eth": {"USDC": "0xA0b8...", ...},
      "sol": {"USDC": "EPjFW...", ...}
    }
"""

import json
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
PARSED = ROOT / "matched_context_parsed.json"
OUT = ROOT / "arbitrage" / "prices"
OUT.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT / "symbol_map.json"

# SOL 侧硬编码 (Wormhole wrapped 版本)
SOL_HARDCODE = {
    "USDC": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "USDT": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "WETH": "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs",  # Wormhole wETH
    "WBTC": "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh",  # Wormhole wBTC
    "SOL":  "So11111111111111111111111111111111111111112",
    "WSOL": "So11111111111111111111111111111111111111112",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
}

ETH_HARDCODE = {
    "ETH":  "0x0000000000000000000000000000000000000000",
    "WETH": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
    "USDC": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "USDT": "0xdac17f958d2ee523a2206206994597c13d831ec7",
    "WBTC": "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
    "DAI":  "0x6b175474e89094c44da98b954eedeac495271d0f",
}


def main():
    print(f"加载 {PARSED.name}...")
    data = json.load(open(PARSED, encoding="utf-8"))
    recs = data["records"] if isinstance(data, dict) and "records" in data else data
    print(f"  {len(recs)} 条记录")

    # ETH: 从 erc20_transfer events 提取 (symbol, address)
    eth_sym2addr = defaultdict(Counter)
    for r in recs:
        for ctx in (r.get("eth_context") or []):
            p = ctx.get("parsed")
            if not p or p.get("error"):
                continue
            for e in p.get("events") or []:
                if e.get("type") != "erc20_transfer":
                    continue
                tok = e.get("token")
                addr = (e.get("contract") or e.get("address") or e.get("token_address") or "").lower()
                if tok and addr and addr.startswith("0x"):
                    eth_sym2addr[tok][addr] += 1

    # 选每个 symbol 出现最多的 address
    eth_map = dict(ETH_HARDCODE)
    conflicts = 0
    for sym, addrs in eth_sym2addr.items():
        # 如果 symbol 本身就是 0x 地址, 直接存
        if sym.startswith("0x") and len(sym) == 42:
            eth_map.setdefault(sym.lower(), sym.lower())
            continue
        top = addrs.most_common(1)[0]
        eth_map[sym] = top[0]
        if len(addrs) > 1:
            conflicts += 1

    # SOL: 硬编码 (summary 里 SOL 只用 USDC/WETH/WBTC)
    sol_map = dict(SOL_HARDCODE)

    out = {"eth": eth_map, "sol": sol_map}
    json.dump(out, open(OUT_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

    print(f"\nETH symbol 映射: {len(eth_map)} (其中 {conflicts} 个 symbol 有多个候选 addr, 取出现最多的)")
    print(f"SOL symbol 映射: {len(sol_map)}")
    print(f"写入: {OUT_FILE}")


if __name__ == "__main__":
    main()
