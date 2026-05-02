#!/usr/bin/env python3
"""
批量获取 Portal/NTT 中所有 Solana token account 过去7天的签名信息。

从 wormhole_portal.json 和 wormhole_ntt.json 提取所有 dst_receiver (token account)，
对每个调用 getSignaturesForAddress 获取基础签名（时间、slot、err）。
边跑边保存，支持断点续跑。

用法:
    python wormhole_data/fetch_all_signatures.py
"""

import requests
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

RPC_URL = "https://api.mainnet-beta.solana.com"

DIR = Path(__file__).parent / "use"
PORTAL = DIR / "bridge_records" / "wormhole_portal.json"
NTT = DIR / "bridge_records" / "wormhole_ntt.json"
OUTPUT = DIR / "token_account_signatures.json"

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
    """获取地址过去7天的所有签名"""
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
        time.sleep(0.5)
    return sigs


def main():
    # 从 Portal + NTT 提取所有 Solana dst_receiver (token account)
    token_accounts = set()
    for fpath in [PORTAL, NTT]:
        if not fpath.exists():
            print(f"跳过: {fpath.name} 不存在")
            continue
        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)
        for r in data["records"]:
            dst = r.get("dst_receiver", "")
            if dst and not dst.startswith("0x"):
                token_accounts.add(dst)

    print(f"Portal+NTT token account: {len(token_accounts)} 个")
    print(f"范围: 过去{DAYS}天 (截止 {datetime.fromtimestamp(CUTOFF, tz=timezone.utc).isoformat()})")
    print()

    # 加载已有结果（断点续跑）
    results = {}
    if OUTPUT.exists():
        with open(OUTPUT, encoding="utf-8") as f:
            results = json.load(f).get("results", {})
        print(f"已有 {len(results)} 个地址的结果，跳过\n")

    total = len(token_accounts)
    done = 0
    total_sigs = sum(len(v.get("signatures", [])) for v in results.values())

    for i, addr in enumerate(sorted(token_accounts)):
        if addr in results:
            continue

        sigs = get_signatures(addr)
        results[addr] = {
            "tx_count": len(sigs),
            "signatures": sigs,
        }
        done += 1
        total_sigs += len(sigs)

        progress = i + 1
        if done % 5 == 0 or progress == total:
            print(f"  [{progress}/{total}]  本次查{done}个  共{total_sigs}笔  "
                  f"{addr[:20]}... = {len(sigs)}笔", flush=True)

        # 每20个保存一次
        if done % 20 == 0:
            with open(OUTPUT, "w", encoding="utf-8") as f:
                json.dump({
                    "meta": {"total_accounts": len(results), "total_signatures": total_sigs,
                             "days": DAYS, "updated": datetime.now(timezone.utc).isoformat()},
                    "results": results,
                }, f, indent=2, ensure_ascii=False)

        time.sleep(0.3)

    # 最终保存
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {"total_accounts": len(results), "total_signatures": total_sigs,
                     "days": DAYS, "updated": datetime.now(timezone.utc).isoformat()},
            "results": results,
        }, f, indent=2, ensure_ascii=False)

    active = sum(1 for r in results.values() if r["tx_count"] > 0)
    print(f"\n{'=' * 60}")
    print(f"  总token account: {len(results)}")
    print(f"  有交易: {active}")
    print(f"  总签名: {total_sigs} 笔")
    print(f"  保存: {OUTPUT.name}")

    print(f"\nTop 20 活跃token account:")
    top = sorted(results.items(), key=lambda x: -x[1]["tx_count"])
    for addr, r in top[:20]:
        print(f"  {addr[:32]}...  {r['tx_count']:>5}笔")


if __name__ == "__main__":
    main()
