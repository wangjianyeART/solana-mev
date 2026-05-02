#!/usr/bin/env python3
"""
匹配 SOL 端和 ETH 端的 Portal 跨链交易对。

匹配范围:
  - 方向1: SOL outbound (Solana→Ethereum) ↔ ETH inbound (Solana→Ethereum)
  - 方向2: ETH outbound (Ethereum→Solana) ↔ SOL inbound

匹配规则 (按优先级):
  1. Token 匹配: 如果 token_pairs.json 有已知的 SPL↔ERC20 映射，优先使用
  2. 数值匹配: 两端金额差异 ≤ 0.1%
  3. 时间匹配: 时间差绝对值最小，且 ≤ 30 分钟

输出到 recent_5d/matched/:
  - matched.json          — 成功配对
  - unmatched_eth.json    — ETH 端未配对
  - unmatched_sol.json    — SOL 端未配对
  - new_pairs.json        — 匹配过程中新发现的 SPL↔ERC20 映射

用法:
    python wormhole_data/match_sol_eth.py
"""

import json
from pathlib import Path

DIR = Path(__file__).parent / "use" / "portal_full"
RECENT = DIR / "recent_5d"

SOL_PARSED = RECENT / "sol_parsed.json"
ETH_PARSED = RECENT / "eth_parsed.json"
TOKEN_PAIRS = DIR / "token_pairs.json"

OUT_DIR = RECENT / "matched"

# 金额容差
AMOUNT_TOLERANCE = 0.001  # 0.1%
# 时间窗口 (秒)
TIME_WINDOW = 24 * 3600  # 24 小时


def build_pair_index(pairs_data):
    """构建双向查找: erc20→set(spl_mint), spl_mint→set(erc20)"""
    erc20_to_spl = {}
    spl_to_erc20 = {}
    for p in pairs_data.get("pairs", []):
        erc20s = p.get("erc20", [])
        spls = p.get("spl_mint", [])
        for e in erc20s:
            if e:
                erc20_to_spl.setdefault(e.lower(), set()).update(s for s in spls if s)
        for s in spls:
            if s:
                spl_to_erc20.setdefault(s, set()).update(e.lower() for e in erc20s if e)
    return erc20_to_spl, spl_to_erc20


def extract_eth_token(tx):
    """从 ETH 交易中提取 portal 相关的 token 和 amount (同 token 多笔 portal_out/in 求和)"""
    if tx["direction"] == "inbound":
        role = "portal_out"
    else:
        role = "portal_in"

    # 按 contract 分组求和同一 role 的所有 transfer
    grouped = {}
    for t in tx.get("transfers", []):
        if t.get("role") == role:
            c = t["contract"].lower()
            if c not in grouped:
                grouped[c] = {"contract": c, "amount": 0, "token": t["token"]}
            grouped[c]["amount"] += t["amount"]

    if grouped:
        # 取金额最大的 contract
        best = max(grouped.values(), key=lambda x: x["amount"])
        return best["contract"], best["amount"], best["token"]
    if tx.get("transfers"):
        t = tx["transfers"][0]
        return t["contract"].lower(), t["amount"], t["token"]
    return None, None, None


def extract_sol_token(tx):
    """从 SOL 交易中提取跨链的 token 和 amount"""
    changes = tx.get("token_changes", [])
    if not changes:
        return None, None, None

    if tx["direction"] == "outbound":
        negatives = [c for c in changes if c["change"] < 0]
        if negatives:
            best = min(negatives, key=lambda c: c["change"])
            return best["mint"], abs(best["change"]), best["symbol"]
    else:
        positives = [c for c in changes if c["change"] > 0]
        if positives:
            best = max(positives, key=lambda c: c["change"])
            return best["mint"], best["change"], best["symbol"]

    best = max(changes, key=lambda c: abs(c["change"]))
    return best["mint"], abs(best["change"]), best["symbol"]


def amount_match(a, b):
    """检查两个金额是否在容差范围内"""
    if a is None or b is None or a == 0 or b == 0:
        return False
    ratio = abs(a - b) / max(abs(a), abs(b))
    return ratio <= AMOUNT_TOLERANCE


