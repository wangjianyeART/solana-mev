#!/usr/bin/env python3
"""
从 arbitrage_candidates_1pct.json 提取所有需要的 (token, ts) 组合,
批量查 DefiLlama historical API, 缓存到本地。

查询逻辑:
  - 对每条候选的 entry_swaps_greedy/exit_swaps_greedy:
    - ts 按 swap ts (不分桶, DefiLlama searchWidth=4h 本身容忍时间差)
    - 提取 sold + bought 里所有 token, 解析为 DefiLlama key
  - 对每条候选额外加:
    - (SOL native, sol_ts) - 用于 sol_fee 美元估值
    - (ETH native, eth_ts) - 用于 eth_fee 美元估值
    - bridge.spl_mint + bridge.erc20_contract at sol_ts / eth_ts (备用)
  - 按 ts 分组, 每个 ts 批 30 token 一查

输出:
  arbitrage/prices/defillama_cache.json
    { "<defillama_key>": { "<ts>": {"price": ..., "confidence": ..., "timestamp": ...} } }
  arbitrage/prices/query_stats.json
    规模汇总

  --extract-only  仅提取查询集, 不发请求 (打印规模)
  --limit N       只跑前 N 条候选 (测试用)
"""

import json
import sys
import time
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
CANDIDATES = ROOT / "arbitrage_candidates_1pct.json"
PRICES_DIR = ROOT / "prices"
SYMBOL_MAP = PRICES_DIR / "symbol_map.json"
CACHE_FILE = PRICES_DIR / "defillama_cache.json"
STATS_FILE = PRICES_DIR / "query_stats.json"

BATCH = 30
SEARCH_WIDTH = "4h"
REQ_TIMEOUT = 15
SLEEP_BETWEEN = 0.02
BUCKET_SEC = 300  # 5min 桶
MAX_WORKERS = 5

# Native + Wormhole 特殊
SOL_NATIVE = "solana:So11111111111111111111111111111111111111112"
ETH_NATIVE = "coingecko:ethereum"


def is_eth_addr(s):
    return isinstance(s, str) and s.startswith("0x") and len(s) == 42

def is_sol_addr(s):
    return isinstance(s, str) and 32 <= len(s) <= 44 and not s.startswith("0x") and s.isalnum()


def resolve_token(chain, tok, symap):
    """返回 DefiLlama key 或 None"""
    if not tok:
        return None
    if chain == "ETH":
        if tok == "ETH":
            return ETH_NATIVE
        if is_eth_addr(tok):
            return f"ethereum:{tok.lower()}"
        addr = symap["eth"].get(tok)
        if addr:
            return f"ethereum:{addr.lower()}"
        return None
    elif chain == "SOL":
        if tok in ("SOL", "WSOL"):
            return SOL_NATIVE
        if is_sol_addr(tok):
            return f"solana:{tok}"
        addr = symap["sol"].get(tok)
        if addr:
            return f"solana:{addr}"
        return None
    return None


