#!/usr/bin/env python3
"""
获取 portal_eth_txs_all.json 中交易的完整 raw 数据（receipt + logs）。

使用 Chainstack ETH RPC + 多线程并发。先获取前1000个测试。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_portal_eth_raw.py          # 前1000个
    python wormhole_data/fetch_portal_eth_raw.py --all     # 全部
"""

import requests
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

CHAINSTACK_URL = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"

DIR = Path(__file__).parent / "use"
INPUT = DIR / "portal_full" / "portal_eth_txs_all.json"
OUTPUT_DIR = DIR / "portal_full" / "portal_eth_raw"
INDEX_FILE = DIR / "portal_full" / "portal_eth_raw_index.json"

LIMIT = 1000
WORKERS = 30  # 降低并发减少失败

# 每个线程用自己的session复用连接
_local = threading.local()

def get_session():
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def rpc_call(method, params):
    session = get_session()
    for attempt in range(8):
        try:
            resp = session.post(CHAINSTACK_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=60)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                if code == 429:
                    time.sleep(2 ** attempt)
                    continue
                return None
            return data.get("result")
        except Exception:
            time.sleep(1 + attempt)
    return None


def fetch_one(txhash):
    receipt = rpc_call("eth_getTransactionReceipt", [txhash])
    if receipt:
        return {"hash": txhash, "receipt": receipt}
    else:
        return {"hash": txhash, "receipt": None, "error": True}


def main():
    fetch_all = "--all" in sys.argv

    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    all_hashes = [tx["hash"] for tx in data["transactions"]]
    if not fetch_all:
        all_hashes = all_hashes[:LIMIT]

    print(f"Portal ETH交易: {len(data['transactions']):,} 总")
    print(f"本次获取: {len(all_hashes):,} 笔  并发: {WORKERS}")
    print()

    OUTPUT_DIR.mkdir(exist_ok=True)

    # 断点续跑：扫描已有batch文件，只把成功的加入done_set（失败的会被重试）
    done_set = set()
    failed_set = set()
    batch_files = sorted(OUTPUT_DIR.glob("batch_*.json"))
    if batch_files:
        for bf in batch_files:
            with open(bf, encoding="utf-8") as f:
                batch_data = json.load(f)
            for entry in batch_data:
                if entry.get("error"):
                    failed_set.add(entry["hash"])
                else:
                    done_set.add(entry["hash"])
        print(f"已有 {len(done_set)} 笔成功，{len(failed_set)} 笔失败将重试\n")

    BATCH_SIZE = 500
    to_do = [h for h in all_hashes if h not in done_set]
    total = len(all_hashes)
    errors = 0
    batch = []
    # 新batch从现有文件后开始编号
    batch_num = len(batch_files) if batch_files else 0
    lock = threading.Lock()
    done = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_one, h): h for h in to_do}
        for future in as_completed(futures):
            result = future.result()
            with lock:
                batch.append(result)
                if result.get("error"):
                    errors += 1
                done_set.add(result["hash"])
                done += 1

                if done % 100 == 0 or done == len(to_do):
                    elapsed = time.time() - start_time
                    rps = done / elapsed if elapsed > 0 else 0
                    eta = (len(to_do) - done) / rps if rps > 0 else 0
                    eta_min = eta / 60
                    print(f"  [{len(done_set)}/{total}]  {done}/{len(to_do)}  "
                          f"{rps:.1f} rps  失败{errors}  ETA {eta_min:.1f}min", flush=True)

                if len(batch) >= BATCH_SIZE:
                    batch_num += 1
                    out_path = OUTPUT_DIR / f"batch_{batch_num:04d}.json"
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(batch, f, indent=2, ensure_ascii=False)
                    print(f"    保存 {out_path.name} ({len(batch)}笔)", flush=True)
                    batch = []
                    with open(INDEX_FILE, "w", encoding="utf-8") as f:
                        json.dump({
                            "total": total, "success_count": len(done_set),
                            "errors": errors,
                            "updated": datetime.now(timezone.utc).isoformat(),
                        }, f, ensure_ascii=False)

    if batch:
        batch_num += 1
        out_path = OUTPUT_DIR / f"batch_{batch_num:04d}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(batch, f, indent=2, ensure_ascii=False)
        print(f"    保存 {out_path.name} ({len(batch)}笔)", flush=True)

    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "total": total, "success_count": len(done_set),
            "errors": errors, "batch_files": batch_num,
            "complete": True,
            "updated": datetime.now(timezone.utc).isoformat(),
        }, f, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  完成: {len(done_set):,} 笔")
    print(f"  失败: {errors}")
    total_size = sum(f.stat().st_size for f in OUTPUT_DIR.glob("batch_*.json")) / 1024 / 1024
    print(f"  总大小: {total_size:.1f} MB")


if __name__ == "__main__":
    main()
