#!/usr/bin/env python3
"""
对 eth_parsed.json 里 src/dst 涉及 Solana 的记录(+ unknown 的 inbound)
调 Wormholescan API 查 sourceChain / targetChain 以及对应的 tx hash。

保留 (src=1,tgt=2) 或 (src=2,tgt=1) 的配对,输出:
  recent_1y/eth_to_sol_via_api.json
    {
      eth_hash: {
        "sol_sig": str,
        "src_chain": int,  # 1 = Solana, 2 = Ethereum
        "tgt_chain": int,
        "direction": "SOL→ETH" or "ETH→SOL",
        "ts_src": None,  # 若 API 返回
        "ts_tgt": None,
      }
    }

Resume: 读已有 cache,跳过 hash 已查过的。
错误单独记录,便于补抓。

用法: python wormhole_data/wormhole_lookup_1y.py
"""

import json
import time
import requests
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_1y"
ETH_PARSED = ROOT / "eth_parsed.json"
OUT = ROOT / "eth_to_sol_via_api.json"
ERR_OUT = ROOT / "eth_to_sol_via_api_errors.json"

API = "https://api.wormholescan.io/api/v1/operations"
MAX_WORKERS = 15
REQ_TIMEOUT = 15
SAVE_EVERY = 500


def query(txhash):
    for attempt in range(3):
        try:
            r = requests.get(API, params={"txHash": txhash}, timeout=REQ_TIMEOUT)
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
                "src_chain": sc.get("chainId"),
                "tgt_chain": tc.get("chainId"),
                "src_tx": (sc.get("transaction") or {}).get("txHash"),
                "tgt_tx": (tc.get("transaction") or {}).get("txHash"),
                "ts_src": sc.get("timestamp"),
                "ts_tgt": tc.get("timestamp"),
            }
        except Exception as e:
            if attempt == 2:
                return {"error": str(e)[:80]}
            time.sleep(1)
    return {"error": "retries"}


def main():
    t0 = time.time()
    print("=" * 60)
    print("加载 eth_parsed.json ...")
    eth_doc = json.load(open(ETH_PARSED, encoding="utf-8"))
    eth_records = eth_doc["transactions"]
    print(f"  total ETH: {len(eth_records)}")

    # 宽口径筛: 凡 src 或 dst 含 Solana / unknown 的都查
    candidates = []
    for r in eth_records:
        sc = r.get("src_chain")
        dc = r.get("dst_chain")
        if sc == "Solana" or dc == "Solana":
            candidates.append(r["hash"])
        elif sc == "unknown" or dc == "unknown":
            candidates.append(r["hash"])  # 让 API 判
    print(f"  候选 (SOL-related + unknown): {len(candidates)}")

    # Resume
    cache = {}
    if OUT.exists():
        cache = json.load(open(OUT, encoding="utf-8"))
        print(f"  已缓存: {len(cache)}")
    errors = {}
    if ERR_OUT.exists():
        errors = json.load(open(ERR_OUT, encoding="utf-8"))
        print(f"  已记错: {len(errors)}")

    # 错误也算已查过(但允许最多重试一次 — 这里先全部跳过错误)
    done_set = set(cache.keys()) | set(errors.keys())
    todo = [h for h in candidates if h not in done_set]
    print(f"  待查: {len(todo)} (预估 @ 10/s = {len(todo)/10/60:.0f} min)")

    if not todo:
        print("  全查完")
    else:
        done = ok_pair = wrong_pair = err = 0
        err_types = defaultdict(int)
        last_save = time.time()

        def _do(h):
            return h, query(h)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = [pool.submit(_do, h) for h in todo]
            for f in as_completed(futs):
                h, res = f.result()
                done += 1
                if res.get("error"):
                    errors[h] = res["error"]
                    err += 1
                    err_types[res["error"]] += 1
                else:
                    src = res.get("src_chain")
                    tgt = res.get("tgt_chain")
                    # 只保留 (1,2) 或 (2,1)
                    if (src, tgt) in [(1, 2), (2, 1)]:
                        if src == 2 and tgt == 1:
                            direction = "ETH→SOL"
                            sol_sig = res.get("tgt_tx")
                        else:
                            direction = "SOL→ETH"
                            sol_sig = res.get("src_tx")
                        cache[h] = {
                            "sol_sig": sol_sig,
                            "src_chain": src,
                            "tgt_chain": tgt,
                            "direction": direction,
                            "ts_src": res.get("ts_src"),
                            "ts_tgt": res.get("ts_tgt"),
                        }
                        ok_pair += 1
                    else:
                        # 非 SOL↔ETH 对(eg ETH↔BSC, ETH↔Arb, tgt=None 等),记一下就行
                        errors[h] = f"pair_{src}_{tgt}"
                        wrong_pair += 1
                        err_types[f"pair_{src}_{tgt}"] += 1

                if done % 100 == 0:
                    rate = done / (time.time() - t0)
                    eta = (len(todo) - done) / rate if rate else 0
                    print(f"  {done}/{len(todo)} ok={ok_pair} wrong={wrong_pair} err={err} "
                          f"({rate:.1f}/s ETA {eta:.0f}s)")

                if time.time() - last_save > 60 or done % SAVE_EVERY == 0:
                    with open(OUT, "w", encoding="utf-8") as f_out:
                        json.dump(cache, f_out, ensure_ascii=False)
                    with open(ERR_OUT, "w", encoding="utf-8") as f_err:
                        json.dump(errors, f_err, ensure_ascii=False)
                    last_save = time.time()

        with open(OUT, "w", encoding="utf-8") as f_out:
            json.dump(cache, f_out, ensure_ascii=False)
        with open(ERR_OUT, "w", encoding="utf-8") as f_err:
            json.dump(errors, f_err, ensure_ascii=False)
        print(f"\n  done={done} ok_pair={ok_pair} wrong_pair={wrong_pair} err={err}")
        print(f"  err_types: {dict(err_types)}")
        print(f"  耗时: {time.time()-t0:.0f}s")

    # 统计
    print(f"\n  最终缓存: {len(cache)} 对 SOL↔ETH")
    dirs = defaultdict(int)
    for v in cache.values():
        dirs[v["direction"]] += 1
    print(f"  方向: {dict(dirs)}")
    print(f"  输出: {OUT}")
    print(f"        {ERR_OUT}")


if __name__ == "__main__":
    main()