def bucket(ts):
    return (ts // BUCKET_SEC) * BUCKET_SEC


def extract_queries(candidates, symap, limit=None):
    """返回 {ts_bucket: set(defillama_keys)} 以及 unresolved symbols"""
    queries = defaultdict(set)
    unresolved = defaultdict(int)
    resolved_count = 0
    total_tokens = 0

    cands = candidates[:limit] if limit else candidates

    for c in cands:
        sol_ts = c.get("sol_ts")
        eth_ts = c.get("eth_ts")
        br = c.get("bridge") or {}

        if br.get("spl_mint") and sol_ts:
            queries[bucket(sol_ts)].add(f"solana:{br['spl_mint']}")
        if br.get("erc20_contract") and eth_ts:
            queries[bucket(eth_ts)].add(f"ethereum:{br['erc20_contract'].lower()}")

        if sol_ts:
            queries[bucket(sol_ts)].add(SOL_NATIVE)
        if eth_ts:
            queries[bucket(eth_ts)].add(ETH_NATIVE)

        for s in (c.get("entry_swaps_greedy") or []) + (c.get("exit_swaps_greedy") or []):
            ts = s.get("ts")
            chain = s.get("chain")
            if not ts or not chain:
                continue
            for item in (s.get("sold") or []) + (s.get("bought") or []):
                total_tokens += 1
                tok = item.get("token")
                key = resolve_token(chain, tok, symap)
                if key:
                    queries[bucket(ts)].add(key)
                    resolved_count += 1
                else:
                    unresolved[f"{chain}:{tok}"] += 1

    return queries, unresolved, resolved_count, total_tokens


def fetch_batch(ts, keys):
    """对一个 ts 查一批 keys (<=30 个)"""
    coins = ",".join(keys)
    url = f"https://coins.llama.fi/prices/historical/{ts}/{coins}?searchWidth={SEARCH_WIDTH}"
    try:
        r = requests.get(url, timeout=REQ_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data.get("coins") or {}
    except Exception as e:
        return {"__error__": str(e)}


def main():
    args = sys.argv[1:]
    extract_only = "--extract-only" in args
    limit = None
    for a in args:
        if a.startswith("--limit="):
            limit = int(a.split("=")[1])

    print(f"加载候选: {CANDIDATES.name}")
    cdata = json.load(open(CANDIDATES, encoding="utf-8"))
    cands = cdata.get("candidates", [])
    print(f"  {len(cands)} 条")

    print(f"加载 symbol_map: {SYMBOL_MAP.name}")
    symap = json.load(open(SYMBOL_MAP, encoding="utf-8"))

    print(f"\n提取查询...")
    queries, unresolved, resolved_n, total_n = extract_queries(cands, symap, limit=limit)

    unique_ts = len(queries)
    total_pairs = sum(len(v) for v in queries.values())
    unique_keys = set()
    for ks in queries.values():
        unique_keys |= ks
    batches = sum((len(v) + BATCH - 1) // BATCH for v in queries.values())

    stats = {
        "total_candidates_scanned": limit or len(cands),
        "unique_ts": unique_ts,
        "unique_token_keys": len(unique_keys),
        "total_(ts,token)_pairs": total_pairs,
        "estimated_http_batches": batches,
        "estimated_seconds_at_0.35s": round(batches * 0.35, 1),
        "token_resolution": {
            "total_token_mentions": total_n,
            "resolved": resolved_n,
            "unresolved": sum(unresolved.values()),
            "unresolved_unique_symbols": len(unresolved),
        },
        "top_unresolved": sorted(unresolved.items(), key=lambda kv: -kv[1])[:20],
    }
    json.dump(stats, open(STATS_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    if extract_only:
        print(f"\n[extract-only] 跳过 fetch. 统计写入 {STATS_FILE.name}")
        return

    # Load existing cache
    cache = {}
    if CACHE_FILE.exists():
        cache = json.load(open(CACHE_FILE, encoding="utf-8"))
        print(f"\n已有缓存: {len(cache)} 个 token, {sum(len(v) for v in cache.values())} 条 price 记录")

    # 建任务队列: (ts, chunk)
    tasks = []
    for ts in sorted(queries.keys()):
        keys_at_ts = list(queries[ts])
        need = [k for k in keys_at_ts if str(ts) not in cache.get(k, {})]
        for i in range(0, len(need), BATCH):
            tasks.append((ts, need[i:i+BATCH]))

    if not tasks:
        print("\n无需 fetch, cache 已完整")
        return

    print(f"\n待跑任务: {len(tasks)} batches, 并发 {MAX_WORKERS}")
    t0 = time.time()
    done = 0
    fetch_errors = 0
    prices_saved = 0

    def _do(task):
        ts, chunk = task
        return ts, chunk, fetch_batch(ts, chunk)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_do, t): t for t in tasks}
        for fut in as_completed(futures):
            ts, chunk, result = fut.result()
            done += 1
            if "__error__" in result:
                fetch_errors += 1
            else:
                for k, v in result.items():
                    cache.setdefault(k, {})[str(ts)] = v
                    prices_saved += 1
            if done % 100 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed else 0
                eta = (len(tasks) - done) / rate if rate else 0
                print(f"  batch {done}/{len(tasks)} "
                      f"({rate:.1f}/s, 已存 {prices_saved}, 错 {fetch_errors}, ETA {eta:.0f}s)")
                json.dump(cache, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)

    json.dump(cache, open(CACHE_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    elapsed = time.time() - t0
    print(f"\n完成: batches={done}, 新存 prices={prices_saved}, 错 {fetch_errors}, 耗时 {elapsed:.1f}s")
    print(f"缓存规模: {len(cache)} token, {sum(len(v) for v in cache.values())} 条 price")


if __name__ == "__main__":
    main()
