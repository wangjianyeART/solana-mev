#!/usr/bin/env python3
"""
获取 Wormhole Portal Token Bridge 过去1年的所有签名。

Program: wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_portal_sigs_1y.py
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

RPC_URL = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"
PROGRAM = "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb"

OUTPUT = Path(__file__).parent / "use" / "portal_full" / "portal_bridge_signatures_all.json"

DAYS = 0  # 0 = 全部历史数据
CUTOFF = 0  # 不做时间截断


def rpc_call(method, params):
    for attempt in range(8):
        try:
            resp = requests.post(RPC_URL, json={
                "jsonrpc": "2.0", "id": 1,
                "method": method, "params": params,
            }, timeout=30)
            data = resp.json()
            if "error" in data:
                code = data["error"].get("code", 0)
                if code == 429 or code == -32429:
                    wait = 2 ** attempt
                    print(f"    rate limit, 等{wait}s...", flush=True)
                    time.sleep(wait)
                    continue
                print(f"    RPC错误: {data['error']}", flush=True)
                return None
            return data.get("result")
        except Exception as e:
            print(f"    请求异常: {e}, 重试...", flush=True)
            time.sleep(2)
    return None


def load_existing():
    """加载已有数据（断点续跑）"""
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            data = json.load(f)
        sigs = data.get("signatures", [])
        last_sig = data.get("meta", {}).get("last_before")
        print(f"断点续跑: 已有 {len(sigs)} 条, 从上次位置继续")
        return sigs, last_sig
    return [], None


def save(sigs, before_sig, done=False):
    output = {
        "meta": {
            "program": PROGRAM,
            "program_name": "Wormhole Portal Token Bridge",
            "days": DAYS,
            "total_signatures": len(sigs),
            "time_earliest": sigs[-1]["time"] if sigs else None,
            "time_latest": sigs[0]["time"] if sigs else None,
            "cutoff": datetime.fromtimestamp(CUTOFF, tz=timezone.utc).isoformat(),
            "last_before": before_sig,
            "complete": done,
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "signatures": sigs,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


def main():
    print(f"Program: {PROGRAM}")
    print(f"范围: 过去{DAYS}天 (截止 {datetime.fromtimestamp(CUTOFF, tz=timezone.utc).isoformat()})")
    print()

    sigs, before = load_existing()
    page = 0

    while True:
        opts = {"limit": 1000}
        if before:
            opts["before"] = before

        result = rpc_call("getSignaturesForAddress", [PROGRAM, opts])
        if not result:
            print("  空结果，结束")
            break

        stop = False
        new_count = 0
        for r in result:
            ts = r.get("blockTime", 0)
            if CUTOFF and ts and ts < CUTOFF:
                stop = True
                break
            sigs.append({
                "sig": r["signature"],
                "ts": ts,
                "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                "slot": r.get("slot"),
                "err": r.get("err"),
            })
            new_count += 1

        page += 1
        before = result[-1]["signature"]
        earliest = sigs[-1]["time"] if sigs else "?"
        print(f"  第{page}页  +{new_count}条  总{len(sigs):,}条  最早: {earliest}", flush=True)

        # 每10页保存一次
        if page % 10 == 0:
            save(sigs, before)
            print(f"    已保存", flush=True)

        if stop or len(result) < 1000:
            break

        time.sleep(0.3)

    save(sigs, before, done=True)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n{'=' * 60}")
    print(f"  总签名: {len(sigs):,} 条")
    print(f"  时间: {sigs[-1]['time']} → {sigs[0]['time']}")
    print(f"  保存: {OUTPUT.name} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
