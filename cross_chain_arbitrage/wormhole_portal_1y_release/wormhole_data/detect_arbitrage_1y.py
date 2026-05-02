#!/usr/bin/env python3
"""
Stage 7 (1y): 跨链套利结构候选检测 (无价格阶段)

输入:
  use/portal_full/recent_1y/matched/matched_context.json     (113K records, 取 both_ok 子集)
  use/portal_full/recent_1y/matched/context_parse_cache/sol_parsed.jsonl
  use/portal_full/recent_1y/matched/context_parse_cache/eth_parsed.jsonl

输出 (双轨):
  use/portal_full/recent_1y/matched/arbitrage/arbitrage_candidates_{pct}pct.json         # strict (round-trip)
  use/portal_full/recent_1y/matched/arbitrage/arbitrage_candidates_{pct}pct_loose.json   # one-way 也算

  阈值: 0.01 / 0.05 / 0.10 / 0.20

判定:
  - strict: entry swap (买入 bridge token) AND exit swap (卖出 bridge token) 同时存在
  - loose:  任一存在即可

actor 匹配:
  - SOL: parsed.signer == sol_sender
  - ETH: tx.from / tx.to / 任一 event 命中 eth_from (loose)

bridge_identity 直接从 matched record 的 sol_user_change/eth_transfer 拿。
"""

import json
import os
import time
from pathlib import Path
from collections import Counter

RECENT = Path(__file__).parent / "use" / "portal_full" / "recent_1y"
RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
ROOT = RECENT / (f"matched_{RUN_TAG}" if RUN_TAG else "matched")
BASE_ROOT = RECENT / "matched"
INPUT = ROOT / "matched_context.json"
SOL_JSONL = BASE_ROOT / "context_parse_cache" / "sol_parsed.jsonl"
ETH_JSONL = BASE_ROOT / "context_parse_cache" / "eth_parsed.jsonl"
OUT_DIR = ROOT / "arbitrage"
OUT_DIR.mkdir(parents=True, exist_ok=True)

THRESHOLDS = [0.01, 0.05, 0.10, 0.20]
MODES = ["strict", "loose"]


# ────────────── load JSONL → dict (last-wins for retry) ──────────────

SOL_KEEP = {"sig", "ts", "slot", "signer", "programs", "summary",
            "token_changes", "fee_sol", "error"}
ETH_KEEP = {"hash", "ts", "block", "status", "from", "to",
            "events", "summary", "fee_eth", "error"}


def load_jsonl_dict(path, key_field, keep_fields):
    print(f"加载 {path.name} (~{path.stat().st_size/1024/1024:.0f} MB) ...")
    t0 = time.time()
    out = {}
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            k = obj.get(key_field)
            if not k:
                continue
            pruned = {kk: obj[kk] for kk in keep_fields if kk in obj}
            out[k] = pruned  # last-wins
            n += 1
            if n % 200000 == 0:
                print(f"  {n:,} lines loaded, {len(out):,} unique  ({time.time()-t0:.0f}s)")
    print(f"  完成: {n:,} 行 → {len(out):,} unique  ({time.time()-t0:.0f}s)")
    return out


# ────────────── swap 判定 ──────────────

def is_swap_sol(p):
    s = p.get("summary") or {}
    return bool(s.get("sold")) and bool(s.get("bought"))


def is_swap_eth(p, eth_from):
    addr = (eth_from or "").lower()
    if not addr:
        return False
    out_keys, in_keys = set(), set()
    for e in p.get("events", []):
        amt = e.get("amount", 0) or 0
        if amt <= 0:
            continue
        frm = (e.get("from") or "").lower()
        to = (e.get("to") or "").lower()
        key = (e.get("type"), (e.get("contract") or "ETH").lower())
        if frm == addr:
            out_keys.add(key)
        if to == addr:
            in_keys.add(key)
    if not out_keys or not in_keys:
        return False
    return len(out_keys | in_keys) >= 2


def sol_actor_match(p, sol_sender):
    return p.get("signer") == sol_sender


def eth_actor_match_loose(p, eth_from):
    addr = (eth_from or "").lower()
    if not addr:
        return False
    if (p.get("from") or "").lower() == addr:
        return True
    for e in p.get("events", []):
        if (e.get("from") or "").lower() == addr:
            return True
        if (e.get("to") or "").lower() == addr:
            return True
    return False


