#!/usr/bin/env python3
"""
对每笔跨链交易，找到：
  - sender 跨链前的10笔交易
  - receiver 跨链后的10笔交易

数据来源:
  - 跨链记录: bridge_records/wormhole_portal.json + wormhole_ntt.json
  - ETH交易列表: signatures/eth_signatures.json
  - SOL交易列表: signatures/sol_signatures.json
  - ETH解析结果: parsed/eth_txs_parsed.json
  - SOL解析结果: parsed/sol_txs_parsed.json

输出: parsed/bridge_context.json

用法:
    python wormhole_data/find_bridge_context.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DIR = Path(__file__).parent / "use"

# 输入
PORTAL_FILE = DIR / "bridge_records" / "wormhole_portal.json"
NTT_FILE = DIR / "bridge_records" / "wormhole_ntt.json"
ETH_SIGS_FILE = DIR / "signatures" / "eth_signatures.json"
SOL_SIGS_FILE = DIR / "signatures" / "sol_signatures.json"
ETH_PARSED_FILE = DIR / "parsed" / "eth_txs_parsed.json"
SOL_PARSED_FILE = DIR / "parsed" / "sol_txs_parsed.json"

# 输出
OUTPUT = DIR / "parsed" / "bridge_context.json"

CONTEXT_SIZE = 10  # 前后各取10笔


def load_bridge_records():
    """加载所有跨链记录"""
    records = []
    for path, bridge_name in [(PORTAL_FILE, "Portal"), (NTT_FILE, "NTT")]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for r in data["records"]:
            src_ts = r.get("src_timestamp", "")
            dst_ts = r.get("dst_timestamp", "")
            records.append({
                "id": r.get("id", ""),
                "bridge": bridge_name,
                "src_sender": r.get("src_sender", ""),
                "dst_receiver": r.get("dst_receiver", ""),
                "src_tx_hash": r.get("src_tx_hash", ""),
                "dst_tx_hash": r.get("dst_tx_hash", ""),
                "src_timestamp": src_ts,
                "dst_timestamp": dst_ts,
                "src_ts": int(datetime.fromisoformat(src_ts).timestamp()) if src_ts else 0,
                "dst_ts": int(datetime.fromisoformat(dst_ts).timestamp()) if dst_ts else 0,
                "token_symbol": r.get("token_symbol", ""),
                "token_amount": r.get("token_amount", 0),
                "usd_amount": r.get("usd_amount", 0),
            })
    return records


def build_eth_tx_index(eth_sigs):
    """构建 ETH 地址 → 按时间排序的交易列表"""
    index = {}  # addr -> [(ts, hash), ...]
    for addr, info in eth_sigs["results"].items():
        txs = []
        for tx in info.get("normal_txs", []):
            txs.append((tx["ts"], tx["hash"]))
        # token_transfers 可能有重复hash，去重
        seen = set(t[1] for t in txs)
        for tx in info.get("token_transfers", []):
            if tx["hash"] not in seen:
                txs.append((tx["ts"], tx["hash"]))
                seen.add(tx["hash"])
        txs.sort(key=lambda x: x[0])  # 按时间升序
        index[addr.lower()] = txs
    return index


def build_sol_tx_index(sol_sigs):
    """构建 SOL 地址 → 按时间排序的签名列表"""
    index = {}  # addr -> [(ts, sig), ...]
    for addr, info in sol_sigs["results"].items():
        sigs = [(s["ts"], s["sig"]) for s in info.get("signatures", []) if s.get("ts")]
        sigs.sort(key=lambda x: x[0])
        index[addr] = sigs
    return index


def find_before(tx_list, target_ts, n):
    """找到 target_ts 之前的 n 笔交易（不含target_ts本身）"""
    # tx_list 已按时间升序
    idx = None
    for i, (ts, _) in enumerate(tx_list):
        if ts >= target_ts:
            idx = i
            break
    if idx is None:
        # 所有交易都在target之前
        return tx_list[-n:]
    start = max(0, idx - n)
    return tx_list[start:idx]


def find_after(tx_list, target_ts, n):
    """找到 target_ts 之后的 n 笔交易（不含target_ts本身）"""
    idx = None
    for i, (ts, _) in enumerate(tx_list):
        if ts > target_ts:
            idx = i
            break
    if idx is None:
        return []
    return tx_list[idx:idx + n]


def find_before_inclusive(tx_list, target_ts, n):
    """找到 ts <= target_ts 的最近 n 笔交易（含同区块）。

    因为同区块的交易可能排在 target_ts 之后（列表中位置靠后但 ts 相同），
    所以先收集所有 ts <= target_ts 的交易，再取最后 n 笔。
    """
    matches = [(ts, h) for ts, h in tx_list if ts <= target_ts]
    return matches[-n:] if matches else []


def find_after_inclusive(tx_list, target_ts, n):
    """找到 ts >= target_ts 的最近 n 笔交易（含同区块）。

    因为同区块的交易可能排在 target_ts 之前（列表中位置靠前但 ts 相同），
    所以先收集所有 ts >= target_ts 的交易，再取前 n 笔。
    """
    matches = [(ts, h) for ts, h in tx_list if ts >= target_ts]
    return matches[:n] if matches else []


def _extract_sold_bought(token_changes, addr):
    """从 token_changes 中提取指定地址的 sold/bought"""
    sold = {}
    bought = {}
    for tc in token_changes:
        if tc.get("owner") != addr:
            continue
        sym = tc.get("symbol", "?")
        change = tc.get("change", 0)
        if change < -1e-8:
            sold[sym] = sold.get(sym, 0) + (-change)
        elif change > 1e-8:
            bought[sym] = bought.get(sym, 0) + change
    return sold, bought


def sol_summary(token_changes, target_addr, sol_parsed_tx=None):
    """从 token_changes 中提取 target_addr 的 sold/bought 汇总。

    策略:
    1. 直接匹配 target_addr（bridge sender/receiver）
    2. 如果不匹配，找交易的 signer（sol_changes[0]），用 signer 的 token 变动
    3. 最后回退: 找 token_changes 中变动最多的地址（最大参与者）
    """
    fmt = lambda s, b: {
        "sold": [{"token": t, "amount": v} for t, v in s.items()],
        "bought": [{"token": t, "amount": v} for t, v in b.items()],
    }

    # 1. 直接匹配 target_addr
    sold, bought = _extract_sold_bought(token_changes, target_addr)
    if sold or bought:
        return fmt(sold, bought)

    # 2. 找交易 signer（sol_changes 第一个账户 = fee payer）
    if sol_parsed_tx:
        sol_changes = sol_parsed_tx.get("sol_changes", [])
        if sol_changes:
            signer = sol_changes[0].get("account", "")
            if signer and signer != target_addr:
                sold, bought = _extract_sold_bought(token_changes, signer)
                if sold or bought:
                    return fmt(sold, bought)

    # 3. 回退: 找变动最大的单个地址（最大参与者）
    per_addr = {}  # addr -> {token: change}
    for tc in token_changes:
        owner = tc.get("owner", "")
        if not owner:
            continue
        sym = tc.get("symbol", "?")
        change = tc.get("change", 0)
        if owner not in per_addr:
            per_addr[owner] = {}
        per_addr[owner][sym] = per_addr[owner].get(sym, 0) + change

    # 选有最多 token 种类变动的地址（swap通常涉及2种token）
    best_addr = None
    best_score = 0
    for addr, tokens in per_addr.items():
        nonzero = sum(1 for v in tokens.values() if abs(v) > 1e-8)
        total_vol = sum(abs(v) for v in tokens.values())
        score = nonzero * 1e12 + total_vol  # 优先token种类多，其次金额大
        if score > best_score:
            best_score = score
            best_addr = addr

    if best_addr:
        sold, bought = _extract_sold_bought(token_changes, best_addr)
        if sold or bought:
            return fmt(sold, bought)

    return {"sold": [], "bought": []}


def is_eth_address(addr):
    return addr.startswith("0x") and len(addr) == 42


def is_sol_address(addr):
    return not addr.startswith("0x") and len(addr) >= 32


def main():
    print("加载数据...", flush=True)

    records = load_bridge_records()
    print(f"  跨链记录: {len(records)} 笔")

    with open(ETH_SIGS_FILE, encoding="utf-8") as f:
        eth_sigs = json.load(f)
    eth_index = build_eth_tx_index(eth_sigs)
    print(f"  ETH地址: {len(eth_index)}")

    with open(SOL_SIGS_FILE, encoding="utf-8") as f:
        sol_sigs = json.load(f)
    sol_index = build_sol_tx_index(sol_sigs)
    print(f"  SOL地址: {len(sol_index)}")

    # 加载解析结果用于enrichment
    with open(ETH_PARSED_FILE, encoding="utf-8") as f:
        eth_parsed_data = json.load(f)
    eth_parsed = {tx["hash"]: tx for tx in eth_parsed_data["transactions"]}
    print(f"  ETH已解析: {len(eth_parsed)}")

    with open(SOL_PARSED_FILE, encoding="utf-8") as f:
        sol_parsed_data = json.load(f)
    sol_parsed = {tx["sig"]: tx for tx in sol_parsed_data["transactions"]}
    print(f"  SOL已解析: {len(sol_parsed)}")

    print(f"\n处理跨链记录...\n")

    results = []
    found_sender_before = 0
    found_sender_after = 0
    found_receiver_before = 0
    found_receiver_after = 0

    def _eth_summary_for_addr(parsed, target_addr):
        """从 ETH parsed events 中，以 target_addr 的视角计算 sold/bought。

        使用 net 模式：同一 token 的流入流出相抵。
        净流出 → sold，净流入 → bought。

        例: bridge 合约发来 442820 MEDUSA → target_addr 发出 440605 MEDUSA 去 swap → 收到 0.051 WETH
        MEDUSA net = +442820 - 440605 = +2214 (净流入，不算 sold)
        但实际是卖了 440605 MEDUSA。

        所以用 gross-out 模式：sold = target_addr 总流出，bought = 净流入（非 sold token）。
        对于同时有流入流出的 token：sold = 流出量，bought = 流入量（分别列出，不相抵）。
        这样套利检测可以匹配 sold amount ≈ bridge amount。

        最终: sold 440605 MEDUSA + 0.00006 ETH, bought 442820 MEDUSA + 0.051 WETH
        但这样 MEDUSA 同时出现在 sold 和 bought 里。

        更准确的方式: 对于同一 token 有进有出的情况，只保留流出量作为 sold，
        净剩余（流入-流出）如果为正则忽略（因为那是自己跨过来的剩余）。
        只有纯流入（没有流出）的 token 才算 bought。

        最终: sold 440605 MEDUSA + 0.00006 ETH, bought 0.051 WETH
        """
        addr = target_addr.lower()

        outflow = {}  # token -> total sent by addr
        inflow = {}   # token -> total received by addr
        for e in parsed.get("events", []):
            if e["type"] not in ("erc20_transfer", "eth_transfer"):
                continue
            token = e.get("token", "?")
            frm = e.get("from", "").lower()
            to = e.get("to", "").lower()
            amt = e.get("amount", 0)
            if frm == addr:
                outflow[token] = outflow.get(token, 0) + amt
            if to == addr:
                inflow[token] = inflow.get(token, 0) + amt

        # 所有 tokens
        all_tokens = set(outflow.keys()) | set(inflow.keys())
        sold = []
        bought = []
        for t in all_tokens:
            out_amt = outflow.get(t, 0)
            in_amt = inflow.get(t, 0)
            if out_amt > 1e-8 and in_amt > 1e-8:
                # 同一 token 有进有出 → sold = 流出量 (swap 出去的部分)
                sold.append({"token": t, "amount": out_amt})
            elif out_amt > 1e-8:
                # 纯流出
                sold.append({"token": t, "amount": out_amt})
            elif in_amt > 1e-8:
                # 纯流入 → bought
                bought.append({"token": t, "amount": in_amt})

        if not sold and not bought:
            return parsed.get("summary")

        return {"sold": sold, "bought": bought}

    def _build_eth_entries(tx_list, time_range_func, target_ts, n, target_addr):
        entries = []
        for ts, h in time_range_func(tx_list, target_ts, n):
            parsed = eth_parsed.get(h)
            if parsed:
                entries.append({
                    "chain": "ETH", "hash": h, "ts": ts,
                    "time": parsed.get("time"),
                    "summary": _eth_summary_for_addr(parsed, target_addr),
                })
            else:
                entries.append({"chain": "ETH", "hash": h, "ts": ts})
        return entries

    def _build_sol_entries(tx_list, time_range_func, target_ts, n, target_addr):
        entries = []
        for ts, sig in time_range_func(tx_list, target_ts, n):
            parsed = sol_parsed.get(sig)
            if parsed:
                tc = parsed.get("token_changes", [])
                entries.append({
                    "chain": "SOL", "sig": sig, "ts": ts,
                    "time": parsed.get("time"),
                    "programs": parsed.get("programs", [])[:5],
                    "token_changes": tc,
                    "summary": sol_summary(tc, target_addr, parsed),
                })
            else:
                entries.append({"chain": "SOL", "sig": sig, "ts": ts})
        return entries

    for i, rec in enumerate(records):
        src_sender = rec["src_sender"]
        dst_receiver = rec["dst_receiver"]
        src_ts = rec["src_ts"]
        dst_ts = rec["dst_ts"]

        context = {
            "bridge": rec["bridge"],
            "id": rec["id"],
            "src_sender": src_sender,
            "dst_receiver": dst_receiver,
            "src_tx_hash": rec["src_tx_hash"],
            "dst_tx_hash": rec["dst_tx_hash"],
            "src_timestamp": rec["src_timestamp"],
            "dst_timestamp": rec["dst_timestamp"],
            "token_symbol": rec["token_symbol"],
            "token_amount": rec["token_amount"],
            "usd_amount": rec["usd_amount"],
            "sender_before": [],
            "sender_after": [],
            "receiver_before": [],
            "receiver_after": [],
        }

        # Sender: before=同区块或之前(含src_tx), after=严格之后
        if src_ts:
            if is_eth_address(src_sender):
                tx_list = eth_index.get(src_sender.lower(), [])
                context["sender_before"] = _build_eth_entries(tx_list, find_before_inclusive, src_ts, CONTEXT_SIZE, src_sender)
                context["sender_after"] = _build_eth_entries(tx_list, find_after, src_ts, CONTEXT_SIZE, src_sender)
            elif is_sol_address(src_sender):
                tx_list = sol_index.get(src_sender, [])
                context["sender_before"] = _build_sol_entries(tx_list, find_before_inclusive, src_ts, CONTEXT_SIZE, src_sender)
                context["sender_after"] = _build_sol_entries(tx_list, find_after, src_ts, CONTEXT_SIZE, src_sender)

        # Receiver: before=严格之前, after=同区块或之后(含dst_tx)
        if dst_ts:
            if is_eth_address(dst_receiver):
                tx_list = eth_index.get(dst_receiver.lower(), [])
                context["receiver_before"] = _build_eth_entries(tx_list, find_before, dst_ts, CONTEXT_SIZE, dst_receiver)
                context["receiver_after"] = _build_eth_entries(tx_list, find_after_inclusive, dst_ts, CONTEXT_SIZE, dst_receiver)
            elif is_sol_address(dst_receiver):
                tx_list = sol_index.get(dst_receiver, [])
                context["receiver_before"] = _build_sol_entries(tx_list, find_before, dst_ts, CONTEXT_SIZE, dst_receiver)
                context["receiver_after"] = _build_sol_entries(tx_list, find_after_inclusive, dst_ts, CONTEXT_SIZE, dst_receiver)

        if context["sender_before"]:
            found_sender_before += 1
        if context["sender_after"]:
            found_sender_after += 1
        if context["receiver_before"]:
            found_receiver_before += 1
        if context["receiver_after"]:
            found_receiver_after += 1

        results.append(context)

    # 保存
    output = {
        "meta": {
            "total_bridge_records": len(results),
            "found_sender_before": found_sender_before,
            "found_sender_after": found_sender_after,
            "found_receiver_before": found_receiver_before,
            "found_receiver_after": found_receiver_after,
            "context_size": CONTEXT_SIZE,
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "records": results,
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"{'=' * 60}")
    print(f"  跨链记录: {len(results)}")
    print(f"  找到sender_before: {found_sender_before}")
    print(f"  找到sender_after: {found_sender_after}")
    print(f"  找到receiver_before: {found_receiver_before}")
    print(f"  找到receiver_after: {found_receiver_after}")
    print(f"  保存: {OUTPUT.name} ({size_mb:.1f} MB)")

    # 样例
    print(f"\n样例:")
    for r in results[:3]:
        print(f"\n  [{r['bridge']}] {r['token_symbol']} {r['token_amount']}")
        print(f"  Sender: {r['src_sender'][:30]}  Time: {r['src_timestamp']}")
        print(f"  Receiver: {r['dst_receiver'][:30]}  Time: {r['dst_timestamp']}")
        if r["sender_before"]:
            print(f"  Sender前{len(r['sender_before'])}笔:")
            for tx in r["sender_before"][-3:]:
                chain = tx["chain"]
                tid = tx.get("hash", tx.get("sig", ""))[:30]
                summary = tx.get("summary", {})
                if summary:
                    sold = summary.get("sold", [])
                    bought = summary.get("bought", [])
                    s_str = ", ".join(f"{s['token']} {s['amount']:.4f}" for s in sold[:2]) if sold else "-"
                    b_str = ", ".join(f"{b['token']} {b['amount']:.4f}" for b in bought[:2]) if bought else "-"
                    print(f"    [{chain}] {tid}  Sold:{s_str}  Bought:{b_str}")
                else:
                    changes = tx.get("token_changes", [])
                    c_str = ", ".join(f"{c['symbol'][:10]} {c['change']:+.4f}" for c in changes[:2]) if changes else "-"
                    print(f"    [{chain}] {tid}  Changes:{c_str}")
        if r["receiver_after"]:
            print(f"  Receiver后{len(r['receiver_after'])}笔:")
            for tx in r["receiver_after"][:3]:
                chain = tx["chain"]
                tid = tx.get("hash", tx.get("sig", ""))[:30]
                summary = tx.get("summary", {})
                if summary:
                    sold = summary.get("sold", [])
                    bought = summary.get("bought", [])
                    s_str = ", ".join(f"{s['token']} {s['amount']:.4f}" for s in sold[:2]) if sold else "-"
                    b_str = ", ".join(f"{b['token']} {b['amount']:.4f}" for b in bought[:2]) if bought else "-"
                    print(f"    [{chain}] {tid}  Sold:{s_str}  Bought:{b_str}")
                else:
                    changes = tx.get("token_changes", [])
                    c_str = ", ".join(f"{c['symbol'][:10]} {c['change']:+.4f}" for c in changes[:2]) if changes else "-"
                    print(f"    [{chain}] {tid}  Changes:{c_str}")


if __name__ == "__main__":
    main()
