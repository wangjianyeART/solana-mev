#!/usr/bin/env python3
"""
彻底修 ERC20 decimals/symbol bug:
  1. 扫 matched_context_parsed.json 所有未知 token (token==contract, 走 fallback)
  2. 并发查 Etherscan tokeninfo 拿真 symbol + decimals
  3. 写入 erc20_cache.json
  4. Walk 所有 events, 对已知合约:
       - amount *= 10**(18 - real_decimals)   # 修正之前按 18 decimals 错算的值
       - token = real_symbol                  # 把 0x... 换成真符号
     再用修正后 events 重建 parsed['summary'] (sold/bought)
  5. 写回 matched_context_parsed.json
后续需 rerun pipeline.
"""

import json
import time
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

ROOT = Path(__file__).parent
BASE = ROOT / "use" / "portal_full" / "recent_30d" / "matched"
PARSED = BASE / "matched_context_parsed.json"
CACHE_FILE = ROOT / "use" / "portal_full" / "erc20_cache.json"

ETHERSCAN_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"
BACKUP_SUFFIX = ".bak_before_decimals_fix"


def etherscan_tokeninfo(addr):
    url = "https://api.etherscan.io/v2/api"
    for attempt in range(3):
        try:
            r = requests.get(url, params={
                "chainid": 1, "module": "token", "action": "tokeninfo",
                "contractaddress": addr, "apikey": ETHERSCAN_KEY,
            }, timeout=20)
            d = r.json()
            if d.get("status") == "1" and d.get("result"):
                res = d["result"][0] if isinstance(d["result"], list) else d["result"]
                sym = (res.get("symbol") or "").strip() or None
                dec_raw = res.get("divisor") or res.get("decimals")
                try:
                    dec = int(dec_raw)
                except (ValueError, TypeError):
                    dec = None
                if sym and dec is not None:
                    return {"symbol": sym, "decimals": dec}
            msg = d.get("message", "").lower()
            if "rate limit" in msg or "max calls" in msg:
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(1)
    return None


def rpc_fallback_decimals(addr):
    """Etherscan 查不到时用 Chainstack RPC eth_call."""
    url = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"
    try:
        r = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": addr, "data": "0x313ce567"}, "latest"],
        }, timeout=15)
        res = r.json().get("result")
        dec = int(res, 16) if res and res != "0x" else None
        r2 = requests.post(url, json={
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": addr, "data": "0x95d89b41"}, "latest"],
        }, timeout=15)
        sres = r2.json().get("result")
        sym = None
        if sres and len(sres) > 130:
            try:
                hex_str = sres[130:]
                sym = bytes.fromhex(hex_str).decode("utf-8").rstrip("\x00").strip()
                if not sym or not sym.isprintable():
                    sym = None
            except Exception:
                sym = None
        if dec is not None and sym:
            return {"symbol": sym, "decimals": dec}
    except Exception:
        pass
    return None


def fetch_one(addr):
    info = etherscan_tokeninfo(addr)
    if not info:
        info = rpc_fallback_decimals(addr)
    return addr, info


def collect_unknown(doc):
    unknown = set()
    for r in doc["records"]:
        parsed = r.get("eth_parsed")
        if parsed and not parsed.get("error"):
            for e in parsed.get("events") or []:
                tok, c = e.get("token", ""), e.get("contract", "")
                if tok == c and tok.startswith("0x") and len(tok) == 42:
                    unknown.add(tok)
        for ctx in r.get("eth_context") or []:
            p = ctx.get("parsed")
            if p and not p.get("error"):
                for e in p.get("events") or []:
                    tok, c = e.get("token", ""), e.get("contract", "")
                    if tok == c and tok.startswith("0x") and len(tok) == 42:
                        unknown.add(tok)
    return unknown


def fix_parsed_events(parsed, cache):
    """Return True if events were modified."""
    changed = False
    events = parsed.get("events") or []
    for e in events:
        tok = e.get("token", "")
        contract = e.get("contract", "")
        if tok == contract and contract in cache:
            info = cache[contract]
            real_dec = info["decimals"]
            if real_dec != 18:
                # 旧 amount = raw / 10^18, 真实 = raw / 10^real_dec
                # multiplier = 10^(18 - real_dec)
                e["amount"] = e["amount"] * (10 ** (18 - real_dec))
            e["token"] = info["symbol"]
            changed = True
    return changed, events


