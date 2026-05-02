#!/usr/bin/env python3
"""
批量获取 all_addresses.json 中所有 Solana 地址过去7天的签名信息。

只拿基础信息（签名、时间戳、slot、err），不查交易详情。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_sol_signatures.py
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

RPC_URL = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"

DIR = Path(__file__).parent / "use"
INPUT = DIR / "addresses" / "all_addresses.json"
OUTPUT = DIR / "signatures" / "sol_signatures.json"

DAYS = 7
CUTOFF = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())


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
                    wait = 2 ** attempt
                    print(f"    rate limit, 等{wait}s...", flush=True)
                    time.sleep(wait)
                    continue
                return None
            return data.get("result")
        except Exception:
            time.sleep(2)
    return None


def get_signatures(address):
    sigs = []
    before = None
    while True:
        opts = {"limit": 1000}
        if before:
            opts["before"] = before
        result = rpc_call("getSignaturesForAddress", [address, opts])
        if not result:
            break
        for r in result:
            ts = r.get("blockTime", 0)
            if ts and ts < CUTOFF:
                return sigs
            sigs.append({
                "sig": r["signature"],
                "ts": ts,
                "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                "slot": r.get("slot"),
                "err": r.get("err"),
            })
        if len(result) < 1000:
            break
        before = result[-1]["signature"]
        time.sleep(0.3)
    return sigs


def save(results, total_sigs):
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {
                "total_addresses": len(results),
                "total_signatures": total_sigs,
                "days": DAYS,
                "cutoff": datetime.fromtimestamp(CUTOFF, tz=timezone.utc).isoformat(),
                "updated": datetime.now(timezone.utc).isoformat(),
            },
            "results": results,
        }, f, indent=2, ensure_ascii=False)


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    sol_addrs = [a["address"] for a in data["solana"]]
    print(f"Solana 地址: {len(sol_addrs)} 个")
    print(f"范围: 过去{DAYS}天\n")

    # 断点续跑
    results = {}
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            results = json.load(f).get("results", {})
        print(f"已有 {len(results)} 个，跳过\n")

    total = len(sol_addrs)
    done = 0
    total_sigs = sum(len(v.get("signatures", [])) for v in results.values())

    for i, addr in enumerate(sol_addrs):
        if addr in results:
            continue

        sigs = get_signatures(addr)
        results[addr] = {"tx_count": len(sigs), "signatures": sigs}
        done += 1
        total_sigs += len(sigs)

        print(f"  [{i+1}/{total}]  {addr[:24]}...  {len(sigs):>4}笔  (累计{total_sigs})", flush=True)

        if done % 20 == 0:
            save(results, total_sigs)

        time.sleep(0.3)

    save(results, total_sigs)

    active = sum(1 for r in results.values() if r["tx_count"] > 0)
    print(f"\n{'=' * 60}")
    print(f"  总地址: {len(results)}")
    print(f"  有交易: {active}")
    print(f"  总签名: {total_sigs} 笔")
    print(f"  保存: {OUTPUT.name}")


if __name__ == "__main__":
    main()
