#!/usr/bin/env python3
"""
Step 1: 跨链套利结构候选检测（不涉及价格）

输入:  matched_context_parsed.json
输出:  arbitrage/arbitrage_candidates_{pct}pct.json  (多阈值)

硬门槛:
  - 源链 context 必须有 swap 买入跨链币（entry）
  - 目标链 context 必须有 swap 卖出跨链币（exit）
  - 缺一不可（否则无法计算利润）

时间: 允许 <= / >= (同区块也算, 加 atomic_*_same_block 标记)
ETH actor: 宽松判定 (from/to 任一 event 涉及 eth_from 即可)
"""

import json
from pathlib import Path

DIR = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched"
INPUT = DIR / "matched_context_parsed.json"
OUT_DIR = DIR / "arbitrage"
OUT_DIR.mkdir(parents=True, exist_ok=True)

THRESHOLDS = [0.01, 0.05, 0.10, 0.20]
AMOUNT_TOLERANCE = 0.02  # 桥接币身份识别时的 amount 匹配容差


# ─────────────────────────── 提取跨链币身份 ───────────────────────────

def extract_bridge_spl(sol_parsed, sol_sender, sol_amount):
    """从 bridge tx 的 SOL parsed 数据找出跨链 SPL mint"""
    if not sol_parsed or sol_parsed.get("error"):
        return None
    best = None
    best_diff = 1.0
    fallback = None
    fallback_amt = 0
    for tc in sol_parsed.get("token_changes", []):
        if tc.get("owner") != sol_sender:
            continue
        amt = abs(tc.get("change", 0) or 0)
        if amt <= 0:
            continue
        if sol_amount and sol_amount > 0:
            diff = abs(amt - sol_amount) / max(amt, sol_amount)
            if diff < AMOUNT_TOLERANCE and diff < best_diff:
                best = tc.get("mint")
                best_diff = diff
        if amt > fallback_amt:
            fallback = tc.get("mint")
            fallback_amt = amt
    return best or fallback


def extract_bridge_erc20(eth_parsed, eth_from, eth_amount):
    """从 bridge tx 的 ETH parsed 数据找出跨链 ERC20 合约"""
    if not eth_parsed or eth_parsed.get("error"):
        return None
    addr = (eth_from or "").lower()
    best = None
    best_diff = 1.0
    fallback = None
    fallback_amt = 0
    for e in eth_parsed.get("events", []):
        if e.get("type") != "erc20_transfer":
            continue
        frm = (e.get("from") or "").lower()
        to = (e.get("to") or "").lower()
        if addr and addr not in (frm, to):
            continue
        amt = e.get("amount", 0) or 0
        if amt <= 0:
            continue
        if eth_amount and eth_amount > 0:
            diff = abs(amt - eth_amount) / max(amt, eth_amount)
            if diff < AMOUNT_TOLERANCE and diff < best_diff:
                best = e.get("contract")
                best_diff = diff
        if amt > fallback_amt:
            fallback = e.get("contract")
            fallback_amt = amt
    return best or fallback


# ─────────────────────────── actor 匹配 ───────────────────────────

def sol_actor_match(p, sol_sender):
    return p.get("signer") == sol_sender


def eth_actor_match_loose(p, eth_from):
    """宽松: tx.from 或 任一 event 的 from/to 命中 eth_from"""
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


# ─────────────────────────── swap 判定 ───────────────────────────

def is_swap_sol(p):
    """SOL swap: summary 同时有 sold 和 bought"""
    s = p.get("summary") or {}
    return bool(s.get("sold")) and bool(s.get("bought"))


def is_swap_eth(p, eth_from):
    """ETH swap: actor 至少送出一种 token 且收到另一种（不同 token）"""
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


# ─────────────────────────── 计算 actor 在 tx 中对 bridge token 的净流入/流出 ───────────────────────────

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


# ─────────────────────────── 扫 context ───────────────────────────

