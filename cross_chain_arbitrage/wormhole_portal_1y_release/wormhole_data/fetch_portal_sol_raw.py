#!/usr/bin/env python3
"""
获取 portal_bridge_signatures_all.json 中过去1年签名的完整 raw 交易数据。

使用多线程 + 限速控制在 ~80 rps，边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_portal_sol_raw.py           # 前1000个
    python wormhole_data/fetch_portal_sol_raw.py --all     # 过去1年全部 (~34万笔)
"""

import requests
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

RPC_URL = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"

DIR = Path(__file__).parent / "use"
INPUT = DIR / "portal_full" / "portal_bridge_signatures_all.json"
OUTPUT_DIR = DIR / "portal_full" / "portal_sol_raw"
INDEX_FILE = DIR / "portal_full" / "portal_sol_raw_index.json"

LIMIT = 1000
BATCH_SIZE = 1000
ONE_YEAR = 365 * 86400
WORKERS = 5

_local = threading.local()
# 全局限速器
_rate_lock = threading.Lock()
_last_request = [0.0]  # 用列表使其可变
MIN_INTERVAL = 0.05  # 每个请求最少间隔50ms → 每线程20rps，5线程≈100rps


def get_session():
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    return _local.session


def rate_limit():
    with _rate_lock:
        now = time.time()
        wait = MIN_INTERVAL - (now - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()


def fetch_one(sig):
    session = get_session()
    for attempt in range(8):
        rate_limit()
        try:
            resp = session.post(RPC_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [sig, {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                }],
            }, timeout=60)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                if code == 429 or code == -32429:
                    time.sleep(2 ** attempt + 1)
                    continue
                return {"sig": sig, "data": None, "error": True}
            result = data.get("result")
            if result:
                return {"sig": sig, "data": result}
            else:
                return {"sig": sig, "data": None, "error": True}
        except Exception:
            time.sleep(2 + attempt)
    return {"sig": sig, "data": None, "error": True}


def main():
    fetch_all = "--all" in sys.argv

    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    cutoff = int(time.time()) - ONE_YEAR
    all_sigs = [s["sig"] for s in data["signatures"]
                if not s.get("err") and s.get("ts", 0) >= cutoff]
    skipped_err = sum(1 for s in data["signatures"] if s.get("err"))
    skipped_old = sum(1 for s in data["signatures"]
                      if not s.get("err") and s.get("ts", 0) < cutoff)

    if not fetch_all:
        all_sigs = all_sigs[:LIMIT]

    print(f"Portal SOL签名: {len(data['signatures']):,} 总")
    print(f"  跳过链上失败: {skipped_err:,}")
    print(f"  跳过超过1年: {skipped_old:,}")
    print(f"本次获取: {len(all_sigs):,} 笔  {WORKERS}线程 限速~{int(1/MIN_INTERVAL)} rps")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 断点续跑
    done_set = set()
    failed_set = set()
    batch_files = sorted(OUTPUT_DIR.glob("batch_*.json"))
    if batch_files:
        print(f"扫描 {len(batch_files)} 个已有batch文件...", flush=True)
        for bf in batch_files:
            with open(bf, encoding="utf-8") as f:
                batch_data = json.load(f)
            for entry in batch_data:
                if entry.get("error"):
                    failed_set.add(entry["sig"])
                else:
                    done_set.add(entry["sig"])
        print(f"已有 {len(done_set):,} 笔成功，{len(failed_set):,} 笔失败将重试\n")

    to_do = [s for s in all_sigs if s not in done_set]
    total = len(all_sigs)
    errors = 0
    batch = []
    batch_num = len(batch_files) if batch_files else 0
    lock = threading.Lock()
    done = 0
    start_time = time.time()

    if not to_do:
        print("全部已完成，无需获取")
        return

    print(f"待获取: {len(to_do):,} 笔\n")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(fetch_one, s): s for s in to_do}
        for future in as_completed(futures):
            result = future.result()
            with lock:
                batch.append(result)
                if result.get("error"):
                    errors += 1
                done_set.add(result["sig"])
                done += 1

                if done % 500 == 0 or done == len(to_do):
                    elapsed = time.time() - start_time
                    rps = done / elapsed if elapsed > 0 else 0
                    eta = (len(to_do) - done) / rps if rps > 0 else 0
                    eta_min = eta / 60
                    fail_pct = errors / done * 100 if done else 0
                    print(f"  [{len(done_set):,}/{total:,}]  {done:,}/{len(to_do):,}  "
                          f"{rps:.1f} rps  失败{errors}({fail_pct:.1f}%)  ETA {eta_min:.1f}min", flush=True)

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
    print(f"  文件数: {batch_num}")
    total_size = sum(f.stat().st_size for f in OUTPUT_DIR.glob("batch_*.json")) / 1024 / 1024
    print(f"  总大小: {total_size:.1f} MB")


if __name__ == "__main__":
    main()
