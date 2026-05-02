#!/usr/bin/env python3
"""
跨链套利检测 v3 — 基于 Token 种类匹配。

与 detect_arbitrage.py (v2) 的区别:
  - v2: 匹配 swap 金额 ≈ bridge 金额 (不管 token 种类)
  - v3: 匹配 swap 中的 token 种类 = bridge token (不要求金额相近)

匹配策略:
  1. 桥接记录的 token_symbol 直接匹配 swap summary 中的 token (symbol)
  2. 对 SOL 链上 swap，token 可能是 mint 地址而非 symbol，使用 mint_cache 解析
  3. 匹配到 token 后，要求交易是 swap (同时有 sold + bought)
  4. 买入: sender 的 before/after 中有 swap，bought 含桥接 token
  5. 卖出: receiver 的 before/after 中有 swap，sold 含桥接 token

打分:
  - time_dist 优先 (离桥接越近越好), amount_dist 辅助

输出目录: parsed/token_match/
  - arbitrage_detected.json
  - arbitrage_unmatched.json
  - arbitrage_unmatched_complete.json
  - arbitrage_unmatched_incomplete.json

用法:
    python wormhole_data/detect_arbitrage_token.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter

DIR = Path(__file__).parent / "use"
INPUT = DIR / "parsed" / "bridge_context_completed.json"
OUT_DIR = DIR / "parsed" / "token_match"

OUTPUT_ARB = OUT_DIR / "arbitrage_detected.json"
OUTPUT_UNMATCHED = OUT_DIR / "arbitrage_unmatched.json"
OUTPUT_COMPLETE = OUT_DIR / "arbitrage_unmatched_complete.json"
OUTPUT_INCOMPLETE = OUT_DIR / "arbitrage_unmatched_incomplete.json"

MINT_CACHE_PATH = DIR / "cache" / "mint_cache.json"
CROSSCHAIN_TOKENS_PATH = Path(__file__).parent.parent / "bridge" / "crosschain_sol_eth_tokens.json"
NTT_PAIRS_PATH = DIR / "ntt_eth_sol_pairs.json"

# sender_after / receiver_before 的时间限制（秒）
# 这两个方向的目的是找同区块/同时间的交易，不是真的找"之后"或"之前"
SAME_BLOCK_THRESHOLD = 30

# 已知 token 别名映射 (桥接 symbol -> swap 中可能出现的 symbol)
TOKEN_ALIASES = {
    "WETH": {"WETH", "ETH"},
    "WSOL": {"WSOL", "SOL"},
    "WBTC": {"WBTC", "BTC"},
    "WBNB": {"WBNB", "BNB"},
    "PayPal USD": {"PayPal USD", "PYUSD", "pyUSD"},
    "Worldcoin": {"Worldcoin", "WLD"},
    "Bonk": {"Bonk", "BONK"},
    "RENDER": {"RENDER", "RNDR"},
    "RNDR": {"RENDER", "RNDR"},
    "tBTC": {"tBTC", "TBTC"},
    "wstETH": {"wstETH", "WSTETH", "stETH"},
    "Cross The Ages": {"Cross The Ages", "CTA"},
    "Pocket Network": {"Pocket Network", "POKT"},
    "XYO Network": {"XYO Network", "XYO"},
    "Department Of Government Efficiency": {"Department Of Government Efficiency", "DOGE", "D.O.G.E"},
    "Kendu Inu": {"Kendu Inu", "KENDU"},
    "Roaring Kitty": {"Roaring Kitty", "KITTY"},
    "Satoshi Nakamoto": {"Satoshi Nakamoto", "SATOSHI"},
    "Wall Street Pepe": {"Wall Street Pepe", "WEPE"},
    "Strawberry AI": {"Strawberry AI", "BERRY"},
    "SPX6900": {"SPX6900", "SPX"},
    "TRUMP": {"TRUMP", "$PTRUMP", "OFFICIAL TRUMP"},
    "$PTRUMP": {"TRUMP", "$PTRUMP", "OFFICIAL TRUMP"},
    "AVA (Travala)": {"AVA (Travala)", "AVA", "TRVL"},
    "AlphaKEK.AI": {"AlphaKEK.AI", "AIKEK"},
    "AstroPepeX": {"AstroPepeX", "APX"},
    "Business Coin": {"Business Coin", "BIZ"},
    "Honey Badger": {"Honey Badger", "BADGER"},
    "Intrepid Token": {"Intrepid Token", "INTREPID"},
    "Optopia AI": {"Optopia AI", "OPTOPIA"},
    "PepeCoin": {"PepeCoin", "PEPECOIN"},
    "Station This": {"Station This", "STATION"},
    "Tradfi Bro": {"Tradfi Bro", "TFB"},
    "The Balkan Dwarf": {"The Balkan Dwarf", "TBD"},
    "WhiteRock": {"WhiteRock", "WHITE"},
    "Game 5 BALL": {"Game 5 BALL", "BALL", "ball"},
    "ball": {"ball", "BALL", "Game 5 BALL"},
    "Unicorn": {"Unicorn", "UNI"},  # 注意：可能和 Uniswap UNI 冲突
    "Dollar": {"Dollar", "USDC", "USD"},  # 根据实际情况调整
    "Dogei": {"Dogei", "DOGEI"},
    "Gondola": {"Gondola", "GONDOLA"},
    "Groyper": {"Groyper", "GROYPER"},
    "Licker": {"Licker", "LICKER"},
    "Mars": {"Mars", "MARS"},
    "Medusa": {"Medusa", "MEDUSA"},
    "Swag": {"Swag", "SWAG"},
    "lit": {"lit", "LIT"},
    "luffy": {"luffy", "LUFFY"},
    "snort": {"snort", "SNORT"},
    "nat": {"nat", "NAT"},
    "assdaq": {"assdaq", "ASSDAQ"},
    "inx": {"inx", "INX"},
    "tkx": {"tkx", "TKX"},
    "mxna": {"mxna", "MXNA"},
    "dotmoovs": {"dotmoovs", "MOOV"},
    "Buddha": {"Buddha", "BUDDHA"},
    "ICE": {"ICE", "ice"},
    "DOG": {"DOG", "dog"},
}

# symbol -> SOL mint 地址映射 (从 crosschain_sol_eth_tokens.json + ntt_eth_sol_pairs.json 加载)
SYMBOL_TO_SOL_MINT = {}

# 加载 mint cache
MINT_TO_SYMBOL = {}


def load_all_token_mappings():
    """加载所有 token 映射: mint_cache + crosschain_tokens + ntt_pairs"""
    global MINT_TO_SYMBOL, SYMBOL_TO_SOL_MINT

    # 1. mint_cache
    if MINT_CACHE_PATH.exists():
        with open(MINT_CACHE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        for mint, sym in raw.items():
            if len(sym) < 20 and sym != mint[:8] + "...":
                MINT_TO_SYMBOL[mint] = sym

    # 2. crosschain_sol_eth_tokens.json
    if CROSSCHAIN_TOKENS_PATH.exists():
        with open(CROSSCHAIN_TOKENS_PATH, encoding="utf-8") as f:
            crosschain = json.load(f)
        for token in crosschain.get("confirmed_crosschain", []):
            sym = token.get("symbol", "")
            sol_mint = token.get("sol_mint", "")
            if sym and sol_mint:
                SYMBOL_TO_SOL_MINT[sym] = sol_mint
                SYMBOL_TO_SOL_MINT[sym.upper()] = sol_mint
                SYMBOL_TO_SOL_MINT[sym.lower()] = sol_mint
                MINT_TO_SYMBOL[sol_mint] = sym

    # 3. ntt_eth_sol_pairs.json
    if NTT_PAIRS_PATH.exists():
        with open(NTT_PAIRS_PATH, encoding="utf-8") as f:
            ntt = json.load(f)
        for pair in ntt.get("pairs", []):
            sym = pair.get("symbol", "")
            sol_mint = pair.get("sol_token", "")
            if sym and sol_mint:
                SYMBOL_TO_SOL_MINT[sym] = sol_mint
                SYMBOL_TO_SOL_MINT[sym.upper()] = sol_mint
                SYMBOL_TO_SOL_MINT[sym.lower()] = sol_mint
                MINT_TO_SYMBOL[sol_mint] = sym

    # 4. 补充已知的常用 token
    extra = {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
        "So11111111111111111111111111111111111111112": "SOL",
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
        "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "stSOL",
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": "JUP",
        "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL": "JTO",
        "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3": "PYTH",
        "85VBFQZC9TZkfaptBWjvUw7YbZjy52A6mjtPGjstQAmQ": "W",
        "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH",
        "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC",
        "A9mUU4qviSctJVPJdBGKbGJCQ9fwSuud9N9DZPB8kqx8": "WETH",  # 另一个 wrapped ETH
        "9gP2kCy3wA1ctvYWQk75guqXuHfrEomqydHLtcTCqiLa": "WBTC",  # 另一个 wrapped BTC
        "9n4nbM75f5Ui33ZbPYXn59EwSgE8CGsHtAeTH5YFeJ9E": "WBTC",  # Portal WBTC
        "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH",  # Portal WETH
        "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo": "PYUSD",
        "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN": "TRUMP",
    }
    for mint, sym in extra.items():
        MINT_TO_SYMBOL[mint] = sym
        if sym not in SYMBOL_TO_SOL_MINT:
            SYMBOL_TO_SOL_MINT[sym] = mint

    print(f"  Mint→Symbol: {len(MINT_TO_SYMBOL)} 个")
    print(f"  Symbol→SOL Mint: {len(SYMBOL_TO_SOL_MINT)} 个")


def resolve_token(token_str):
    """将 token 标识符解析为统一的 symbol"""
    if not token_str:
        return ""
    # 已经是短 symbol
    if len(token_str) < 20:
        return token_str
    # 尝试 mint cache
    if token_str in MINT_TO_SYMBOL:
        return MINT_TO_SYMBOL[token_str]
    # 0x 开头的 ETH 地址，不太常见
    if token_str.startswith("0x"):
        return token_str
    # 无法解析的 SOL mint
    return token_str


def get_token_names(bridge_symbol):
    """获取桥接 token 的所有可能名称（含别名 + SOL mint 地址）"""
    names = {bridge_symbol}

    # 别名
    if bridge_symbol in TOKEN_ALIASES:
        names.update(TOKEN_ALIASES[bridge_symbol])

    # 大小写变体
    names.add(bridge_symbol.upper())
    names.add(bridge_symbol.lower())

    # 对每个 name，查找对应的 SOL mint 地址 (swap summary 可能用 mint 而非 symbol)
    mints_to_add = set()
    for name in list(names):
        if name in SYMBOL_TO_SOL_MINT:
            mints_to_add.add(SYMBOL_TO_SOL_MINT[name])
        if name.upper() in SYMBOL_TO_SOL_MINT:
            mints_to_add.add(SYMBOL_TO_SOL_MINT[name.upper()])
    names.update(mints_to_add)

    return names


def is_swap(summary):
    """判断交易是否是 swap（必须同时有 sold 和 bought）"""
    if not summary:
        return False
    return bool(summary.get("sold")) and bool(summary.get("bought"))


def token_matches(item_token, target_names):
    """检查 swap 中的 token 是否匹配桥接 token"""
    resolved = resolve_token(item_token)
    # 直接匹配
    if resolved in target_names:
        return True
    # 大小写不敏感匹配
    resolved_lower = resolved.lower()
    for name in target_names:
        if name.lower() == resolved_lower:
            return True
    return False


def find_match(tx_list, bridge_amount, bridge_ts, match_field, direction_label, target_names):
    """在交易列表中找匹配 token 种类的 swap。

    Args:
        tx_list: 交易列表
        bridge_amount: 桥接金额 (用于 amount ratio 计算，不作为筛选条件)
        bridge_ts: 桥接时间戳 (epoch)
        match_field: "bought" 找买入, "sold" 找卖出
        direction_label: 方向标签
        target_names: 桥接 token 的所有可能名称

    Returns:
        best match dict or None
    """
    if not tx_list:
        return None

    candidates = []

    for i, tx in enumerate(tx_list):
        summary = tx.get("summary", {})
        if not is_swap(summary):
            continue

        for item in summary.get(match_field, []):
            if not token_matches(item["token"], target_names):
                continue

            amt = float(item["amount"])
            tx_ts = tx.get("ts", 0)
            time_dist = abs(tx_ts - bridge_ts) if tx_ts and bridge_ts else 999999

            # amount ratio (信息用，不用于筛选)
            ratio = amt / bridge_amount if bridge_amount else 0

            # 打分: 时间优先
            score = time_dist

            candidates.append({
                "index": i,
                "tx": tx,
                "matched_item": item,
                "ratio": ratio,
                "time_dist_s": time_dist,
                "score": score,
                "direction": direction_label,
            })

    if not candidates:
        return None

    best = min(candidates, key=lambda x: x["score"])

    if "before" in direction_label:
        position = len(tx_list) - best["index"]
        pos_label = "position_before"
    else:
        position = best["index"] + 1
        pos_label = "position_after"

    return {
        "list_index": best["index"],
        pos_label: position,
        "direction": best["direction"],
        "tx": best["tx"],
        "matched_token": best["matched_item"]["token"],
        "matched_token_resolved": resolve_token(best["matched_item"]["token"]),
        "matched_amount": float(best["matched_item"]["amount"]),
        "ratio": best["ratio"],
        "time_dist_s": best["time_dist_s"],
    }


def find_buy(rec, bridge_amount, src_ts, target_names):
    """在 sender 的 before+after 中找买入桥接 token 的 swap。
    sender_after 只找同区块的交易（time_dist <= SAME_BLOCK_THRESHOLD）。"""
    best = None
    for direction in ["sender_before", "sender_after"]:
        match = find_match(rec.get(direction, []), bridge_amount, src_ts,
                           "bought", direction, target_names)
        if match:
            # sender_after 必须是同区块（时间差很小）
            if direction == "sender_after" and match["time_dist_s"] > SAME_BLOCK_THRESHOLD:
                continue
            if best is None or match["time_dist_s"] < best["time_dist_s"]:
                best = match
    return best


def find_sell(rec, bridge_amount, dst_ts, target_names):
    """在 receiver 的 before+after 中找卖出桥接 token 的 swap。
    receiver_before 只找同区块的交易（time_dist <= SAME_BLOCK_THRESHOLD）。"""
    best = None
    for direction in ["receiver_before", "receiver_after"]:
        match = find_match(rec.get(direction, []), bridge_amount, dst_ts,
                           "sold", direction, target_names)
        if match:
            # receiver_before 必须是同区块（时间差很小）
            if direction == "receiver_before" and match["time_dist_s"] > SAME_BLOCK_THRESHOLD:
                continue
            if best is None or match["time_dist_s"] < best["time_dist_s"]:
                best = match
    return best


def extract_cost(buy_tx):
    summary = buy_tx.get("summary", {})
    return [{"token": s["token"], "amount": float(s["amount"])} for s in summary.get("sold", [])]


def extract_revenue(sell_tx):
    summary = sell_tx.get("summary", {})
    return [{"token": b["token"], "amount": float(b["amount"])} for b in summary.get("bought", [])]


def compute_time_gaps(rec, buy_match, sell_match):
    gaps = {}
    bridge_src_ts = rec.get("src_timestamp", "")
    bridge_dst_ts = rec.get("dst_timestamp", "")

    buy_ts = buy_match["tx"].get("ts", 0) if buy_match else 0
    sell_ts = sell_match["tx"].get("ts", 0) if sell_match else 0

    src_ts = dst_ts = 0
    if bridge_src_ts:
        try:
            src_ts = int(datetime.fromisoformat(bridge_src_ts).timestamp())
        except Exception:
            pass
    if bridge_dst_ts:
        try:
            dst_ts = int(datetime.fromisoformat(bridge_dst_ts).timestamp())
        except Exception:
            pass

    if buy_ts and src_ts:
        gaps["buy_to_bridge_seconds"] = src_ts - buy_ts
    if dst_ts and sell_ts:
        gaps["receive_to_sell_seconds"] = sell_ts - dst_ts
    if buy_ts and sell_ts:
        gaps["total_cycle_seconds"] = sell_ts - buy_ts
    if src_ts and dst_ts:
        gaps["bridge_latency_seconds"] = dst_ts - src_ts

    return gaps


def main():
    load_all_token_mappings()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)

    records = data["records"]
    print(f"总记录: {len(records)}")

    arbitrages = []
    unmatched = []

    stats = {"buy_and_sell": 0, "buy_only": 0, "sell_only": 0, "no_match": 0}
    token_miss = Counter()  # 没匹配到的 bridge token

    for rec in records:
        bridge_amount = float(rec["token_amount"]) if rec.get("token_amount") else 0
        bridge_symbol = rec.get("token_symbol", "")

        if not bridge_symbol:
            unmatched.append(rec)
            stats["no_match"] += 1
            continue

        target_names = get_token_names(bridge_symbol)

        # 解析跨链时间戳
        src_ts = dst_ts = 0
        try:
            src_ts = int(datetime.fromisoformat(rec["src_timestamp"]).timestamp())
        except Exception:
            pass
        try:
            dst_ts = int(datetime.fromisoformat(rec["dst_timestamp"]).timestamp())
        except Exception:
            pass

        buy_match = find_buy(rec, bridge_amount, src_ts, target_names)
        sell_match = find_sell(rec, bridge_amount, dst_ts, target_names)

        if buy_match and sell_match:
            buy_tx = buy_match["tx"]
            sell_tx = sell_match["tx"]

            arb_record = {
                "bridge": rec.get("bridge", ""),
                "id": rec["id"],
                "token_symbol": bridge_symbol,
                "token_amount": bridge_amount,
                "usd_amount": rec.get("usd_amount", 0),
                "src_sender": rec["src_sender"],
                "dst_receiver": rec["dst_receiver"],
                "src_tx_hash": rec.get("src_tx_hash"),
                "dst_tx_hash": rec.get("dst_tx_hash"),
                "src_timestamp": rec.get("src_timestamp"),
                "dst_timestamp": rec.get("dst_timestamp"),
                "match_type": "token_buy_and_sell",
                "buy": {
                    "chain": buy_tx.get("chain", ""),
                    "tx_id": buy_tx.get("hash") or buy_tx.get("sig", ""),
                    "ts": buy_tx.get("ts", 0),
                    "time": buy_tx.get("time", ""),
                    "direction": buy_match["direction"],
                    "matched_token": buy_match["matched_token"],
                    "matched_token_resolved": buy_match["matched_token_resolved"],
                    "matched_amount": buy_match["matched_amount"],
                    "amount_ratio": round(buy_match["ratio"], 6) if buy_match["ratio"] else 0,
                    "time_dist_s": buy_match["time_dist_s"],
                    "cost": extract_cost(buy_tx),
                    "full_summary": buy_tx.get("summary", {}),
                },
                "sell": {
                    "chain": sell_tx.get("chain", ""),
                    "tx_id": sell_tx.get("hash") or sell_tx.get("sig", ""),
                    "ts": sell_tx.get("ts", 0),
                    "time": sell_tx.get("time", ""),
                    "direction": sell_match["direction"],
                    "matched_token": sell_match["matched_token"],
                    "matched_token_resolved": sell_match["matched_token_resolved"],
                    "matched_amount": sell_match["matched_amount"],
                    "amount_ratio": round(sell_match["ratio"], 6) if sell_match["ratio"] else 0,
                    "time_dist_s": sell_match["time_dist_s"],
                    "revenue": extract_revenue(sell_tx),
                    "full_summary": sell_tx.get("summary", {}),
                },
            }

            for side_key in ["buy", "sell"]:
                match_obj = buy_match if side_key == "buy" else sell_match
                for k in ["position_before", "position_after"]:
                    if k in match_obj:
                        arb_record[side_key][k] = match_obj[k]

            arb_record["time_gaps"] = compute_time_gaps(rec, buy_match, sell_match)

            # P&L 估算
            costs = arb_record["buy"]["cost"]
            revenues = arb_record["sell"]["revenue"]
            cost_tokens = {c["token"]: c["amount"] for c in costs}
            rev_tokens = {r["token"]: r["amount"] for r in revenues}

            stables = {"USDC", "USDT", "DAI", "BUSD", "UST", "PYUSD", "USDe"}
            stable_cost = sum(v for k, v in cost_tokens.items()
                             if resolve_token(k) in stables)
            stable_rev = sum(v for k, v in rev_tokens.items()
                            if resolve_token(k) in stables)

            pnl = {}
            # 按 resolved symbol 匹配
            resolved_cost = {resolve_token(k): v for k, v in cost_tokens.items()}
            resolved_rev = {resolve_token(k): v for k, v in rev_tokens.items()}
            common = set(resolved_cost.keys()) & set(resolved_rev.keys())
            if common:
                for t in common:
                    pnl[t] = round(resolved_rev[t] - resolved_cost[t], 6)
            if stable_cost > 0 and stable_rev > 0:
                pnl["_stable_pnl"] = round(stable_rev - stable_cost, 6)
                pnl["_stable_pnl_pct"] = round(
                    (stable_rev - stable_cost) / stable_cost * 100, 4)

            native = {"ETH", "WETH", "SOL", "WSOL"}
            native_cost = sum(v for k, v in resolved_cost.items() if k in native)
            native_rev = sum(v for k, v in resolved_rev.items() if k in native)
            if native_cost > 0 and native_rev > 0:
                pnl["_native_pnl"] = round(native_rev - native_cost, 6)

            arb_record["pnl_estimate"] = pnl if pnl else None

            arbitrages.append(arb_record)
            stats["buy_and_sell"] += 1

        elif buy_match:
            stats["buy_only"] += 1
            unmatched.append(rec)
        elif sell_match:
            stats["sell_only"] += 1
            unmatched.append(rec)
        else:
            stats["no_match"] += 1
            token_miss[bridge_symbol] += 1
            unmatched.append(rec)

    # 排序
    arbitrages.sort(key=lambda x: -(float(x.get("usd_amount") or 0)))

    # 保存 detected
    arb_output = {
        "meta": {
            "version": "v3_token_match",
            "description": "基于 token 种类匹配的套利检测 (不要求金额匹配)",
            "total_bridge_records": len(records),
            "arbitrage_detected": len(arbitrages),
            "buy_and_sell": stats["buy_and_sell"],
            "buy_only": stats["buy_only"],
            "sell_only": stats["sell_only"],
            "no_match": stats["no_match"],
            "swap_requirement": "both sold AND bought must be non-empty",
            "token_match": "bridge token_symbol must appear in swap summary (with alias resolution)",
            "scoring": "time_dist (closest to bridge time wins)",
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "records": arbitrages,
    }
    with open(OUTPUT_ARB, "w", encoding="utf-8") as f:
        json.dump(arb_output, f, indent=2, ensure_ascii=False)

    # 保存 unmatched (全部)
    unmatched_output = {
        "meta": {
            "total_bridge_records": len(records),
            "unmatched_count": len(unmatched),
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "records": unmatched,
    }
    with open(OUTPUT_UNMATCHED, "w", encoding="utf-8") as f:
        json.dump(unmatched_output, f, indent=2, ensure_ascii=False)

    # 拆分 complete / incomplete
    complete = []
    incomplete = []
    for r in unmatched:
        sb = r.get("sender_before", [])
        sa = r.get("sender_after", [])
        rb = r.get("receiver_before", [])
        ra = r.get("receiver_after", [])
        if sb and sa and rb and ra:
            complete.append(r)
        else:
            incomplete.append(r)

    with open(OUTPUT_COMPLETE, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {"count": len(complete),
                     "updated": datetime.now(timezone.utc).isoformat()},
            "records": complete,
        }, f, indent=2, ensure_ascii=False)

    with open(OUTPUT_INCOMPLETE, "w", encoding="utf-8") as f:
        json.dump({
            "meta": {"count": len(incomplete),
                     "updated": datetime.now(timezone.utc).isoformat()},
            "records": incomplete,
        }, f, indent=2, ensure_ascii=False)

    # ===== 统计输出 =====
    print(f"\n{'=' * 60}")
    print(f"  Token Match 套利检测 v3")
    print(f"{'=' * 60}")
    print(f"  总跨链记录:      {len(records)}")
    print(f"  检测到套利:       {stats['buy_and_sell']}")
    print(f"  仅买入匹配:       {stats['buy_only']}")
    print(f"  仅卖出匹配:       {stats['sell_only']}")
    print(f"  无匹配:           {stats['no_match']}")
    print(f"  未匹配-完整:      {len(complete)}")
    print(f"  未匹配-不完整:    {len(incomplete)}")

    for name, path in [("detected", OUTPUT_ARB), ("unmatched", OUTPUT_UNMATCHED),
                        ("complete", OUTPUT_COMPLETE), ("incomplete", OUTPUT_INCOMPLETE)]:
        sz = path.stat().st_size / 1024 / 1024
        print(f"\n  {name}: {path.name} ({sz:.1f} MB)")

    # 方向统计
    buy_dirs = Counter()
    sell_dirs = Counter()
    for a in arbitrages:
        buy_dirs[a["buy"]["direction"]] += 1
        sell_dirs[a["sell"]["direction"]] += 1

    print(f"\n  买入方向分布:")
    for d, cnt in buy_dirs.most_common():
        print(f"    {d:<20s} {cnt:>4}")

    print(f"\n  卖出方向分布:")
    for d, cnt in sell_dirs.most_common():
        print(f"    {d:<20s} {cnt:>4}")

    # Token 分布
    print(f"\n  Top 20 套利 Token:")
    token_counts = Counter()
    for a in arbitrages:
        token_counts[a["token_symbol"]] += 1
    for t, cnt in token_counts.most_common(20):
        print(f"    {t:<25s} {cnt:>4}")

    # 未匹配 token
    print(f"\n  Top 20 未匹配 Token (no_match):")
    for t, cnt in token_miss.most_common(20):
        print(f"    {t:<25s} {cnt:>4}")

    # 时间窗口统计
    total_cycle = [a["time_gaps"]["total_cycle_seconds"] for a in arbitrages
                   if a.get("time_gaps", {}).get("total_cycle_seconds") is not None]
    if total_cycle:
        s = sorted(total_cycle)
        print(f"\n  完整周期 (买→卖):")
        print(f"    中位数: {s[len(s)//2]}s ({s[len(s)//2]/60:.1f}min)")
        print(f"    均值: {sum(s)/len(s):.0f}s ({sum(s)/len(s)/60:.1f}min)")
        print(f"    范围: {min(s)}s ~ {max(s)}s")

    # amount ratio 分布
    buy_ratios = [a["buy"]["amount_ratio"] for a in arbitrages if a["buy"]["amount_ratio"]]
    sell_ratios = [a["sell"]["amount_ratio"] for a in arbitrages if a["sell"]["amount_ratio"]]
    if buy_ratios:
        print(f"\n  买入 amount_ratio (swap_amount / bridge_amount):")
        print(f"    中位数: {sorted(buy_ratios)[len(buy_ratios)//2]:.4f}")
        print(f"    范围: {min(buy_ratios):.4f} ~ {max(buy_ratios):.4f}")
        # 在 0.85-1.15 范围内的比例
        in_range = sum(1 for r in buy_ratios if 0.85 <= r <= 1.15)
        print(f"    [0.85,1.15] 范围内: {in_range}/{len(buy_ratios)} ({in_range/len(buy_ratios)*100:.1f}%)")

    if sell_ratios:
        print(f"\n  卖出 amount_ratio (swap_amount / bridge_amount):")
        print(f"    中位数: {sorted(sell_ratios)[len(sell_ratios)//2]:.4f}")
        print(f"    范围: {min(sell_ratios):.4f} ~ {max(sell_ratios):.4f}")
        in_range = sum(1 for r in sell_ratios if 0.85 <= r <= 1.15)
        print(f"    [0.85,1.15] 范围内: {in_range}/{len(sell_ratios)} ({in_range/len(sell_ratios)*100:.1f}%)")

    # 样例
    print(f"\n  样例 (前5个):")
    for a in arbitrages[:5]:
        usd = float(a.get('usd_amount') or 0)
        amt = float(a.get('token_amount') or 0)
        print(f"\n    [{a['bridge']}] {a['token_symbol']} {amt:.4f}"
              f"  (${usd:.2f})")
        b = a["buy"]
        s = a["sell"]
        pos_b = b.get("position_before") or b.get("position_after", "?")
        pos_s = s.get("position_before") or s.get("position_after", "?")
        print(f"    BUY [{b['direction']}]: {b['chain']} 第{pos_b}笔  "
              f"买 {b['matched_token_resolved']} {b['matched_amount']:.4f}  "
              f"ratio={b['amount_ratio']}  Δt={b['time_dist_s']}s")
        print(f"      花费: {b['cost']}")
        print(f"    SELL [{s['direction']}]: {s['chain']} 第{pos_s}笔  "
              f"卖 {s['matched_token_resolved']} {s['matched_amount']:.4f}  "
              f"ratio={s['amount_ratio']}  Δt={s['time_dist_s']}s")
        print(f"      收入: {s['revenue']}")
        if a.get("pnl_estimate"):
            print(f"    P&L: {a['pnl_estimate']}")
        gaps = a.get("time_gaps", {})
        if gaps:
            print(f"    时间: 买→桥{gaps.get('buy_to_bridge_seconds','?')}s  "
                  f"桥延迟{gaps.get('bridge_latency_seconds','?')}s  "
                  f"收→卖{gaps.get('receive_to_sell_seconds','?')}s  "
                  f"总{gaps.get('total_cycle_seconds','?')}s")


if __name__ == "__main__":
    main()
