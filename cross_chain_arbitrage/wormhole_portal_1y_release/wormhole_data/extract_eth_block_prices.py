#!/usr/bin/env python3
"""
从候选里 ETH 侧 swap 的 sold/bought 比值直接提取 block-level 价格。
  - 任一 ETH swap 如果 sold∪bought 里同时有 stable 和非-stable
    → 非-stable 价 = stable_amount / non_stable_amount
  - 适用于 WETH, 也适用于其他 ETH token (如 POKT, MOCA...)
  - ts 精度 = ETH block (~12s), 比 Birdeye 1m 更细
输出: prices/eth_block_exact.json
  {"ethereum:addr": {"<ts>": price}}
多笔同 ts 取平均。
"""

import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
CANDIDATES = ROOT / "arbitrage_candidates_1pct.json"
OUT = ROOT / "prices" / "eth_block_exact.json"

STABLE_ADDRS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
}
WETH_ADDR = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


def addr_of(tok, symap):
    if not tok:
        return None
    if tok == "ETH":
        return WETH_ADDR
    if isinstance(tok, str) and tok.startswith("0x") and len(tok) == 42:
        return tok.lower()
    a = symap["eth"].get(tok)
    return a.lower() if a else None


def main():
    cands = json.load(open(CANDIDATES, encoding="utf-8"))["candidates"]
    symap = json.load(open(ROOT / "prices" / "symbol_map.json"))

    # {token_addr: {ts: [price, price, ...]}}
    prices = defaultdict(lambda: defaultdict(list))

    n_swaps = 0
    n_extracted = 0
    for c in cands:
        for field in ["entry_swaps", "exit_swaps", "entry_swaps_greedy", "exit_swaps_greedy"]:
            for s in c.get(field) or []:
                if s.get("chain") != "ETH":
                    continue
                ts = s.get("ts")
                if not ts:
                    continue
                n_swaps += 1
                all_items = []
                for it in (s.get("sold") or []):
                    a = addr_of(it.get("token"), symap)
                    amt = it.get("amount")
                    if a and isinstance(amt, (int, float)) and amt > 0:
                        all_items.append((a, amt))
                for it in (s.get("bought") or []):
                    a = addr_of(it.get("token"), symap)
                    amt = it.get("amount")
                    if a and isinstance(amt, (int, float)) and amt > 0:
                        all_items.append((a, amt))
                # 找 stable 和 非-stable
                stables = [(a, x) for a, x in all_items if a in STABLE_ADDRS]
                non_stables = [(a, x) for a, x in all_items if a not in STABLE_ADDRS]
                if not stables or not non_stables:
                    continue
                # 聚合 stable USD (相加, 因为 USDC ≈ USDT ≈ DAI ≈ $1)
                stable_usd = sum(x for _, x in stables)
                # 对每个非-stable 计算价
                for a, amt in non_stables:
                    # 简单均分: 如果 swap 有多个非-stable, 无法精确分配 — 用比例粗估
                    price = stable_usd / amt if amt > 0 else 0
                    if price > 0 and price < 1e10:  # 排除异常
                        prices[a][ts].append(price)
                        n_extracted += 1

    # 每个 (token, ts) 取中位数
    out = {}
    for addr, ts_map in prices.items():
        key = f"ethereum:{addr}"
        out[key] = {}
        for ts, plist in ts_map.items():
            plist.sort()
            med = plist[len(plist) // 2]
            out[key][str(ts)] = med

    OUT.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    total_pts = sum(len(v) for v in out.values())
    print(f"ETH swaps 扫描: {n_swaps}, 提取出价: {n_extracted}")
    print(f"写入 {OUT.name}: {len(out)} token, {total_pts} 条 (ts, price)")

    # 检查 WETH 覆盖
    weth_key = f"ethereum:{WETH_ADDR}"
    print(f"\nWETH 覆盖: {len(out.get(weth_key, {}))} 个 ts")

    # 每个 token 有多少 ts
    top = sorted(out.items(), key=lambda kv: -len(kv[1]))[:15]
    print("Top 15 token by ts 数:")
    for k, v in top:
        print(f"  {k}: {len(v)}")


if __name__ == "__main__":
    main()
