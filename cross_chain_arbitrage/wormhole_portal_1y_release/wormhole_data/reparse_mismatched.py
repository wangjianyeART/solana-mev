#!/usr/bin/env python3
"""
对 235 条 hash_mismatch 记录做定向重解析:
  1. 把 eth_hash 换成 Wormholescan 返回的 canonical ETH hash
  2. 重新解析该 ETH tx  (eth_parsed, eth_from, eth_ts, eth_block ...)
  3. 基于新的 eth_from + eth_ts 重拉 ETH context (Etherscan ±1h 窗口)
  4. 并发解析 context tx
  5. 同步写回 matched.json / matched_context.json / matched_context_parsed.json
SOL 侧不变. 后续再跑 detect_arbitrage → compute_pnl.
"""

import json
import time
import shutil
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

import batch_parse_context as bpc  # 复用解析器

BASE = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
ARB = BASE / "arbitrage"

CLASSIFIED = ARB / "chain_verification_classified.json"
MATCHED = BASE / "matched.json"
CONTEXT = BASE / "matched_context.json"
PARSED = BASE / "matched_context_parsed.json"

ETHERSCAN_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"
CONTEXT_SIZE = 10  # 前后各 10 笔
BRIDGE_OP_HASHES = {
    # Wormhole Portal Bridge 主合约, 新发现时可补充; 影响很小 — 过滤关联 tx 时用
}

BACKUP_SUFFIX = ".bak_before_reparse235"


def _estimate_block(ts):
    ref_ts = 1776336191
    ref_block = 24891861
    return max(0, ref_block + int((ts - ref_ts) / 12))


def fetch_eth_tx_window(addr, ts, pad=3600):
    """Etherscan 取 addr 在 [ts-pad, ts+pad] 窗口内所有 tx (normal + token)."""
    addr = addr.lower()
    start_block = _estimate_block(ts - pad)
    end_block = _estimate_block(ts + pad)
    out = {}
    for action in ["txlist", "tokentx"]:
        url = (f"https://api.etherscan.io/v2/api?chainid=1&module=account"
               f"&action={action}&address={addr}"
               f"&startblock={start_block}&endblock={end_block}"
               f"&sort=asc&apikey={ETHERSCAN_KEY}")
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=30)
                d = r.json()
                if d.get("status") == "1" and isinstance(d.get("result"), list):
                    for tx in d["result"]:
                        h = tx.get("hash", "")
                        t = int(tx.get("timeStamp", 0))
                        if h and t and h not in out:
                            out[h] = {
                                "hash": h, "ts": t,
                                "blockNumber": tx.get("blockNumber", ""),
                                "from": tx.get("from", ""),
                                "to": tx.get("to", ""),
                                "methodId": tx.get("methodId", ""),
                            }
                break
            except Exception:
                time.sleep(1)
        time.sleep(0.25)
    txs = sorted(out.values(), key=lambda x: x["ts"])
    return txs


def build_context_window(txs, primary_hash, primary_ts):
    """取 primary_ts 前后各 CONTEXT_SIZE 笔, 不含 primary 自身."""
    before = [t for t in txs if t["ts"] < primary_ts and t["hash"] != primary_hash][-CONTEXT_SIZE:]
    after = [t for t in txs if t["ts"] > primary_ts and t["hash"] != primary_hash][:CONTEXT_SIZE]
    return before + after


def parse_primary(api_hash, eth_from_hint):
    """解析 canonical 桥接 tx. 若 eth_from_hint 为 None, 用 tx.from 作为 perspective."""
    target = eth_from_hint if eth_from_hint else None
    r = bpc.parse_eth_tx(api_hash, target_addr=target)
    return r


def parse_context(hash_):
    """解析 context tx — 不指定 perspective, 让 bpc 用 tx.from 作为默认."""
    return bpc.parse_eth_tx(hash_, target_addr=None)


