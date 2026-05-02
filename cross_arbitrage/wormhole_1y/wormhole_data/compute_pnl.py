#!/usr/bin/env python3
"""
计算每条候选的 PnL。

对每条 candidate:
  entry_cost_usd  = Σ over entry_swaps_greedy: quote_amount × quote_usd_at_ts
  exit_value_usd  = Σ over exit_swaps_greedy:  quote_amount × quote_usd_at_ts
  gas_cost_usd    = sol_fee_sol × SOL_usd + eth_fee_eth × ETH_usd
  gross_pnl       = exit_value_usd - entry_cost_usd
  net_pnl         = gross_pnl - gas_cost_usd
  roi_pct         = net_pnl / entry_cost_usd * 100

价格查找优先级:
  1. 稳定币 → $1
  2. SOL 链 → exact_sol.json (1s 精度)
  3. ETH 链 → series_eth_1m.json (1m 精度, 找最近的分钟)
  4. fallback → birdeye_cache.json (1H)

写回 candidate 的 pnl 字段, 并在 meta 加统计。
"""

import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
PRICES_DIR = ROOT / "prices"

SYMBOL_MAP = PRICES_DIR / "symbol_map.json"
SOL_EXACT = PRICES_DIR / "exact_sol.json"
ETH_BLOCK = PRICES_DIR / "eth_block_exact.json"
ETH_1M = PRICES_DIR / "series_eth_1m.json"
H1_CACHE = PRICES_DIR / "birdeye_cache.json"

# 每个阈值文件都跑
CAND_FILES = sorted(ROOT.glob("arbitrage_candidates_*pct.json"))

STABLES = {
    "solana:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "solana:Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7",
    "ethereum:0x6b175474e89094c44da98b954eedeac495271d0f",
}

SOL_NATIVE = "solana:So11111111111111111111111111111111111111112"
ETH_NATIVE = "ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"  # WETH, 跟 ETH 同价

EXACT_TOL_SEC = 3600    # SOL exact: 命中不到 ts 时可接受的最大偏差 (fallback)
BLOCK_TOL_SEC = 60      # ETH block: swap-ratio 价, block-level, 仅接受 1min 内
MIN_TOL_SEC = 900       # ETH 1m: 找最近分钟, 最大偏差 15min
H1_TOL_SEC = 7200       # fallback: 1H cache, 最大 2h


def is_eth_addr(s):
    return isinstance(s, str) and s.startswith("0x") and len(s) == 42

def is_sol_addr(s):
    return isinstance(s, str) and 32 <= len(s) <= 44 and not s.startswith("0x") and s.isalnum()


def resolve_token(chain, tok, symap):
    if not tok:
        return None
    if chain == "ETH":
        if tok == "ETH":
            return ETH_NATIVE
        if is_eth_addr(tok):
            return f"ethereum:{tok.lower()}"
        addr = symap["eth"].get(tok)
        return f"ethereum:{addr.lower()}" if addr else None
    elif chain == "SOL":
        if tok in ("SOL", "WSOL"):
            return SOL_NATIVE
        if is_sol_addr(tok):
            return f"solana:{tok}"
        addr = symap["sol"].get(tok)
        return f"solana:{addr}" if addr else None
    return None


def _nearest(series, ts, tol):
    if not series:
        return None, None
    target = str(ts)
    if target in series:
        return series[target], 0
    best_k = None
    best_d = None
    ts_i = int(ts)
    for k in series.keys():
        d = abs(int(k) - ts_i)
        if best_d is None or d < best_d:
            best_d = d
            best_k = k
    if best_d is not None and best_d <= tol:
        return series[best_k], best_d
    return None, best_d


class PriceLookup:
    def __init__(self):
        self.sol_exact = json.load(open(SOL_EXACT, encoding="utf-8")) if SOL_EXACT.exists() else {}
        self.eth_block = json.load(open(ETH_BLOCK, encoding="utf-8")) if ETH_BLOCK.exists() else {}
        self.eth_1m = json.load(open(ETH_1M, encoding="utf-8")) if ETH_1M.exists() else {}
        self.h1 = json.load(open(H1_CACHE, encoding="utf-8")) if H1_CACHE.exists() else {}

    def get(self, key, ts):
        """返回 (price, source, offset_sec) 或 (None, None, None)"""
        if not key or ts is None:
            return None, None, None
        if key in STABLES:
            return 1.0, "stable", 0

        chain = key.split(":", 1)[0]

        # 第一梯队: 最精确源
        if chain == "solana" and key in self.sol_exact:
            p, d = _nearest(self.sol_exact[key], ts, EXACT_TOL_SEC)
            if p is not None:
                return p, "sol_exact", d
        elif chain == "ethereum":
            # block-level (由 swap ratio 提取, ~12s 精度)
            if key in self.eth_block:
                p, d = _nearest(self.eth_block[key], ts, BLOCK_TOL_SEC)
                if p is not None:
                    return p, "eth_block", d
            # 1m 序列
            if key in self.eth_1m:
                p, d = _nearest(self.eth_1m[key], ts, MIN_TOL_SEC)
                if p is not None:
                    return p, "eth_1m", d

        # 第二梯队: 1H cache fallback
        if key in self.h1 and self.h1[key]:
            p, d = _nearest(self.h1[key], ts, H1_TOL_SEC)
            if p is not None:
                return p, "h1_cache", d

        return None, None, None