def rebuild_summary(parsed):
    """基于 events 重建 summary.sold/bought, perspective = parsed['from']."""
    perspective = (parsed.get("from") or "").lower()
    if not perspective:
        return
    outflow, inflow = {}, {}
    for e in parsed.get("events") or []:
        if e.get("type") not in ("erc20_transfer", "eth_transfer"):
            continue
        token = e.get("token", "?")
        amt = e.get("amount", 0)
        if e.get("from", "").lower() == perspective:
            outflow[token] = outflow.get(token, 0) + amt
        if e.get("to", "").lower() == perspective:
            inflow[token] = inflow.get(token, 0) + amt
    sold, bought = [], []
    for t in set(outflow.keys()) | set(inflow.keys()):
        if outflow.get(t, 0) > 1e-8:
            sold.append({"token": t, "amount": outflow[t]})
        if inflow.get(t, 0) > 1e-8:
            bought.append({"token": t, "amount": inflow[t]})
    parsed["summary"] = {"sold": sold, "bought": bought}


def main():
    print("加载 matched_context_parsed.json ...")
    doc = json.load(open(PARSED, encoding="utf-8"))
    print(f"  records: {len(doc['records'])}")

    print("\n扫描未知 ERC20 合约 ...")
    unknown = collect_unknown(doc)
    print(f"  未知: {len(unknown)}")

    # 加载现有 cache (作为去重)
    cache = {}
    if CACHE_FILE.exists():
        raw = json.load(open(CACHE_FILE))
        for addr, info in raw.items():
            if info.get("symbol") and not info["symbol"].startswith("0x"):
                cache[addr.lower()] = {
                    "symbol": info["symbol"],
                    "decimals": int(info.get("decimals", 18)),
                }
    print(f"  已有 good cache: {len(cache)}")
    todo = [a for a in unknown if a.lower() not in cache]
    print(f"  待查: {len(todo)}")

    if todo:
        print("\n并发查 Etherscan + RPC fallback ...")
        t0 = time.time()
        done = err = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(fetch_one, a): a for a in todo}
            for f in as_completed(futs):
                addr, info = f.result()
                done += 1
                if info:
                    cache[addr.lower()] = info
                else:
                    err += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(todo)} err={err} ({done/(time.time()-t0):.1f}/s)")
        print(f"完成: {done}, 查不到={err}, {time.time()-t0:.1f}s")

        # 写回 cache (保留原有 symbol 格式)
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        out_cache = {}
        if CACHE_FILE.exists():
            out_cache = json.load(open(CACHE_FILE))
        for addr, info in cache.items():
            out_cache[addr] = {"symbol": info["symbol"], "decimals": info["decimals"]}
        json.dump(out_cache, open(CACHE_FILE, "w"), ensure_ascii=False, indent=2)
        print(f"  更新 {CACHE_FILE.name}: {len(out_cache)} 条")

    # Walk + fix
    print("\n修复 parsed events + summary ...")
    shutil.copy2(PARSED, str(PARSED) + BACKUP_SUFFIX)
    fixed_primary = 0
    fixed_context = 0
    decimals_changed = 0
    tokens_renamed = 0

    def _count_changes(parsed):
        nonlocal decimals_changed, tokens_renamed
        for e in parsed.get("events") or []:
            tok = e.get("token", "")
            contract = e.get("contract", "")
            if contract in cache:
                info = cache[contract]
                if tok == contract:
                    tokens_renamed += 1
                    if info["decimals"] != 18:
                        decimals_changed += 1

    for r in doc["records"]:
        p = r.get("eth_parsed")
        if p and not p.get("error"):
            _count_changes(p)
            changed, _ = fix_parsed_events(p, cache)
            if changed:
                rebuild_summary(p)
                fixed_primary += 1
        for ctx in r.get("eth_context") or []:
            p = ctx.get("parsed")
            if p and not p.get("error"):
                _count_changes(p)
                changed, _ = fix_parsed_events(p, cache)
                if changed:
                    rebuild_summary(p)
                    fixed_context += 1

    print(f"  primary eth_parsed 修正: {fixed_primary}")
    print(f"  context eth_parsed 修正: {fixed_context}")
    print(f"  总 token 重命名: {tokens_renamed}")
    print(f"  非 18 decimals 校正: {decimals_changed}")

    doc["meta"]["decimals_fixed"] = True
    doc["meta"]["decimals_fix_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

    print("\n写回 parsed.json ...")
    json.dump(doc, open(PARSED, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"完成. 原文件备份 {BACKUP_SUFFIX}")
    print("下一步: rerun detect_arbitrage → tag_subtypes → apply_greedy_dedup → compute_pnl")


if __name__ == "__main__":
    main()