def main():
    bpc.load_erc20_cache()

    hm = json.load(open(CLASSIFIED, encoding="utf-8"))["hash_mismatch"]
    print(f"hash_mismatch: {len(hm)}")
    by_sig = {r["sig"]: r for r in hm}

    # 第 1 步: 并发解析 235 条 canonical ETH tx
    print("\n== Step 1: 解析 canonical ETH tx ==")
    primary_cache = {}
    t0 = time.time()

    def _do_primary(entry):
        sig, api_hash = entry["sig"], entry["api"]
        return sig, api_hash, bpc.parse_eth_tx(api_hash, target_addr=None)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_do_primary, r): r for r in hm}
        done = err = 0
        for f in as_completed(futs):
            sig, api_hash, parsed = f.result()
            primary_cache[sig] = {"api_hash": api_hash, "parsed": parsed}
            done += 1
            if not parsed or parsed.get("error"):
                err += 1
            if done % 50 == 0:
                print(f"  {done}/{len(hm)} err={err} ({done/(time.time()-t0):.1f}/s)")
    print(f"primary 完成: {done}, errors={err}, {time.time()-t0:.1f}s")

    # 从 parsed 提取 new_eth_from (tx.from), new_eth_ts, new_eth_block
    enriched = {}
    for sig, info in primary_cache.items():
        p = info["parsed"]
        if not p or p.get("error"):
            enriched[sig] = None
            continue
        # tx ts 可能在 blockTimestamp logs 里取到, 没有时 fallback 0
        ts = p.get("ts") or 0
        if not ts:
            # 如果没有 blockTimestamp, 调 eth_getBlockByNumber 补
            blk = p.get("block")
            if blk:
                b = bpc._eth_rpc("eth_getBlockByNumber", [hex(blk), False])
                if b:
                    ts = int(b.get("timestamp", "0x0"), 16)
        enriched[sig] = {
            "hash": info["api_hash"],
            "from": p.get("from", ""),
            "ts": ts,
            "block": p.get("block", 0),
            "fee_eth": p.get("fee_eth", 0),
            "gas_used": p.get("gas_used", 0),
            "status": p.get("status"),
            "events": p.get("events", []),
            "summary": p.get("summary", {}),
            "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
        }
    n_ok = sum(1 for v in enriched.values() if v)
    print(f"enriched: {n_ok}/{len(enriched)} 可用")

    # 第 2 步: 按 eth_from 分组拉 context
    print("\n== Step 2: 拉 ETH context (Etherscan) ==")
    eth_ctx_map = {}  # sig -> list of context tx dicts (unparsed)
    addr_groups = {}  # addr -> [(sig, ts)]
    for sig, info in enriched.items():
        if not info or not info["from"]:
            continue
        addr_groups.setdefault(info["from"].lower(), []).append((sig, info["ts"]))

    print(f"唯一 eth_from: {len(addr_groups)}")
    t1 = time.time()
    all_ctx_hashes = set()

    for i, (addr, items) in enumerate(addr_groups.items(), 1):
        min_ts = min(t for _, t in items)
        max_ts = max(t for _, t in items)
        txs = fetch_eth_tx_window(addr, (min_ts + max_ts) // 2,
                                  pad=max(3600, (max_ts - min_ts) // 2 + 3600))
        for sig, ts in items:
            primary_hash = enriched[sig]["hash"]
            win = build_context_window(txs, primary_hash, ts)
            eth_ctx_map[sig] = win
            for t in win:
                all_ctx_hashes.add(t["hash"])
        if i % 20 == 0:
            print(f"  {i}/{len(addr_groups)} addr, ctx_hashes={len(all_ctx_hashes)}")
    print(f"context 拉取完成: {len(addr_groups)} addr, {len(all_ctx_hashes)} 唯一 ctx hash, {time.time()-t1:.1f}s")

    # 第 3 步: 并发解析 context tx
    print("\n== Step 3: 解析 ETH context tx ==")
    ctx_parsed = {}
    t2 = time.time()
    def _do_ctx(h):
        return h, bpc.parse_eth_tx(h, target_addr=None)
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = {pool.submit(_do_ctx, h): h for h in all_ctx_hashes}
        done = err = 0
        for f in as_completed(futs):
            h, parsed = f.result()
            ctx_parsed[h] = parsed
            done += 1
            if not parsed or parsed.get("error"):
                err += 1
            if done % 200 == 0:
                print(f"  {done}/{len(all_ctx_hashes)} err={err} ({done/(time.time()-t2):.1f}/s)")
    print(f"context 解析完成: {done}, err={err}, {time.time()-t2:.1f}s")

    # 第 4 步: 写回三个文件
    print("\n== Step 4: 写回 matched.json / context.json / parsed.json ==")

    # 备份
    for p in (MATCHED, CONTEXT, PARSED):
        shutil.copy2(p, str(p) + BACKUP_SUFFIX)

    # matched.json
    m = json.load(open(MATCHED, encoding="utf-8"))
    updated_m = 0
    for r in m["records"]:
        sig = r.get("sol_sig")
        if sig in enriched and enriched[sig]:
            info = enriched[sig]
            r["eth_hash"] = info["hash"]
            r["eth_time"] = info["time"]
            if info["ts"] and r.get("sol_ts"):
                r["time_diff_sec"] = info["ts"] - r["sol_ts"]
            elif info["ts"]:
                # 从 sol_time 算
                try:
                    sts = int(datetime.fromisoformat(r["sol_time"]).timestamp())
                    r["time_diff_sec"] = info["ts"] - sts
                except Exception:
                    pass
            r["match_type"] = (r.get("match_type") or "known_pair") + "_chain_corrected"
            updated_m += 1
    json.dump(m, open(MATCHED, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  matched.json: 更新 {updated_m}")

    # matched_context.json
    ctx = json.load(open(CONTEXT, encoding="utf-8"))
    updated_c = 0
    for r in ctx["records"]:
        sig = r.get("sol_sig")
        if sig not in enriched or not enriched[sig]:
            continue
        info = enriched[sig]
        r["eth_hash"] = info["hash"]
        r["eth_from"] = info["from"]
        r["eth_time"] = info["time"]
        r["eth_ts"] = info["ts"]
        r["eth_block"] = info["block"]
        r["eth_fee_eth"] = info["fee_eth"]
        r["eth_gas_used"] = info["gas_used"]
        # 重建 eth_context — bare 版 (hash/ts/blockNumber/from/to/methodId)
        new_ctx = []
        for t in eth_ctx_map.get(sig, []):
            new_ctx.append({
                "hash": t["hash"],
                "timeStamp": str(t["ts"]),
                "blockNumber": t.get("blockNumber", ""),
                "from": t.get("from", ""),
                "to": t.get("to", ""),
                "methodId": t.get("methodId", ""),
            })
        r["eth_context"] = new_ctx
        # eth_context_pos: 我们把 primary 自己不放进 context, pos = len(before) — 重新算
        r["eth_context_pos"] = sum(1 for t in eth_ctx_map.get(sig, []) if t["ts"] < info["ts"])
        updated_c += 1
    json.dump(ctx, open(CONTEXT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  matched_context.json: 更新 {updated_c}")

    # matched_context_parsed.json
    parsed_doc = json.load(open(PARSED, encoding="utf-8"))
    updated_p = 0
    for r in parsed_doc["records"]:
        sig = r.get("sol_sig")
        if sig not in enriched or not enriched[sig]:
            continue
        info = enriched[sig]
        r["eth_hash"] = info["hash"]
        r["eth_from"] = info["from"]
        r["eth_time"] = info["time"]
        r["eth_ts"] = info["ts"]
        r["eth_block"] = info["block"]
        r["eth_fee_eth"] = info["fee_eth"]
        r["eth_gas_used"] = info["gas_used"]
        # eth_parsed
        r["eth_parsed"] = primary_cache[sig]["parsed"]
        # eth_context — 包含 parsed
        new_ctx = []
        for t in eth_ctx_map.get(sig, []):
            entry = {
                "hash": t["hash"],
                "timeStamp": str(t["ts"]),
                "blockNumber": t.get("blockNumber", ""),
                "from": t.get("from", ""),
                "to": t.get("to", ""),
                "methodId": t.get("methodId", ""),
            }
            cp = ctx_parsed.get(t["hash"])
            if cp and not cp.get("error"):
                entry["parsed"] = cp
            new_ctx.append(entry)
        r["eth_context"] = new_ctx
        r["eth_context_pos"] = sum(1 for t in eth_ctx_map.get(sig, []) if t["ts"] < info["ts"])
        updated_p += 1

    # 更新 meta 统计
    parsed_doc["meta"]["eth_parsed"] = sum(1 for r in parsed_doc["records"]
                                           if r.get("eth_parsed") and not r["eth_parsed"].get("error"))
    parsed_doc["meta"]["eth_errors"] = sum(1 for r in parsed_doc["records"]
                                           if not r.get("eth_parsed") or r["eth_parsed"].get("error"))
    parsed_doc["meta"]["reparsed_mismatched"] = updated_p
    parsed_doc["meta"]["updated"] = datetime.now(timezone.utc).isoformat()

    json.dump(parsed_doc, open(PARSED, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  matched_context_parsed.json: 更新 {updated_p}")

    print("\n完成. 原文件已备份 (*.bak_before_reparse235)")
    print(f"下一步: rerun detect_arbitrage.py → tag_subtypes → apply_greedy_dedup → compute_pnl")


if __name__ == "__main__":
    main()