def do_matching(sol_txs, eth_txs, spl_to_erc20, erc20_to_spl):
    """
    通用匹配函数。
    sol_txs: Solana 端交易列表
    eth_txs: Ethereum 端交易列表
    不管方向，分别用 extract_sol_token / extract_eth_token 提取各自的 token 信息，
    然后用 pair 表做双向查找。
    """
    matched = []
    new_pairs = {}
    eth_used = set()
    sol_used = set()

    # ── Pass 1: 已知 pair + 数值 + 时间 ──
    for si, sol in enumerate(sol_txs):
        sol_mint, sol_amount, sol_symbol = extract_sol_token(sol)
        if sol_mint is None or sol_amount is None:
            continue

        known_erc20s = spl_to_erc20.get(sol_mint, set())
        if not known_erc20s:
            continue  # pass 1 只处理已知 pair

        best_match = None
        best_time_diff = float("inf")

        for ei, eth in enumerate(eth_txs):
            if ei in eth_used:
                continue
            eth_contract, eth_amount, eth_symbol = extract_eth_token(eth)
            if eth_contract is None or eth_amount is None:
                continue

            if eth_contract not in known_erc20s:
                continue

            if not amount_match(sol_amount, eth_amount):
                continue

            time_diff = abs((eth.get("ts") or 0) - (sol.get("ts") or 0))
            if time_diff > TIME_WINDOW:
                continue

            if time_diff < best_time_diff:
                best_time_diff = time_diff
                best_match = ei

        if best_match is not None:
            eth = eth_txs[best_match]
            eth_contract, eth_amount, eth_symbol = extract_eth_token(eth)
            matched.append(_build_record("known_pair", sol, eth, sol_mint, eth_contract,
                                         sol_symbol, eth_symbol, sol_amount, eth_amount, best_time_diff))
            eth_used.add(best_match)
            sol_used.add(si)

    # ── Pass 2: 未知 pair, 纯数值+时间匹配 ──
    for si, sol in enumerate(sol_txs):
        if si in sol_used:
            continue
        sol_mint, sol_amount, sol_symbol = extract_sol_token(sol)
        if sol_mint is None or sol_amount is None:
            continue

        best_match = None
        best_time_diff = float("inf")

        for ei, eth in enumerate(eth_txs):
            if ei in eth_used:
                continue
            eth_contract, eth_amount, eth_symbol = extract_eth_token(eth)
            if eth_contract is None or eth_amount is None:
                continue

            if not amount_match(sol_amount, eth_amount):
                continue

            time_diff = abs((eth.get("ts") or 0) - (sol.get("ts") or 0))
            if time_diff > TIME_WINDOW:
                continue

            if time_diff < best_time_diff:
                best_time_diff = time_diff
                best_match = ei

        if best_match is not None:
            eth = eth_txs[best_match]
            eth_contract, eth_amount, eth_symbol = extract_eth_token(eth)

            pair_key = f"{sol_mint}:{eth_contract}"
            if pair_key not in new_pairs:
                new_pairs[pair_key] = {
                    "spl_mint": sol_mint,
                    "erc20_contract": eth_contract,
                    "sol_symbol": sol_symbol,
                    "eth_symbol": eth_symbol,
                    "count": 0,
                }
            new_pairs[pair_key]["count"] += 1

            matched.append(_build_record("amount_time", sol, eth, sol_mint, eth_contract,
                                         sol_symbol, eth_symbol, sol_amount, eth_amount, best_time_diff))
            eth_used.add(best_match)
            sol_used.add(si)

    unmatched_sol = [sol_txs[i] for i in range(len(sol_txs)) if i not in sol_used]
    unmatched_eth = [eth_txs[i] for i in range(len(eth_txs)) if i not in eth_used]

    return matched, unmatched_sol, unmatched_eth, list(new_pairs.values())


def _build_record(match_type, sol, eth, spl_mint, erc20_contract,
                   sol_symbol, eth_symbol, sol_amount, eth_amount, time_diff):
    return {
        "match_type": match_type,
        "sol_sig": sol.get("sig"),
        "eth_hash": eth.get("hash"),
        "sol_time": sol.get("time"),
        "eth_time": eth.get("time"),
        "time_diff_sec": time_diff,
        "sol_direction": sol["direction"],
        "eth_direction": eth["direction"],
        "spl_mint": spl_mint,
        "erc20_contract": erc20_contract,
        "sol_symbol": sol_symbol,
        "eth_symbol": eth_symbol,
        "sol_amount": sol_amount,
        "eth_amount": eth_amount,
        "amount_diff_pct": round(abs(sol_amount - eth_amount) / max(sol_amount, eth_amount) * 100, 4) if max(sol_amount, eth_amount) > 0 else 0,
        "sol_sender": sol.get("sender") or sol.get("from"),
        "eth_from": eth.get("from") or eth.get("sender"),
        "sol_fee_sol": sol.get("fee_sol"),
        "eth_fee_eth": eth.get("fee_eth"),
        "eth_gas_used": eth.get("gas_used"),
    }


