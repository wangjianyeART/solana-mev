#!/usr/bin/env python3
"""
用 Wormhole API 的权威配对 (eth_hash ↔ sol_sig) 拼成 matched.json。
- 从 portal_sol_raw/ 按需取 SOL raw 解析
- 与 eth_parsed.json 里的 ETH record 拼合
- 输出: recent_1y/matched/matched.json + missing_sol_sigs.json

用法: python wormhole_data/build_matched_1y.py
"""

import json
import base58
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

DIR = Path(__file__).parent / "use" / "portal_full"
RECENT = DIR / "recent_1y"
SOL_RAW_ALL = DIR / "portal_sol_raw"  # 34 万笔全量 1y cache
ETH_PARSED = RECENT / "eth_parsed.json"
WH_LOOKUP = RECENT / "eth_to_sol_via_api.json"
RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
MATCH_DIR = RECENT / (f"matched_{RUN_TAG}" if RUN_TAG else "matched")
OUT = MATCH_DIR / "matched.json"
MISSING_OUT = MATCH_DIR / "missing_sol_sigs.json"

MATCH_DIR.mkdir(exist_ok=True)

# ─── SOL 解析 (与 parse_1y.py 保持一致) ───

PORTAL_PROGRAM = "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb"
MINT_SYMBOLS = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH(Wormhole)",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC(Wormhole)",
    "A9mUU4qviSctJVPJdBGS2hwp7P816M25QVRnRMCFNOJf": "USDT(Wormhole)",
    "FjYfhJx8BcAzeiY3APg8E2QZiLW4F4uqz9JoSBXr1BoP": "USDC(Wormhole)",
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "stSOL",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "jitoSOL",
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": "bSOL",
}
WORMHOLE_CHAINS = {
    1: "Solana", 2: "Ethereum", 3: "Terra", 4: "BSC", 5: "Polygon",
    6: "Avalanche", 7: "Oasis", 8: "Algorand", 9: "Aurora", 10: "Fantom",
    11: "Karura", 12: "Acala", 13: "Klaytn", 14: "Celo", 15: "NEAR",
    16: "Moonbeam", 22: "Aptos", 23: "Arbitrum", 24: "Optimism",
    28: "XPLA", 30: "Base", 32: "Sei", 34: "Scroll", 35: "Mantle",
    36: "Blast", 40: "Sui",
}


def get_mint_symbol(mint):
    return MINT_SYMBOLS.get(mint, mint)


def parse_sol_tx(entry):
    data = entry.get("data")
    if not data:
        return None
    sig = entry["sig"]
    meta = data.get("meta", {})
    tx_msg = data.get("transaction", {}).get("message", {})
    block_time = data.get("blockTime", 0)
    slot = data.get("slot", 0)
    if meta.get("err"):
        return None
    logs = meta.get("logMessages", [])
    signers = [k["pubkey"] for k in tx_msg.get("accountKeys", []) if k.get("signer")]
    sender = signers[0] if signers else ""

    has_mint = any("Instruction: MintTo" in l for l in logs)
    has_burn = any("Instruction: Burn" in l for l in logs)
    has_approve = any("Instruction: Approve" in l for l in logs)
    has_transfer = any("Instruction: Transfer" in l for l in logs)

    if has_mint and not has_burn:
        direction = "inbound"
    elif has_burn and not has_mint:
        direction = "outbound"
    elif has_transfer and has_approve:
        direction = "outbound"
    elif has_transfer and not has_mint and not has_burn:
        direction = "outbound"
    elif has_mint and has_burn:
        direction = "swap"
    else:
        direction = "unknown"

    dst_chain = None
    dst_recipient = None
    if direction == "outbound":
        for inst in tx_msg.get("instructions", []):
            if inst.get("programId") != PORTAL_PROGRAM:
                continue
            try:
                raw = base58.b58decode(inst["data"])
                if len(raw) >= 55:
                    chain_id = int.from_bytes(raw[-2:], "little")
                    dst_chain = WORMHOLE_CHAINS.get(chain_id, f"chain_{chain_id}")
                    recip_bytes = raw[21:53]
                    if chain_id == 1:
                        dst_recipient = base58.b58encode(recip_bytes).decode()
                    else:
                        dst_recipient = "0x" + recip_bytes[-20:].hex()
            except Exception:
                pass
    elif direction == "inbound":
        dst_chain = "Solana"

    pre = {(b["accountIndex"], b["mint"]): (float(b.get("uiTokenAmount",{}).get("uiAmountString","0") or 0), b.get("owner","")) for b in meta.get("preTokenBalances", [])}
    post = {(b["accountIndex"], b["mint"]): (float(b.get("uiTokenAmount",{}).get("uiAmountString","0") or 0), b.get("owner","")) for b in meta.get("postTokenBalances", [])}
    token_changes = []
    for k in set(pre) | set(post):
        pre_a, pre_o = pre.get(k, (0, ""))
        post_a, post_o = post.get(k, (0, ""))
        diff = post_a - pre_a
        if diff != 0:
            mint = k[1]
            token_changes.append({
                "mint": mint,
                "symbol": get_mint_symbol(mint),
                "owner": pre_o or post_o,
                "change": diff,
            })

    user_changes = [tc for tc in token_changes if tc["owner"] == sender]
    result = {
        "sig": sig,
        "ts": block_time,
        "slot": slot,
        "sender": sender,
        "direction": direction,
        "src_chain": "Solana",
        "dst_chain": dst_chain,
        "token_changes": token_changes,
        "summary_user": [
            {"mint": tc["mint"], "symbol": tc["symbol"], "change": tc["change"]}
            for tc in user_changes
        ],
    }
    if dst_recipient:
        result["dst_recipient"] = dst_recipient
    return result