def bridge_key(candidate, chain):
    br = candidate.get("bridge") or {}
    if chain == "SOL" and br.get("spl_mint"):
        return f"solana:{br['spl_mint']}"
    if chain == "ETH" and br.get("erc20_contract"):
        return f"ethereum:{br['erc20_contract'].lower()}"
    return None


def compute_swap_usd(swap, side, candidate, prices, symap):
    """
    side: 'entry' (quote = sold) 或 'exit' (quote = bought)
    返回 (usd_value, errors, pricing_trace)
    """
    chain = swap.get("chain")
    ts = swap.get("ts")
    bkey = bridge_key(candidate, chain)
    quote_items = swap.get("sold") if side == "entry" else swap.get("bought")
    if not quote_items:
        return None, ["no_quote_side"], []

    total = 0.0
    errors = []
    trace = []
    for item in quote_items:
        tok = item.get("token")
        amount = item.get("amount") or 0
        key = resolve_token(chain, tok, symap)
        if not key:
            errors.append(f"unresolved:{tok}")
            continue
        # 如果 quote == bridge_token (极端对倒), 跳过
        if bkey and key == bkey:
            continue
        price, src, off = prices.get(key, ts)
        if price is None:
            errors.append(f"no_price:{key}")
            continue
        total += amount * price
        trace.append({"token": key, "amount": amount, "price": price, "source": src, "offset_sec": off})

    if errors and total == 0:
        return None, errors, trace
    return total, errors, trace


def compute_pnl(c, prices, symap):
    entry_cost = 0.0
    entry_errs = []
    entry_trace = []
    for s in c.get("entry_swaps_greedy") or []:
        usd, errs, tr = compute_swap_usd(s, "entry", c, prices, symap)
        if usd is not None:
            entry_cost += usd
        entry_errs.extend(errs)
        entry_trace.extend(tr)

    exit_value = 0.0
    exit_errs = []
    exit_trace = []
    for s in c.get("exit_swaps_greedy") or []:
        usd, errs, tr = compute_swap_usd(s, "exit", c, prices, symap)
        if usd is not None:
            exit_value += usd
        exit_errs.extend(errs)
        exit_trace.extend(tr)

    # Gas
    sol_fee = c.get("sol_fee_sol") or 0
    eth_fee = c.get("eth_fee_eth") or 0
    sol_ts = c.get("sol_ts")
    eth_ts = c.get("eth_ts")

    sol_px, sol_src, _ = prices.get(SOL_NATIVE, sol_ts) if sol_ts else (None, None, None)
    eth_px, eth_src, _ = prices.get(ETH_NATIVE, eth_ts) if eth_ts else (None, None, None)
    sol_fee_usd = sol_fee * sol_px if sol_px else 0.0
    eth_fee_usd = eth_fee * eth_px if eth_px else 0.0
    total_fee = sol_fee_usd + eth_fee_usd

    has_entry = entry_cost > 0 and not entry_errs
    has_exit = exit_value > 0 and not exit_errs
    complete = has_entry and has_exit

    gross = exit_value - entry_cost if complete else None
    net = (gross - total_fee) if complete else None
    roi = (net / entry_cost * 100) if (complete and entry_cost > 0) else None

    return {
        "entry_cost_usd": round(entry_cost, 6) if entry_cost else None,
        "exit_value_usd": round(exit_value, 6) if exit_value else None,
        "sol_fee_usd": round(sol_fee_usd, 6),
        "eth_fee_usd": round(eth_fee_usd, 6),
        "total_fee_usd": round(total_fee, 6),
        "gross_pnl_usd": round(gross, 6) if gross is not None else None,
        "net_pnl_usd": round(net, 6) if net is not None else None,
        "roi_pct": round(roi, 4) if roi is not None else None,
        "complete": complete,
        "errors": list(set(entry_errs + exit_errs)) or None,
        "gas_price_source": {"sol": sol_src, "eth": eth_src},
        "pricing_trace": entry_trace + exit_trace,
    }


