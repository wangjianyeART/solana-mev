#!/usr/bin/env python3
"""
1y 版 PnL 计算。

价格查找优先级 (严格 ts 精度, 无 1H fallback):
  1. 稳定币 → $1
  2. SOL 链 → exact_sol.json (1s 精度)
  3. ETH 链 → eth_block_exact.json (block 精度) → series_eth_1m.json (1m 精度)

写回 candidate 的 pnl 字段, 并在 meta 加统计。
"""

import hashlib
import json
import os
import time
from bisect import bisect_left
from collections import OrderedDict, defaultdict
from pathlib import Path

RECENT = Path(__file__).parent / "use" / "portal_full" / "recent_1y"
RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
ROOT = RECENT / (f"matched_{RUN_TAG}" if RUN_TAG else "matched") / "arbitrage"
BASE_ROOT = RECENT / "matched" / "arbitrage"
PRICES_DIR = ROOT / "prices" if (ROOT / "prices").exists() else BASE_ROOT / "prices"

SYMBOL_MAP = PRICES_DIR / "symbol_map.json"
SOL_EXACT = PRICES_DIR / "exact_sol.json"
ETH_BLOCK = PRICES_DIR / "eth_block_exact.json"
ETH_1M = PRICES_DIR / "series_eth_1m.json"
ETH_1M_SHARDS = PRICES_DIR / "eth_1m_shards"
ETH_1M_INDEX = ETH_1M_SHARDS / "_index.json"

CAND_FILES = sorted(ROOT.glob("arbitrage_candidates_*pct*.json"))
CAND_FILES = [f for f in CAND_FILES if not f.name.endswith(".bak")]

STABLES = {
    "solana:EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "solana:Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "ethereum:0xdac17f958d2ee523a2206206994597c13d831ec7",
    "ethereum:0x6b175474e89094c44da98b954eedeac495271d0f",
}

SOL_NATIVE = "solana:So11111111111111111111111111111111111111112"
ETH_NATIVE = "ethereum:0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"

EXACT_TOL_SEC = 3600
BLOCK_TOL_SEC = 60
MIN_TOL_SEC = 900


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


def _nearest_sorted(sorted_ts, values, ts, tol):
    """二分: sorted_ts 是升序 int 列表, values 对齐."""
    if not sorted_ts:
        return None, None
    ts_i = int(ts)
    i = bisect_left(sorted_ts, ts_i)
    candidates = []
    if i < len(sorted_ts):
        candidates.append(i)
    if i > 0:
        candidates.append(i - 1)
    best_k = None
    best_d = None
    for ci in candidates:
        d = abs(sorted_ts[ci] - ts_i)
        if best_d is None or d < best_d:
            best_d = d
            best_k = ci
    if best_d is not None and best_d <= tol:
        return values[best_k], best_d
    return None, best_d