def sol_bridge_flow(p, spl_mint, sol_sender):
    total = 0.0
    for tc in p.get("token_changes", []):
        if tc.get("mint") == spl_mint and tc.get("owner") == sol_sender:
            total += tc.get("change", 0) or 0
    return total


def eth_bridge_flow(p, erc20_contract, eth_from):
    addr = (eth_from or "").lower()
    contract = (erc20_contract or "").lower()
    net = 0.0
    for e in p.get("events", []):
        if e.get("type") != "erc20_transfer":
            continue
        if (e.get("contract") or "").lower() != contract:
            continue
        amt = e.get("amount", 0) or 0
        if (e.get("to") or "").lower() == addr:
            net += amt
        if (e.get("from") or "").lower() == addr:
            net -= amt
    return net


# ────────────── 扫 context (1y schema) ──────────────

def scan_sol_side(context_items, sol_parsed_db, spl_mint, sol_sender,
                  bridge_ts, bridge_slot, side, bridge_amount):
    out = []
    for item in context_items:
        sig = item.get("sig")
        if not sig:
            continue
        t = item.get("ts")
        if not t:
            continue
        if side == "entry" and t > bridge_ts:
            continue
        if side == "exit" and t < bridge_ts:
            continue
        p = sol_parsed_db.get(sig)
        if not p or p.get("error"):
            continue
        if not sol_actor_match(p, sol_sender):
            continue
        if not is_swap_sol(p):
            continue
        flow = sol_bridge_flow(p, spl_mint, sol_sender)
        if side == "entry" and flow <= 0:
            continue
        if side == "exit" and flow >= 0:
            continue
        amt = abs(flow)
        slot = p.get("slot")
        out.append({
            "tx": sig, "chain": "SOL", "ts": t, "slot": slot,
            "bridge_token_amount": amt,
            "coverage": amt / bridge_amount if bridge_amount else 0,
            "delta_bridge_sec": (bridge_ts - t) if side == "entry" else (t - bridge_ts),
            "atomic_same_block": bool(bridge_slot and slot == bridge_slot),
            "sold": (p.get("summary") or {}).get("sold", []),
            "bought": (p.get("summary") or {}).get("bought", []),
            "programs": p.get("programs", []),
            "fee_sol": p.get("fee_sol", 0),
        })
    return out


def scan_eth_side(context_items, eth_parsed_db, erc20_contract, eth_from,
                  bridge_ts, bridge_block, side, bridge_amount):
    out = []
    for item in context_items:
        h = item.get("hash")
        if not h:
            continue
        t = item.get("ts")
        if not t:
            continue
        if side == "entry" and t > bridge_ts:
            continue
        if side == "exit" and t < bridge_ts:
            continue
        p = eth_parsed_db.get(h)
        if not p or p.get("error"):
            continue
        if not eth_actor_match_loose(p, eth_from):
            continue
        if not is_swap_eth(p, eth_from):
            continue
        flow = eth_bridge_flow(p, erc20_contract, eth_from)
        if side == "entry" and flow <= 0:
            continue
        if side == "exit" and flow >= 0:
            continue
        amt = abs(flow)
        block = p.get("block")
        out.append({
            "tx": h, "chain": "ETH", "ts": t, "block": block,
            "bridge_token_amount": amt,
            "coverage": amt / bridge_amount if bridge_amount else 0,
            "delta_bridge_sec": (bridge_ts - t) if side == "entry" else (t - bridge_ts),
            "atomic_same_block": bool(bridge_block and block == bridge_block),
            "sold": (p.get("summary") or {}).get("sold", []),
            "bought": (p.get("summary") or {}).get("bought", []),
            "from": p.get("from"), "to": p.get("to"),
            "fee_eth": p.get("fee_eth", 0),
        })
    return out


# ────────────── process record ──────────────

