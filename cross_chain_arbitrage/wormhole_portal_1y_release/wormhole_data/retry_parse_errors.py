#!/usr/bin/env python3
"""
重跑 batch_parse_context.py 缓存中的 error 条目。

只重跑 error="rpc_failed"（网络/RPC 失败），跳过 error="tx_failed"（链上失败）。
成功的结果会覆盖缓存中的 error 条目。

用法:
    python wormhole_data/retry_parse_errors.py
    python wormhole_data/retry_parse_errors.py --all    # 也重跑 tx_failed
    python wormhole_data/retry_parse_errors.py --sol    # 只跑 SOL
    python wormhole_data/retry_parse_errors.py --eth    # 只跑 ETH
"""

import json
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from batch_parse_context import (
    SOL_CACHE_FILE, ETH_CACHE_FILE,
    parse_sol_tx, parse_eth_tx,
    load_erc20_cache,
)


def retry_sol(cache, only_rpc=True, max_workers=20):
    # 找出 error 条目
    targets = []
    for sig, entry in cache.items():
        if not isinstance(entry, dict) or not entry.get("error"):
            continue
        if only_rpc and entry["error"] != "rpc_failed":
            continue
        targets.append(sig)

    if not targets:
        print("  SOL: 无需重跑")
        return 0, 0

    print(f"  SOL: 需重跑 {len(targets)} 笔")
    done = 0
    fixed = 0
    still_err = 0

    def _do(sig):
        return sig, parse_sol_tx(sig)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_do, sig): sig for sig in targets}
        for future in as_completed(futures):
            sig, result = future.result()
            done += 1
            if result.get("error"):
                still_err += 1
            else:
                fixed += 1
                cache[sig] = result  # 只在成功时覆盖
            if done % 200 == 0:
                print(f"    SOL 进度: {done}/{len(targets)} (修复={fixed}, 仍失败={still_err})")
                with open(SOL_CACHE_FILE, "w") as f:
                    json.dump(cache, f, ensure_ascii=False)

    with open(SOL_CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    print(f"  SOL 完成: 修复={fixed}, 仍失败={still_err}")
    return fixed, still_err


def retry_eth(cache, only_rpc=True, max_workers=10):
    targets = []
    for h, entry in cache.items():
        if not isinstance(entry, dict) or not entry.get("error"):
            continue
        if only_rpc and entry["error"] != "rpc_failed":
            continue
        targets.append(h)

    if not targets:
        print("  ETH: 无需重跑")
        return 0, 0

    print(f"  ETH: 需重跑 {len(targets)} 笔")
    done = 0
    fixed = 0
    still_err = 0

    def _do(h):
        return h, parse_eth_tx(h)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_do, h): h for h in targets}
        for future in as_completed(futures):
            h, result = future.result()
            done += 1
            if result.get("error"):
                still_err += 1
            else:
                fixed += 1
                cache[h] = result
            if done % 200 == 0:
                print(f"    ETH 进度: {done}/{len(targets)} (修复={fixed}, 仍失败={still_err})")
                with open(ETH_CACHE_FILE, "w") as f:
                    json.dump(cache, f, ensure_ascii=False)

    with open(ETH_CACHE_FILE, "w") as f:
        json.dump(cache, f, ensure_ascii=False)
    print(f"  ETH 完成: 修复={fixed}, 仍失败={still_err}")
    return fixed, still_err


def main():
    args = sys.argv[1:]
    only_rpc = "--all" not in args
    do_sol = "--eth" not in args
    do_eth = "--sol" not in args

    load_erc20_cache()

    if do_sol:
        if not SOL_CACHE_FILE.exists():
            print(f"SOL 缓存不存在: {SOL_CACHE_FILE}")
        else:
            sol_cache = json.load(open(SOL_CACHE_FILE))
            err_count = sum(1 for v in sol_cache.values()
                            if isinstance(v, dict) and v.get("error"))
            rpc_count = sum(1 for v in sol_cache.values()
                            if isinstance(v, dict) and v.get("error") == "rpc_failed")
            tx_count = sum(1 for v in sol_cache.values()
                           if isinstance(v, dict) and v.get("error") == "tx_failed")
            print(f"SOL 缓存: {len(sol_cache)} 笔, 错误={err_count} (rpc_failed={rpc_count}, tx_failed={tx_count})")
            t0 = time.time()
            retry_sol(sol_cache, only_rpc=only_rpc)
            print(f"  耗时: {time.time() - t0:.1f}s\n")

    if do_eth:
        if not ETH_CACHE_FILE.exists():
            print(f"ETH 缓存不存在: {ETH_CACHE_FILE}")
        else:
            eth_cache = json.load(open(ETH_CACHE_FILE))
            err_count = sum(1 for v in eth_cache.values()
                            if isinstance(v, dict) and v.get("error"))
            rpc_count = sum(1 for v in eth_cache.values()
                            if isinstance(v, dict) and v.get("error") == "rpc_failed")
            tx_count = sum(1 for v in eth_cache.values()
                           if isinstance(v, dict) and v.get("error") == "tx_failed")
            print(f"ETH 缓存: {len(eth_cache)} 笔, 错误={err_count} (rpc_failed={rpc_count}, tx_failed={tx_count})")
            t0 = time.time()
            retry_eth(eth_cache, only_rpc=only_rpc)
            print(f"  耗时: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