# ─── MAIN ───

def main():
    t0 = time.time()
    print("=" * 60)

    print("[1/4] 加载 Wormhole lookup ...")
    if not WH_LOOKUP.exists():
        print(f"  ERROR: 缺少 {WH_LOOKUP}")
        return
    wh = json.load(open(WH_LOOKUP, encoding="utf-8"))
    print(f"  权威配对数: {len(wh)}")

    # 需要取的 SOL sigs
    need_sigs = set()
    for v in wh.values():
        s = v.get("sol_sig")
        if s:
            need_sigs.add(s)
    print(f"  需要 SOL sigs: {len(need_sigs)}")

    print("[2/4] 加载 eth_parsed ...")
    eth_doc = json.load(open(ETH_PARSED, encoding="utf-8"))
    eth_by_hash = {r["hash"]: r for r in eth_doc["transactions"]}
    print(f"  eth_by_hash: {len(eth_by_hash)}")

    print("[3/4] 扫描 portal_sol_raw 取需要的 sigs ...")
    sol_entries = {}  # sig -> entry
    batches = sorted(SOL_RAW_ALL.glob("batch_*.json"))
    for i, bp in enumerate(batches, 1):
        batch = json.load(open(bp, encoding="utf-8"))
        for e in batch:
            sig = e.get("sig")
            if sig in need_sigs:
                sol_entries[sig] = e
        if i % 50 == 0:
            print(f"  {i}/{len(batches)} batches | matched {len(sol_entries)}/{len(need_sigs)} ({time.time()-t0:.0f}s)")
        if len(sol_entries) == len(need_sigs):
            print(f"  早退: 全部命中在 {i}/{len(batches)}")
            break

    missing = need_sigs - set(sol_entries.keys())
    print(f"  命中: {len(sol_entries)}/{len(need_sigs)} | 缺失: {len(missing)}")
    if missing:
        with open(MISSING_OUT, "w", encoding="utf-8") as f:
            json.dump(sorted(missing), f, ensure_ascii=False, indent=2)
        print(f"  缺失 sigs 已写入 {MISSING_OUT}")

    print("[4/4] 组合 matched records ...")
    records = []
    skip_no_eth = skip_no_sol = skip_parse_fail = 0
    for eth_hash, wh_info in wh.items():
        eth_rec = eth_by_hash.get(eth_hash)
        if not eth_rec:
            skip_no_eth += 1
            continue
        sol_sig = wh_info.get("sol_sig")
        sol_entry = sol_entries.get(sol_sig)
        if not sol_entry:
            skip_no_sol += 1
            continue
        sol_rec = parse_sol_tx(sol_entry)
        if not sol_rec:
            skip_parse_fail += 1
            continue

        # 拼合
        direction = wh_info["direction"]  # "SOL→ETH" or "ETH→SOL"
        sol_ts = sol_rec.get("ts") or 0
        eth_ts = eth_rec.get("ts") or 0
        latency = abs(eth_ts - sol_ts) if (sol_ts and eth_ts) else None

        # bridge 资产(ETH 侧释放/转入的代币)
        eth_transfers = eth_rec.get("transfers", [])
        portal_tokens = [t for t in eth_transfers if t.get("role") in ("portal_in", "portal_out")]
        eth_token = portal_tokens[0] if portal_tokens else (eth_transfers[0] if eth_transfers else None)

        sol_tokens = sol_rec.get("token_changes", [])
        # 选最大变动作为 bridge 代币(排除 user SOL fee 变动)
        sol_user_change = max(sol_rec.get("summary_user", []), key=lambda t: abs(t.get("change", 0)), default=None)

        if direction == "SOL→ETH":
            portal_out_to = next((t["to"] for t in eth_transfers if t.get("role") == "portal_out"), None)
            src_sender_v = sol_rec.get("sender")
            src_ts_v = sol_ts
            dst_receiver_v = portal_out_to or sol_rec.get("dst_recipient")
            dst_ts_v = eth_ts
        else:  # ETH→SOL
            src_sender_v = eth_rec.get("from")
            src_ts_v = eth_ts
            dst_receiver_v = sol_rec.get("sender")
            dst_ts_v = sol_ts
        sender_side = {
            "src_sender": src_sender_v,
            "src_ts": src_ts_v,
            "dst_receiver": dst_receiver_v,
            "dst_ts": dst_ts_v,
        }

        rec = {
            "direction": direction,
            "sol_sig": sol_sig,
            "eth_hash": eth_hash,
            "sol_ts": sol_ts,
            "eth_ts": eth_ts,
            "latency_seconds": latency,
            "sol_sender": sol_rec.get("sender"),
            "sol_dst_recipient": sol_rec.get("dst_recipient"),
            "eth_from": eth_rec.get("from"),
            "eth_dst_recipient": (eth_rec.get("wormhole") or {}).get("dst_recipient"),
            "src_sender": sender_side["src_sender"],
            "dst_receiver": sender_side["dst_receiver"],
            "sol_direction": sol_rec.get("direction"),
            "eth_direction": eth_rec.get("direction"),
            "sol_user_change": sol_user_change,
            "eth_transfer": {
                "token": eth_token["token"] if eth_token else None,
                "amount": eth_token["amount"] if eth_token else None,
                "contract": eth_token["contract"] if eth_token else None,
            } if eth_token else None,
            "eth_wormhole_payload": eth_rec.get("wormhole"),
            "src_chain_id": wh_info["src_chain"],
            "tgt_chain_id": wh_info["tgt_chain"],
        }
        records.append(rec)

    records.sort(key=lambda r: -(r.get("eth_ts") or 0))

    # 统计
    dirs = defaultdict(int)
    for r in records:
        dirs[r["direction"]] += 1

    output = {
        "meta": {
            "total": len(records),
            "source": "wormholescan API authoritative pair",
            "directions": dict(dirs),
            "skip_no_eth": skip_no_eth,
            "skip_no_sol": skip_no_sol,
            "skip_parse_fail": skip_parse_fail,
            "missing_sol_sigs": len(missing),
            "time_range": f"{datetime.fromtimestamp(records[-1]['eth_ts'], tz=timezone.utc).isoformat()} ~ {datetime.fromtimestamp(records[0]['eth_ts'], tz=timezone.utc).isoformat()}" if records else "",
            "updated": datetime.now(timezone.utc).isoformat(),
        },
        "records": records,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  matched: {len(records)}")
    print(f"  dirs: {dict(dirs)}")
    print(f"  skipped (no_eth={skip_no_eth}, no_sol={skip_no_sol}, parse_fail={skip_parse_fail})")
    print(f"  missing sol sigs: {len(missing)}")
    print(f"  输出: {OUT}")
    print(f"  耗时: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