def scan_sol_side(context, spl_mint, sol_sender, bridge_ts, bridge_slot, side, bridge_amount):
    """side='entry' 时间<=bridge 净流入>0; side='exit' 时间>=bridge 净流出>0"""
    out = []
    for ctx in context:
        t = ctx.get("blockTime")
        if not t:
            continue
        if side == "entry" and t > bridge_ts:
            continue
        if side == "exit" and t < bridge_ts:
            continue
        p = ctx.get("parsed")
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
            "tx": p.get("sig") or ctx.get("sig"),
            "chain": "SOL",
            "ts": t,
            "slot": slot,
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


def scan_eth_side(context, erc20_contract, eth_from, bridge_ts, bridge_block, side, bridge_amount):
    out = []
    for ctx in context:
        try:
            t = int(ctx.get("timeStamp", 0))
        except (TypeError, ValueError):
            t = 0
        if not t:
            continue
        if side == "entry" and t > bridge_ts:
            continue
        if side == "exit" and t < bridge_ts:
            continue
        p = ctx.get("parsed")
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
            "tx": p.get("hash") or ctx.get("hash"),
            "chain": "ETH",
            "ts": t,
            "block": block,
            "bridge_token_amount": amt,
            "coverage": amt / bridge_amount if bridge_amount else 0,
            "delta_bridge_sec": (bridge_ts - t) if side == "entry" else (t - bridge_ts),
            "atomic_same_block": bool(bridge_block and block == bridge_block),
            "sold": (p.get("summary") or {}).get("sold", []),
            "bought": (p.get("summary") or {}).get("bought", []),
            "from": p.get("from"),
            "to": p.get("to"),
            "fee_eth": p.get("fee_eth", 0),
        })
    return out


# ─────────────────────────── 处理单条 record ───────────────────────────

def process_record(r):
    direction = r.get("direction")
    if direction not in ("SOL→ETH", "ETH→SOL"):
        return {"skip": "unknown_direction"}

    sol_sender = r.get("sol_sender")
    eth_from = r.get("eth_from")
    sol_ts = r.get("sol_ts")
    eth_ts = r.get("eth_ts")
    sol_slot = r.get("sol_slot")
    eth_block = r.get("eth_block")
    sol_amount = r.get("sol_amount")
    eth_amount = r.get("eth_amount")

    if not (sol_ts and eth_ts and sol_sender and eth_from):
        return {"skip": "missing_bridge_header"}

    spl_mint = extract_bridge_spl(r.get("sol_parsed"), sol_sender, sol_amount)
    erc20_contract = extract_bridge_erc20(r.get("eth_parsed"), eth_from, eth_amount)
    if not spl_mint or not erc20_contract:
        return {"skip": "missing_bridge_identity"}

    if not sol_amount or sol_amount <= 0:
        return {"skip": "missing_bridge_amount"}

    if direction == "SOL→ETH":
        entry_raw = scan_sol_side(
            r.get("sol_context", []), spl_mint, sol_sender,
            sol_ts, sol_slot, "entry", sol_amount)
        exit_raw = scan_eth_side(
            r.get("eth_context", []), erc20_contract, eth_from,
            eth_ts, eth_block, "exit", eth_amount or sol_amount)
    else:
        entry_raw = scan_eth_side(
            r.get("eth_context", []), erc20_contract, eth_from,
            eth_ts, eth_block, "entry", eth_amount or sol_amount)
        exit_raw = scan_sol_side(
            r.get("sol_context", []), spl_mint, sol_sender,
            sol_ts, sol_slot, "exit", sol_amount)

    return {
        "id": f"{direction}|{(r.get('sol_sig') or '')[:16]}|{(r.get('eth_hash') or '')[:16]}",
        "direction": direction,
        "sol_sig": r.get("sol_sig"),
        "eth_hash": r.get("eth_hash"),
        "sol_ts": sol_ts,
        "eth_ts": eth_ts,
        "sol_slot": sol_slot,
        "eth_block": eth_block,
        "arbitrageur": {"sol": sol_sender, "eth": eth_from},
        "bridge": {
            "spl_mint": spl_mint,
            "erc20_contract": erc20_contract,
            "sol_symbol": r.get("sol_symbol"),
            "eth_symbol": r.get("eth_symbol"),
            "sol_amount": sol_amount,
            "eth_amount": eth_amount,
        },
        "time_diff_sec": r.get("time_diff_sec"),
        "sol_fee_sol": r.get("sol_fee_sol"),
        "eth_fee_eth": r.get("eth_fee_eth"),
        "_entry_raw": entry_raw,
        "_exit_raw": exit_raw,
    }