def main():
    sol_data = json.load(open(SOL_PARSED, encoding="utf-8"))
    eth_data = json.load(open(ETH_PARSED, encoding="utf-8"))
    pairs_data = json.load(open(TOKEN_PAIRS, encoding="utf-8")) if TOKEN_PAIRS.exists() else {"pairs": []}

    erc20_to_spl, spl_to_erc20 = build_pair_index(pairs_data)
    print(f"已知 pair: erc20→spl {len(erc20_to_spl)} 个, spl→erc20 {len(spl_to_erc20)} 个")

    # ── 方向1: SOL outbound(→ETH) ↔ ETH inbound(Solana→ETH) ──
    sol_out_eth = [t for t in sol_data["transactions"]
                   if t["direction"] == "outbound" and t.get("dst_chain") in ("Ethereum", None)]
    eth_in_sol = [t for t in eth_data["transactions"]
                  if t["direction"] == "inbound" and t.get("src_chain") == "Solana"]

    print(f"\n方向1: SOL→ETH")
    print(f"  SOL outbound→Ethereum: {len(sol_out_eth)}")
    print(f"  ETH inbound←Solana:    {len(eth_in_sol)}")

    m1, us1, ue1, np1 = do_matching(sol_out_eth, eth_in_sol, spl_to_erc20, erc20_to_spl)
    print(f"  匹配: {len(m1)} (known={sum(1 for m in m1 if m['match_type']=='known_pair')}, "
          f"new={sum(1 for m in m1 if m['match_type']=='amount_time')})")

    # ── 方向2: ETH outbound(→SOL) ↔ SOL inbound ──
    eth_out_sol = [t for t in eth_data["transactions"]
                   if t["direction"] == "outbound" and t.get("dst_chain") == "Solana"]
    sol_in = [t for t in sol_data["transactions"]
              if t["direction"] == "inbound"]

    print(f"\n方向2: ETH→SOL")
    print(f"  ETH outbound→Solana: {len(eth_out_sol)}")
    print(f"  SOL inbound:         {len(sol_in)}")

    # 方向2: SOL 是接收端, ETH 是发送端, 但 do_matching 始终是 (sol_txs, eth_txs)
    m2, us2, ue2, np2 = do_matching(sol_in, eth_out_sol, spl_to_erc20, erc20_to_spl)
    print(f"  匹配: {len(m2)} (known={sum(1 for m in m2 if m['match_type']=='known_pair')}, "
          f"new={sum(1 for m in m2 if m['match_type']=='amount_time')})")

    # 合并结果
    all_matched = m1 + m2
    all_unmatched_sol = us1 + us2
    all_unmatched_eth = ue1 + ue2
    all_new_pairs = np1 + np2

    known_count = sum(1 for m in all_matched if m["match_type"] == "known_pair")
    amount_count = sum(1 for m in all_matched if m["match_type"] == "amount_time")

    print(f"\n{'=' * 60}")
    print(f"匹配结果:")
    print(f"  总配对: {len(all_matched)}")
    print(f"    已知 pair: {known_count}")
    print(f"    数值+时间: {amount_count}")
    print(f"  未匹配 SOL: {len(all_unmatched_sol)}")
    print(f"  未匹配 ETH: {len(all_unmatched_eth)}")
    print(f"  新发现 pair: {len(all_new_pairs)}")

    # 保存
    OUT_DIR.mkdir(exist_ok=True)

    with open(OUT_DIR / "matched.json", "w", encoding="utf-8") as f:
        json.dump({"meta": {"total": len(all_matched), "known_pair": known_count,
                            "amount_time": amount_count},
                   "records": all_matched}, f, indent=2, ensure_ascii=False)

    with open(OUT_DIR / "unmatched_sol.json", "w", encoding="utf-8") as f:
        json.dump({"total": len(all_unmatched_sol),
                   "transactions": all_unmatched_sol}, f, indent=2, ensure_ascii=False)

    with open(OUT_DIR / "unmatched_eth.json", "w", encoding="utf-8") as f:
        json.dump({"total": len(all_unmatched_eth),
                   "transactions": all_unmatched_eth}, f, indent=2, ensure_ascii=False)

    with open(OUT_DIR / "new_pairs.json", "w", encoding="utf-8") as f:
        json.dump({"total": len(all_new_pairs),
                   "pairs": all_new_pairs}, f, indent=2, ensure_ascii=False)

    print(f"\n输出目录: {OUT_DIR}")

    # 打印匹配样例
    if all_matched:
        print(f"\n样例 (前10笔):")
        for m in all_matched[:10]:
            direction = f"{m['sol_direction']}(SOL)/{m['eth_direction']}(ETH)"
            print(f"  [{m['match_type']}] {m['sol_symbol']}/{m['eth_symbol']}  "
                  f"SOL:{m['sol_amount']:.4f}  ETH:{m['eth_amount']:.4f}  "
                  f"diff:{m['amount_diff_pct']}%  time:{m['time_diff_sec']}s  {direction}")

    if all_new_pairs:
        print(f"\n新发现的 token pair:")
        for p in sorted(all_new_pairs, key=lambda x: -x["count"]):
            print(f"  {p['sol_symbol']:<45s} ↔ {p['eth_symbol']:<20s} x{p['count']}")


if __name__ == "__main__":
    main()
