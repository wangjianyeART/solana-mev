#!/usr/bin/env python3
"""
对每条 matched 记录查 Wormholescan, 拿 sourceChain / targetChain,
输出:
  chain_verification.json
    {
      "<sol_sig>": {"src": <chainId>, "tgt": <chainId>, "api_eth_hash": <str or None>, "our_eth_hash": <str>}
    }
只保留 (1,2) 或 (2,1) 的记录 —— 其余是 BNB/SUI/etc. 污染。
"""

import json
import time
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
MATCHED = ROOT / "matched.json"
OUT = ROOT / "arbitrage" / "chain_verification.json"

API = "https://api.wormholescan.io/api/v1/operations"
MAX_WORKERS = 10
REQ_TIMEOUT = 15


def query(sig):
    for attempt in range(3):
        try:
            r = requests.get(API, params={"txHash": sig}, timeout=REQ_TIMEOUT)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                return {"error": f"http_{r.status_code}"}
            ops = (r.json() or {}).get("operations") or []
            if not ops:
                return {"error": "no_ops"}
            op = ops[0]
            sc = op.get("sourceChain") or {}
            tc = op.get("targetChain") or {}
            return {
                "src": sc.get("chainId"),
                "tgt": tc.get("chainId"),
                "src_tx": (sc.get("transaction") or {}).get("txHash"),
                "tgt_tx": (tc.get("transaction") or {}).get("txHash"),
            }
        except Exception as e:
            if attempt == 2:
                return {"error": str(e)[:80]}
            time.sleep(1)
    return {"error": "retries"}


def main():
    data = json.load(open(MATCHED, encoding="utf-8"))
    recs = data.get("records") or data.get("matches") or data
    print(f"总 {len(recs)} 条")

    # Resume
    cache = {}
    if OUT.exists():
        cache = json.load(open(OUT, encoding="utf-8"))
        print(f"已有缓存: {len(cache)}")

    todo = [r for r in recs if r.get("sol_sig") and r["sol_sig"] not in cache]
    print(f"待查: {len(todo)}")
    if not todo:
        print("全查完")
    else:
        t0 = time.time()
        done = 0
        err = 0
        err_types = defaultdict(int)

        def _do(r):
            return r["sol_sig"], r.get("eth_hash"), query(r["sol_sig"])

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_do, r): r["sol_sig"] for r in todo}
            for fut in as_completed(futures):
                sig, our_eth, res = fut.result()
                cache[sig] = {**res, "our_eth_hash": our_eth}
                done += 1
                if "error" in res:
                    err += 1
                    err_types[res["error"]] += 1
                if done % 200 == 0:
                    rate = done / (time.time() - t0)
                    eta = (len(todo) - done) / rate if rate else 0
                    print(f"  {done}/{len(todo)} err={err} ({rate:.1f}/s, ETA {eta:.0f}s)")
                    json.dump(cache, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)

        json.dump(cache, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"完成: err={err}, 耗时 {time.time()-t0:.1f}s, 错误: {dict(err_types)}")

    # 统计链对
    pairs = defaultdict(int)
    errors = 0
    for sig, v in cache.items():
        if v.get("error"):
            errors += 1
            continue
        pairs[(v.get("src"), v.get("tgt"))] += 1

    print(f"\n链对分布 (src → tgt):")
    for (s, t), n in sorted(pairs.items(), key=lambda x: -x[1]):
        print(f"  {s} → {t}: {n}")
    print(f"API 错误 / 查不到: {errors}")


if __name__ == "__main__":
    main()
