#!/usr/bin/env python3
"""
对嫌疑 ETH token 查 Birdeye /defi/token_overview 拿真实 decimals,
对比 parser 假定 (18), 写 decimals_fix_map.json:
  {token: {"real": 6, "assumed": 18, "multiplier": 1e12}}
然后 patch 所有候选的 sold/bought amount。
"""

import json
import time
import requests
from pathlib import Path

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_30d" / "matched" / "arbitrage"
SUSPECTS = ROOT / "decimals_suspects.json"
FIX_MAP = ROOT / "decimals_fix_map.json"
CANDIDATES = ROOT / "arbitrage_candidates_1pct.json"

API = "https://public-api.birdeye.so/defi/token_overview"
ASSUMED_DECIMALS = 18


def load_key():
    env = Path(__file__).parent.parent / ".env"
    for line in env.read_text().splitlines():
        if line.startswith("BIRDEYE_API_KEY"):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("no BIRDEYE_API_KEY")


def fetch_decimals(addr, api_key):
    params = {"address": addr}
    headers = {"X-API-KEY": api_key, "x-chain": "ethereum"}
    r = requests.get(API, params=params, headers=headers, timeout=15)
    if r.status_code != 200:
        return None, f"http_{r.status_code}"
    data = (r.json() or {}).get("data") or {}
    dec = data.get("decimals")
    sym = data.get("symbol")
    return (dec, sym), None


def audit():
    key = load_key()
    suspects = json.load(open(SUSPECTS))
    print(f"审计 {len(suspects)} 个 ETH token...")

    fix = {}
    for tok in suspects.keys():
        (info, err) = fetch_decimals(tok, key)
        if err:
            print(f"  {tok}: ERROR {err}")
            continue
        dec, sym = info
        if dec is None:
            print(f"  {tok} ({sym}): decimals 未返回")
            continue
        diff = ASSUMED_DECIMALS - dec
        mult = 10 ** diff if diff > 0 else 1
        fix[tok] = {"symbol": sym, "real": dec, "assumed": ASSUMED_DECIMALS, "multiplier": mult}
        print(f"  {tok} ({sym}): real={dec}, assumed={ASSUMED_DECIMALS}, ×{mult:g}")
        time.sleep(0.1)

    json.dump(fix, open(FIX_MAP, "w"), indent=2)
    print(f"\n写入 {FIX_MAP.name}")
    return fix


def patch_candidates(fix):
    """把 fix map 应用到 candidates 文件的所有 swap amount"""
    if not fix:
        print("无需 patch")
        return
    patched_tokens = {k: v for k, v in fix.items() if v["multiplier"] != 1}
    if not patched_tokens:
        print("没有 token 需要修正")
        return

    print(f"\n对 candidates 打 patch, 影响 {len(patched_tokens)} token...")
    data = json.load(open(CANDIDATES, encoding="utf-8"))
    cands = data.get("candidates", [])

    touched = 0
    for c in cands:
        for field in ["entry_swaps", "exit_swaps", "entry_swaps_greedy", "exit_swaps_greedy"]:
            for s in c.get(field) or []:
                if s.get("chain") != "ETH":
                    continue
                for it in (s.get("sold") or []) + (s.get("bought") or []):
                    tok = it.get("token")
                    if tok in patched_tokens and isinstance(it.get("amount"), (int, float)):
                        mult = patched_tokens[tok]["multiplier"]
                        it["amount"] = it["amount"] * mult
                        touched += 1

    json.dump(data, open(CANDIDATES, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"patch 完成, 修正 {touched} 个 amount 字段")


if __name__ == "__main__":
    fix = audit()
    patch_candidates(fix)