def process_record(r, sol_db, eth_db):
    direction = r.get("direction")
    if direction not in ("SOL→ETH", "ETH→SOL"):
        return {"skip": "unknown_direction"}

    sol_sender = r.get("sol_sender")
    eth_from = r.get("eth_from")
    sol_ts = r.get("sol_ts")
    eth_ts = r.get("eth_ts")
    sol_sig = r.get("sol_sig")
    eth_hash = r.get("eth_hash")

    if not (sol_ts and eth_ts and sol_sender and eth_from):
        return {"skip": "missing_bridge_header"}

    suc = r.get("sol_user_change") or {}
    # New matched records carry both mint and display symbol. Older records only
    # carried symbol; for unmapped long-tail tokens symbol often equals mint.
    spl_mint = suc.get("mint") or suc.get("symbol")
    sol_amount = abs(suc.get("change") or 0)

    et = r.get("eth_transfer") or {}
    erc20_contract = et.get("contract") or et.get("token")
    eth_amount = abs(et.get("amount") or 0)

    if not spl_mint or not erc20_contract:
        return {"skip": "missing_bridge_identity"}
    if sol_amount <= 0:
        return {"skip": "missing_bridge_amount"}

    # bridge tx own slot/block (from parsed jsonl)
    bridge_sol = sol_db.get(sol_sig) or {}
    bridge_eth = eth_db.get(eth_hash) or {}
    sol_slot = bridge_sol.get("slot")
    eth_block = bridge_eth.get("block")

    if direction == "SOL→ETH":
        entry_raw = scan_sol_side(
            r.get("sol_context", []), sol_db,
            spl_mint, sol_sender, sol_ts, sol_slot, "entry", sol_amount)
        exit_raw = scan_eth_side(
            r.get("eth_context", []), eth_db,
            erc20_contract, eth_from, eth_ts, eth_block, "exit",
            eth_amount or sol_amount)
    else:
        entry_raw = scan_eth_side(
            r.get("eth_context", []), eth_db,
            erc20_contract, eth_from, eth_ts, eth_block, "entry",
            eth_amount or sol_amount)
        exit_raw = scan_sol_side(
            r.get("sol_context", []), sol_db,
            spl_mint, sol_sender, sol_ts, sol_slot, "exit", sol_amount)

    return {
        "id": f"{direction}|{(sol_sig or '')[:16]}|{(eth_hash or '')[:16]}",
        "direction": direction,
        "sol_sig": sol_sig, "eth_hash": eth_hash,
        "sol_ts": sol_ts, "eth_ts": eth_ts,
        "sol_slot": sol_slot, "eth_block": eth_block,
        "arbitrageur": {"sol": sol_sender, "eth": eth_from},
        "bridge": {
            "spl_mint": spl_mint,
            "erc20_contract": erc20_contract,
            "sol_amount": sol_amount,
            "eth_amount": eth_amount,
        },
        "latency_seconds": r.get("latency_seconds"),
        "sol_fee_sol": bridge_sol.get("fee_sol"),
        "eth_fee_eth": bridge_eth.get("fee_eth"),
        "_entry_raw": entry_raw,
        "_exit_raw": exit_raw,
    }


# ────────────── apply threshold (双模式) ──────────────

def apply_threshold(raw, thr, mode):
    entry_f = [s for s in raw["_entry_raw"] if s["coverage"] >= thr]
    exit_f = [s for s in raw["_exit_raw"] if s["coverage"] >= thr]

    if mode == "strict":
        if not entry_f or not exit_f:
            return None
    else:  # loose
        if not entry_f and not exit_f:
            return None

    entry_cov = sum(s["coverage"] for s in entry_f)
    exit_cov = sum(s["coverage"] for s in exit_f)
    entry_full = 0.8 <= entry_cov <= 1.2 if entry_f else False
    exit_full = 0.8 <= exit_cov <= 1.2 if exit_f else False

    if mode == "strict":
        classification = "arbitrage_full" if (entry_full and exit_full) else "arbitrage_partial_coverage"
    else:
        if entry_f and exit_f:
            classification = "arbitrage_full" if (entry_full and exit_full) else "arbitrage_partial_coverage"
        elif entry_f:
            classification = "one_way_entry_only"
        else:
            classification = "one_way_exit_only"

    flags = []
    if any(s.get("atomic_same_block") for s in entry_f):
        flags.append("atomic_entry_same_block")
    if any(s.get("atomic_same_block") for s in exit_f):
        flags.append("atomic_exit_same_block")
    if "atomic_entry_same_block" in flags and "atomic_exit_same_block" in flags:
        flags.append("fully_atomic")
    deltas = [s.get("delta_bridge_sec", 10**9) for s in entry_f + exit_f]
    if deltas:
        md = min(deltas)
        if md <= 60:
            flags.append("quick_round_trip_60s")
        elif md <= 300:
            flags.append("quick_round_trip_300s")

    rec = {k: v for k, v in raw.items() if not k.startswith("_")}
    rec["entry_swaps"] = entry_f
    rec["exit_swaps"] = exit_f
    rec["entry_coverage"] = entry_cov
    rec["exit_coverage"] = exit_cov
    rec["classification"] = classification
    rec["flags"] = flags
    return rec