# ─────────────────────────── 按阈值分类 ───────────────────────────

def apply_threshold(raw, thr):
    entry_f = [s for s in raw["_entry_raw"] if s["coverage"] >= thr]
    exit_f = [s for s in raw["_exit_raw"] if s["coverage"] >= thr]
    if not entry_f or not exit_f:
        return None

    entry_cov = sum(s["coverage"] for s in entry_f)
    exit_cov = sum(s["coverage"] for s in exit_f)
    entry_full = 0.8 <= entry_cov <= 1.2
    exit_full = 0.8 <= exit_cov <= 1.2
    classification = "arbitrage_full" if (entry_full and exit_full) else "arbitrage_partial_coverage"

    flags = []
    if any(s.get("atomic_same_block") for s in entry_f):
        flags.append("atomic_entry_same_block")
    if any(s.get("atomic_same_block") for s in exit_f):
        flags.append("atomic_exit_same_block")
    if "atomic_entry_same_block" in flags and "atomic_exit_same_block" in flags:
        flags.append("fully_atomic")
    min_delta = min([s.get("delta_bridge_sec", 10**9) for s in entry_f + exit_f] + [10**9])
    if min_delta <= 60:
        flags.append("quick_round_trip_60s")
    elif min_delta <= 300:
        flags.append("quick_round_trip_300s")

    rec = {k: v for k, v in raw.items() if not k.startswith("_")}
    rec["entry_swaps"] = entry_f
    rec["exit_swaps"] = exit_f
    rec["entry_coverage"] = entry_cov
    rec["exit_coverage"] = exit_cov
    rec["classification"] = classification
    rec["flags"] = flags
    return rec


# ─────────────────────────── 主流程 ───────────────────────────

def main():
    print(f"加载 {INPUT.name}...")
    data = json.load(open(INPUT, encoding="utf-8"))
    records = data["records"]
    print(f"总记录: {len(records)}")

    raw_results = []
    skip_stats = {}
    for r in records:
        res = process_record(r)
        if "skip" in res:
            skip_stats[res["skip"]] = skip_stats.get(res["skip"], 0) + 1
            continue
        raw_results.append(res)

    print(f"成功提取跨链身份: {len(raw_results)}")
    if skip_stats:
        print(f"跳过: {skip_stats}")

    both_at_1pct = sum(
        1 for r in raw_results
        if any(s["coverage"] >= 0.01 for s in r["_entry_raw"])
        and any(s["coverage"] >= 0.01 for s in r["_exit_raw"])
    )
    print(f"1% 阈值下两边都有 swap 的记录: {both_at_1pct}")

    for thr in THRESHOLDS:
        thr_pct = int(round(thr * 100))
        filtered = []
        stats = {"arbitrage_full": 0, "arbitrage_partial_coverage": 0}
        flag_stats = {}
        dir_stats = {"SOL→ETH": 0, "ETH→SOL": 0}

        for raw in raw_results:
            rec = apply_threshold(raw, thr)
            if not rec:
                continue
            filtered.append(rec)
            stats[rec["classification"]] += 1
            dir_stats[rec["direction"]] += 1
            for f in rec["flags"]:
                flag_stats[f] = flag_stats.get(f, 0) + 1

        out = {
            "meta": {
                "threshold": thr,
                "input": INPUT.name,
                "total_records": len(records),
                "total_candidates": len(filtered),
                "classification_stats": stats,
                "direction_stats": dir_stats,
                "flag_stats": flag_stats,
            },
            "candidates": filtered,
        }
        fn = OUT_DIR / f"arbitrage_candidates_{thr_pct}pct.json"
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"\n阈值 {thr_pct}%: {len(filtered)} 条 -> {fn.name}")
        print(f"  分类: {stats}")
        print(f"  方向: {dir_stats}")
        if flag_stats:
            print(f"  flags: {flag_stats}")


if __name__ == "__main__":
    main()