def _index_series(series_dict):
    """把 {ts_str: val} → (sorted_int_ts, aligned_values)."""
    if not series_dict:
        return [], []
    pairs = sorted(((int(k), v) for k, v in series_dict.items()), key=lambda x: x[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


class PriceLookup:
    """Lazy-loading price lookup.

    - sol_exact (3.6MB) 整块加载。
    - eth_block 整块加载 (当前 0 tokens, 可忽略)。
    - eth_1m 分片加载 (804MB → 165 个分片, 单个 ≤ 20MB),
      首次查询某 token 时从 eth_1m_shards/ 读该分片。
      用 LRU 限制驻留 shard 数, 内存峰值可控。
    """

    ETH_1M_LRU_MAX = 32  # 同时驻留的 ETH 1m 分片上限; 单 shard 最大 19MB raw,
                          # parsed ~5x; 32 个分片峰值 ~1.5GB 足够缓存覆盖热门 token,
                          # 避免在 30k 候选迭代中频繁换页.

    def __init__(self):
        t0 = time.time()
        print(f"  加载 sol_exact ...", flush=True)
        sol_raw = json.load(open(SOL_EXACT, encoding="utf-8")) if SOL_EXACT.exists() else {}
        self.sol_exact = {k: _index_series(v) for k, v in sol_raw.items()}
        print(f"  加载 sol_exact: {len(self.sol_exact)} tokens ({time.time()-t0:.1f}s)",
              flush=True)
        del sol_raw

        t0 = time.time()
        eth_block_raw = json.load(open(ETH_BLOCK, encoding="utf-8")) if ETH_BLOCK.exists() else {}
        self.eth_block = {k: _index_series(v) for k, v in eth_block_raw.items()}
        print(f"  加载 eth_block: {len(self.eth_block)} tokens ({time.time()-t0:.1f}s)",
              flush=True)
        del eth_block_raw

        # eth_1m: 只加载索引, 分片懒加载
        t0 = time.time()
        if ETH_1M_INDEX.exists():
            self._eth_1m_index = json.load(open(ETH_1M_INDEX, encoding="utf-8"))
            self._eth_1m_shard_dir = ETH_1M_SHARDS
            self._use_shards = True
            print(f"  eth_1m: {len(self._eth_1m_index)} tokens (lazy shard mode) "
                  f"({time.time()-t0:.1f}s)", flush=True)
        elif ETH_1M.exists():
            # 兜底: 没分片就回到整块加载 (慢, 但保持兼容)
            print(f"  warning: eth_1m shards not found, falling back to full load", flush=True)
            eth_1m_raw = json.load(open(ETH_1M, encoding="utf-8"))
            self._eth_1m_full = {k: _index_series(v) for k, v in eth_1m_raw.items()}
            self._use_shards = False
            del eth_1m_raw
            print(f"  eth_1m: {len(self._eth_1m_full)} tokens loaded "
                  f"({time.time()-t0:.1f}s)", flush=True)
        else:
            self._eth_1m_index = {}
            self._use_shards = True
            self._eth_1m_shard_dir = ETH_1M_SHARDS

        # LRU cache: key → (ts_list, values)
        self._eth_1m_cache = OrderedDict()
        self._eth_1m_misses = set()  # keys known to be absent (skip dir stat)

    def _load_eth_1m(self, key):
        """Return (ts_list, values) for a single ETH 1m token, lazy-loading shard."""
        if not self._use_shards:
            return self._eth_1m_full.get(key, ([], []))
        if key in self._eth_1m_cache:
            self._eth_1m_cache.move_to_end(key)
            return self._eth_1m_cache[key]
        if key in self._eth_1m_misses:
            return [], []
        fn = self._eth_1m_index.get(key)
        if not fn:
            self._eth_1m_misses.add(key)
            return [], []
        path = self._eth_1m_shard_dir / fn
        try:
            raw = json.load(open(path, encoding="utf-8"))
        except FileNotFoundError:
            self._eth_1m_misses.add(key)
            return [], []
        indexed = _index_series(raw)
        self._eth_1m_cache[key] = indexed
        while len(self._eth_1m_cache) > self.ETH_1M_LRU_MAX:
            self._eth_1m_cache.popitem(last=False)
        return indexed

    def get(self, key, ts):
        if not key or ts is None:
            return None, None, None
        if key in STABLES:
            return 1.0, "stable", 0

        chain = key.split(":", 1)[0]

        if chain == "solana" and key in self.sol_exact:
            ts_list, vals = self.sol_exact[key]
            p, d = _nearest_sorted(ts_list, vals, ts, EXACT_TOL_SEC)
            if p is not None:
                return p, "sol_exact", d
        elif chain == "ethereum":
            if key in self.eth_block:
                ts_list, vals = self.eth_block[key]
                p, d = _nearest_sorted(ts_list, vals, ts, BLOCK_TOL_SEC)
                if p is not None:
                    return p, "eth_block", d
            ts_list, vals = self._load_eth_1m(key)
            if ts_list:
                p, d = _nearest_sorted(ts_list, vals, ts, MIN_TOL_SEC)
                if p is not None:
                    return p, "eth_1m", d

        return None, None, None


def bridge_key(candidate, chain):
    br = candidate.get("bridge") or {}
    if chain == "SOL" and br.get("spl_mint"):
        return f"solana:{br['spl_mint']}"
    if chain == "ETH" and br.get("erc20_contract"):
        return f"ethereum:{br['erc20_contract'].lower()}"
    return None


def compute_swap_usd(swap, side, candidate, prices, symap):
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
    print(f"\n处理 {path.name}", flush=True)
    t0 = time.time()
    data = json.load(open(path, encoding="utf-8"))
    cands = data.get("candidates", [])
    print(f"  loaded {len(cands)} candidates ({time.time()-t0:.1f}s)", flush=True)

    source_stats = defaultdict(int)
    offset_stats = []
    complete_n = 0
    by_reliability = defaultdict(lambda: {"n": 0, "complete": 0, "net_pnl_sum": 0.0})

    t0 = time.time()
    total_n = len(cands)
    for i, c in enumerate(cands):
        if i and i % 10000 == 0:
            rate = i / (time.time() - t0)
            eta = (total_n - i) / rate if rate else 0
            print(f"    {i}/{total_n} ({rate:.0f}/s ETA {eta:.0f}s)", flush=True)
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

    def dist(pnls):
        xs = sorted(pnls)
        def pct(p):
            if not xs: return None
            i = int(p * (len(xs) - 1))
            return xs[i]
        return {
            "n": len(xs),
            "p01": pct(0.01), "p10": pct(0.10), "p25": pct(0.25),
            "median": pct(0.50),
            "p75": pct(0.75), "p90": pct(0.90), "p99": pct(0.99),
            "min": xs[0] if xs else None,
            "max": xs[-1] if xs else None,
            "sum": round(sum(xs), 2),
            "positive_n": sum(1 for x in xs if x > 0),
            "negative_n": sum(1 for x in xs if x < 0),
        }

    all_net = [c["pnl"]["net_pnl_usd"] for c in cands if c["pnl"]["complete"]]
    reliable_net = [c["pnl"]["net_pnl_usd"] for c in cands
                    if c["pnl"]["complete"]
                    and (c.get("pnl_reliability_greedy") or c.get("pnl_reliability")) == "reliable"]

    summary = {
        "candidates": len(cands),
        "pnl_complete": complete_n,
        "price_source_counts": dict(source_stats),
        "offset_avg_sec": round(sum(offset_stats) / len(offset_stats), 2) if offset_stats else None,
        "offset_max_sec": max(offset_stats) if offset_stats else None,
        "net_pnl_reliable_only": dist(reliable_net),
        "net_pnl_all_inc_unreliable": dist(all_net),
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

    for c in cands:
        if "pricing_trace" in c.get("pnl", {}):
            del c["pnl"]["pricing_trace"]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"  总计 {len(cands)}, complete PnL: {complete_n}")
    print(f"  price sources: {dict(source_stats)}")
    print(f"  offset avg: {summary['offset_avg_sec']}s, max: {summary['offset_max_sec']}s")
    r = summary["net_pnl_reliable_only"]
    a = summary["net_pnl_all_inc_unreliable"]
    print(f"  net PnL RELIABLE ONLY:   n={r['n']} sum=${r['sum']} median=${r['median']} (+{r['positive_n']}/-{r['negative_n']})")
    print(f"  net PnL ALL (inc unrel): n={a['n']} sum=${a['sum']} median=${a['median']} (+{a['positive_n']}/-{a['negative_n']})")


def main():
    symap = json.load(open(SYMBOL_MAP, encoding="utf-8"))
    prices = PriceLookup()
    eth_1m_n = len(getattr(prices, "_eth_1m_index", getattr(prices, "_eth_1m_full", {})))
    print(f"Price caches: sol_exact={len(prices.sol_exact)}, eth_block={len(prices.eth_block)}, eth_1m={eth_1m_n}")
    for f in CAND_FILES:
        process_file(f, prices, symap)
    print("\n完成")


if __name__ == "__main__":
    main()
