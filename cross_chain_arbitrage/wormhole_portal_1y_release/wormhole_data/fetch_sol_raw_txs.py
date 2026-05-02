#!/usr/bin/env python3
"""
获取 sol_signatures.json 中所有签名的完整 raw 交易数据。

对每个签名调用 getTransaction (jsonParsed)，保存完整返回结果。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_sol_raw_txs.py
"""

import requests
import json
import time
from datetime import datetime, timezone
from pathlib import Path

RPC_URL = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"

DIR = Path(__file__).parent / "use"
INPUT = DIR / "signatures" / "sol_signatures.json"
OUTPUT_DIR = DIR / "raw" / "sol_raw_txs"
INDEX_FILE = DIR / "raw" / "sol_raw_index.json"


def rpc_call(method, params):
    for attempt in range(5):
        try:
            resp = requests.post(RPC_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                if code == 429 or code == -32429:
                    time.sleep(2 ** attempt)
                    continue
                return None
            return data.get("result")
        except Exception:
            time.sleep(1)
    return None


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    # 收集唯一签名
    all_sigs = set()
    for info in data["results"].values():
        for s in info.get("signatures", []):
            all_sigs.add(s["sig"])

    print(f"唯一签名: {len(all_sigs):,} 笔")

    # 加载已完成的index
    done_set = set()
    if INDEX_FILE.exists():
        with open(INDEX_FILE, encoding="utf-8") as f:
            index = json.load(f)
        done_set = set(index.get("completed", []))
        print(f"已有 {len(done_set)} 笔，跳过")

    print()

    BATCH_SIZE = 1000
    sigs_sorted = sorted(all_sigs - done_set)
    total = len(all_sigs)
    to_do = len(sigs_sorted)
    done = 0
    errors = 0
    batch = []
    batch_num = len(done_set) // BATCH_SIZE
    print(f"待处理: {to_do} 笔\n")

    for sig in sigs_sorted:
        result = rpc_call("getTransaction", [sig, {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
        }])

        if result:
            batch.append({"sig": sig, "data": result})
        else:
            batch.append({"sig": sig, "data": None, "error": True})
            errors += 1
        done_set.add(sig)
        done += 1

        if done % 100 == 0 or done == to_do:
            print(f"  [{len(done_set)}/{total}]  本次{done}/{to_do}  失败{errors}", flush=True)

        if len(batch) >= BATCH_SIZE:
            batch_num += 1
            out_path = OUTPUT_DIR / f"batch_{batch_num:04d}.json"
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(batch, f, indent=2, ensure_ascii=False)
            print(f"    保存 {out_path.name} ({len(batch)}笔)", flush=True)
            batch = []
            with open(INDEX_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "total": total, "completed_count": len(done_set),
                    "errors": errors, "completed": sorted(done_set),
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
            "total": total, "completed_count": len(done_set),
            "errors": errors, "batch_files": batch_num,
            "completed": sorted(done_set), "complete": True,
            "updated": datetime.now(timezone.utc).isoformat(),
        }, f, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"  总交易: {len(done_set):,} 笔")
    print(f"  失败: {errors}")
    print(f"  文件数: {batch_num}")
    total_size = sum(f.stat().st_size for f in OUTPUT_DIR.glob("batch_*.json")) / 1024 / 1024
    print(f"  总大小: {total_size:.1f} MB")


if __name__ == "__main__":
    main()