# ────────────── main ──────────────

def main():
    print("=" * 60)
    print("Stage 7 (1y): Arbitrage candidate detection")
    print("=" * 60)

    print(f"\n[1/4] 加载 {INPUT.name} ...")
    t0 = time.time()
    d = json.load(open(INPUT, encoding="utf-8"))
    records = d["records"]
    print(f"  total records: {len(records):,}  ({time.time()-t0:.0f}s)")

    both_ok = [r for r in records
               if r.get("sol_context_flag") is None and r.get("eth_context_flag") is None]
    print(f"  both_ok subset: {len(both_ok):,}")

    print(f"\n[2/4] 加载 sol_parsed.jsonl (1.5GB+ in RAM est) ...")
    sol_db = load_jsonl_dict(SOL_JSONL, "sig", SOL_KEEP)

    print(f"\n[3/4] 加载 eth_parsed.jsonl (10GB+ in RAM est) ...")
    eth_db = load_jsonl_dict(ETH_JSONL, "hash", ETH_KEEP)

    print(f"\n[4/4] 处理 records ...")
    t1 = time.time()
    raw_results = []
    skip_stats = Counter()
    for i, r in enumerate(both_ok):
        res = process_record(r, sol_db, eth_db)
        if "skip" in res:
            skip_stats[res["skip"]] += 1
            continue
        raw_results.append(res)
        if (i + 1) % 10000 == 0:
            print(f"  {i+1:,}/{len(both_ok):,} processed  raws={len(raw_results):,}  ({time.time()-t1:.0f}s)")
    print(f"  完成 ({time.time()-t1:.0f}s): raw={len(raw_results):,}  skip={dict(skip_stats)}")

    # 统计 1% 双侧覆盖
    both_at_1pct = sum(
        1 for r in raw_results
        if any(s["coverage"] >= 0.01 for s in r["_entry_raw"])
        and any(s["coverage"] >= 0.01 for s in r["_exit_raw"])
    )
    print(f"  1% 双侧 swap 都有: {both_at_1pct:,}")

    # 释放 db (后面只用 raw_results)
    del sol_db, eth_db

    # 输出 (双模式 × 4 阈值 = 8 文件)
    print(f"\n生成候选文件 ...")
    summary = {}
    for mode in MODES:
        for thr in THRESHOLDS:
            thr_pct = int(round(thr * 100))
            filtered = []
            stats = Counter()
            flag_stats = Counter()
            dir_stats = Counter()
            for raw in raw_results:
                rec = apply_threshold(raw, thr, mode)
                if not rec:
                    continue
                filtered.append(rec)
                stats[rec["classification"]] += 1
                dir_stats[rec["direction"]] += 1
                for f in rec["flags"]:
                    flag_stats[f] += 1

            suffix = "" if mode == "strict" else "_loose"
            out = {
                "meta": {
                    "mode": mode,
                    "threshold": thr,
                    "input": INPUT.name,
                    "total_records": len(records),
                    "both_ok": len(both_ok),
                    "raw_with_bridge_identity": len(raw_results),
                    "total_candidates": len(filtered),
                    "classification_stats": dict(stats),
                    "direction_stats": dict(dir_stats),
                    "flag_stats": dict(flag_stats),
                },
                "candidates": filtered,
            }
            fn = OUT_DIR / f"arbitrage_candidates_{thr_pct}pct{suffix}.json"
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
            sz = fn.stat().st_size / 1024 / 1024
            line = f"  [{mode:6s}] thr={thr_pct:2d}%: {len(filtered):>6,} 条  ({sz:.1f} MB)  → {fn.name}"
            summary[fn.name] = {"count": len(filtered), "stats": dict(stats), "dir": dict(dir_stats)}
            print(line)

    print(f"\n总耗时: {time.time()-t0:.0f}s")
    print(f"输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
