#!/usr/bin/env python3
"""
查询 Solana 钱包过去7天的所有交易。

快速模式（默认）：只拿签名+时间，秒完
详情模式（--detail）：逐笔查token转账详情

用法:
    python wormhole_data/wallet_sol_txs.py <钱包地址>
    python wormhole_data/wallet_sol_txs.py <钱包地址> --detail
"""

import requests
import json
import time
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

RPC_URL = "https://api.mainnet-beta.solana.com"

OUTPUT_DIR = Path(__file__).parent / "wallet_txs"
OUTPUT_DIR.mkdir(exist_ok=True)

DAYS = 7
CUTOFF = int((datetime.now(timezone.utc) - timedelta(days=DAYS)).timestamp())


def rpc_call(method, params):
    for attempt in range(3):
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
                "signature": r["signature"],
                "block_time": ts,
                "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
                "slot": r.get("slot"),
                "err": r.get("err"),
            })
        before = result[-1]["signature"]
        time.sleep(0.5)
    return sigs


def get_tx_detail(sig):
    result = rpc_call("getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
    }])
    if not result:
        return None

    tx = result.get("transaction", {})
    meta = result.get("meta", {})

    pre_balances = {}
    post_balances = {}
    for b in (meta.get("preTokenBalances") or []):
        owner = b.get("owner", "")
        mint = b.get("mint", "")
        amt = float(b.get("uiTokenAmount", {}).get("uiAmountString") or 0)
        pre_balances[(owner, mint)] = amt
    for b in (meta.get("postTokenBalances") or []):
        owner = b.get("owner", "")
        mint = b.get("mint", "")
        amt = float(b.get("uiTokenAmount", {}).get("uiAmountString") or 0)
        post_balances[(owner, mint)] = amt

    token_changes = []
    for key in set(pre_balances.keys()) | set(post_balances.keys()):
        diff = post_balances.get(key, 0) - pre_balances.get(key, 0)
        if abs(diff) > 0.000001:
            token_changes.append({"owner": key[0], "mint": key[1], "change": round(diff, 6)})

    accounts = tx.get("message", {}).get("accountKeys", [])
    pre_sol = meta.get("preBalances", [])
    post_sol = meta.get("postBalances", [])
    sol_changes = []
    for i, (pre, post) in enumerate(zip(pre_sol, post_sol)):
        diff = (post - pre) / 1e9
        if abs(diff) > 0.000001:
            acc = accounts[i] if i < len(accounts) else f"account_{i}"
            if isinstance(acc, dict):
                acc = acc.get("pubkey", f"account_{i}")
            sol_changes.append({"account": acc, "change_sol": round(diff, 6)})

    instructions = tx.get("message", {}).get("instructions", [])
    programs = []
    for ix in instructions:
        if isinstance(ix, dict):
            programs.append(ix.get("program") or ix.get("programId", ""))

    return {
        "fee_sol": (meta.get("fee") or 0) / 1e9,
        "err": meta.get("err"),
        "programs": list(set(p for p in programs if p)),
        "token_changes": token_changes,
        "sol_changes": sol_changes,
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    detail = "--detail" in sys.argv

    if not args:
        print("用法: python wormhole_data/wallet_sol_txs.py <钱包地址> [--detail]")
        sys.exit(1)

    address = args[0]
    mode = "详情" if detail else "快速"
    print(f"钱包: {address}")
    print(f"模式: {mode}")
    print(f"范围: 过去{DAYS}天\n")

    # 拿签名
    print("获取交易签名...", flush=True)
    sigs = get_signatures(address)
    print(f"  找到 {len(sigs)} 笔交易")

    if not sigs:
        print("无交易")
        return

    print(f"  最早: {sigs[-1]['time']}")
    print(f"  最新: {sigs[0]['time']}")

    # 快速模式：直接保存签名
    if not detail:
        short_addr = address[:8]
        out_path = OUTPUT_DIR / f"sol_{short_addr}.json"
        output = {
            "meta": {
                "address": address,
                "chain": "solana",
                "mode": "fast",
                "days": DAYS,
                "total_txs": len(sigs),
                "time_earliest": sigs[-1]["time"],
                "time_latest": sigs[0]["time"],
            },
            "transactions": sigs,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        size_mb = out_path.stat().st_size / 1024 / 1024
        print(f"\n保存: {out_path.name} ({size_mb:.1f} MB)")
        print(f"\n提示: 加 --detail 可查每笔交易的token转账详情")
        return

    # 详情模式：逐笔查
    print(f"\n获取交易详情 ({len(sigs)}笔)...", flush=True)
    records = []
    for i, sig_info in enumerate(sigs):
        record = dict(sig_info)
        detail_data = get_tx_detail(sig_info["signature"])
        if detail_data:
            record.update(detail_data)
        records.append(record)
        if (i + 1) % 10 == 0 or i == len(sigs) - 1:
            print(f"    {i+1}/{len(sigs)}", flush=True)
        time.sleep(0.3)

    short_addr = address[:8]
    out_path = OUTPUT_DIR / f"sol_{short_addr}_detail.json"
    output = {
        "meta": {
            "address": address,
            "chain": "solana",
            "mode": "detail",
            "days": DAYS,
            "total_txs": len(records),
            "time_earliest": records[-1]["time"] if records else None,
            "time_latest": records[0]["time"] if records else None,
        },
        "transactions": records,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"\n保存: {out_path.name} ({size_mb:.1f} MB)")

    # 摘要
    for r in records:
        token_str = ""
        for tc in r.get("token_changes", []):
            if tc["owner"] == address:
                token_str += f" {tc['mint'][:8]}:{tc['change']:+.2f}"
        print(f"  {r['time']}  {token_str or '(无token变化)'}")


if __name__ == "__main__":
    main()