def process_file(path, prices, symap):
    print(f"\n处理 {path.name}")
    data = json.load(open(path, encoding="utf-8"))
    cands = data.get("candidates", [])

    source_stats = defaultdict(int)
    offset_stats = []
    complete_n = 0
    by_reliability = defaultdict(lambda: {"n": 0, "complete": 0, "net_pnl_sum": 0.0})

    for c in cands:
        pnl = compute_pnl(c, prices, symap)
        c["pnl"] = pnl
        if pnl["complete"]:
            complete_n += 1
        rel = c.get("pnl_reliability_greedy") or c.get("pnl_reliability") or "unknown"
        by_reliability[rel]["n"] += 1
        if pnl["complete"]:
            by_reliability[rel]["complete"] += 1
            by_reliability[rel]["net_pnl_sum"] += pnl["net_pnl_usd"] or 0
        for tr in pnl["pricing_trace"]:
            source_stats[tr.get("source") or "none"] += 1
            if tr.get("offset_sec") is not None:
                offset_stats.append(tr["offset_sec"])

    # 摘要
    completes = [c["pnl"] for c in cands if c["pnl"]["complete"]]
    net_pnls = sorted([c["net_pnl_usd"] for c in completes], key=lambda x: x)

    def pct(xs, p):
        if not xs: return None
        i = int(p * (len(xs) - 1))
        return xs[i]

    summary = {
        "candidates": len(cands),
        "pnl_complete": complete_n,
        "price_source_counts": dict(source_stats),
        "offset_avg_sec": round(sum(offset_stats) / len(offset_stats), 2) if offset_stats else None,
        "offset_max_sec": max(offset_stats) if offset_stats else None,
        "net_pnl_distribution": {
            "p01": pct(net_pnls, 0.01),
            "p10": pct(net_pnls, 0.10),
            "p25": pct(net_pnls, 0.25),
            "median": pct(net_pnls, 0.50),
            "p75": pct(net_pnls, 0.75),
            "p90": pct(net_pnls, 0.90),
            "p99": pct(net_pnls, 0.99),
            "min": net_pnls[0] if net_pnls else None,
            "max": net_pnls[-1] if net_pnls else None,
            "sum": round(sum(net_pnls), 2),
            "positive_n": sum(1 for x in net_pnls if x > 0),
            "negative_n": sum(1 for x in net_pnls if x < 0),
        },
        "by_reliability": {
            k: {
                "n": v["n"],
                "complete": v["complete"],
                "net_pnl_sum": round(v["net_pnl_sum"], 2),
                "net_pnl_avg": round(v["net_pnl_sum"] / v["complete"], 2) if v["complete"] else None,
            }
            for k, v in by_reliability.items()
        },
    }
    data.setdefault("meta", {})["pnl_summary"] = summary

    # pricing_trace 占空间, 写回时去掉 (保留 source+offset 外层已够)
    for c in cands:
        if "pricing_trace" in c.get("pnl", {}):
            del c["pnl"]["pricing_trace"]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  总计 {len(cands)}, complete PnL: {complete_n}")
    print(f"  price sources: {dict(source_stats)}")
    print(f"  offset avg: {summary['offset_avg_sec']}s, max: {summary['offset_max_sec']}s")
    print(f"  net PnL: sum={summary['net_pnl_distribution']['sum']}, "
          f"median={summary['net_pnl_distribution']['median']}, "
          f"positive={summary['net_pnl_distribution']['positive_n']}, "
          f"negative={summary['net_pnl_distribution']['negative_n']}")
    print(f"  by reliability:")
    for k, v in summary["by_reliability"].items():
        print(f"    {k}: n={v['n']}, complete={v['complete']}, Σnet={v['net_pnl_sum']}, avg={v['net_pnl_avg']}")


def main():
    symap = json.load(open(SYMBOL_MAP, encoding="utf-8"))
    prices = PriceLookup()
    print(f"Price caches: sol_exact={len(prices.sol_exact)}, eth_block={len(prices.eth_block)}, eth_1m={len(prices.eth_1m)}, h1={len(prices.h1)}")
    for f in CAND_FILES:
        process_file(f, prices, symap)
    print("\n完成")


if __name__ == "__main__":
    main()
