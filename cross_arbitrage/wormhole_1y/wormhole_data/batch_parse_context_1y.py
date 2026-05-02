#!/usr/bin/env python3
"""
Stage 6 (1y): 批量解析 matched_context.json 的 both_ok 子集所有 context sig/hash。

输入:
  use/portal_full/recent_1y/matched/matched_context.json  (~1M recs 但只取 both_ok)
  use/portal_full/erc20_cache.json                        (ETH decimals 缓存)

输出 (JSONL,append-only,可 resume):
  use/portal_full/recent_1y/matched/context_parse_cache/sol_parsed.jsonl
  use/portal_full/recent_1y/matched/context_parse_cache/eth_parsed.jsonl
  use/portal_full/recent_1y/matched/context_parse_cache/parse_errors.json

每行 = 一个解析后的 tx (含 summary.sold / summary.bought)。

用法: python wormhole_data/batch_parse_context_1y.py [--strict]
  --strict: 只跑 66K strict 子集 (默认跑 77K both_ok)
"""

import argparse
import json
import os
import sys
import time
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_1y" / "matched"
INPUT = ROOT / "matched_context.json"
CACHE_DIR = ROOT / "context_parse_cache"
SOL_OUT = CACHE_DIR / "sol_parsed.jsonl"
ETH_OUT = CACHE_DIR / "eth_parsed.jsonl"
ERR_OUT = CACHE_DIR / "parse_errors.json"

ERC20_CACHE_FILE = Path(__file__).parent / "use" / "portal_full" / "erc20_cache.json"

SOL_RPC = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"
ETH_RPC = "https://ethereum-mainnet.core.chainstack.com/c6f9a8579dc240ea4e8c7d56d1236d0c"

# Chainstack member RPS = 250 总预算 (SOL + ETH 共享)
GLOBAL_RPS = 240  # 留一点余量
# Chainstack getTransaction ~1-1.5s/req → 需要 240-300 workers 才能打满 240 RPS
SOL_WORKERS = 150
ETH_WORKERS = 80

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

MINT_SYMBOLS = {
    "So11111111111111111111111111111111111111112": "SOL",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": "BONK",
    "7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs": "WETH",
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": "WBTC",
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": "jitoSOL",
}
KNOWN_PROGRAMS = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6",
    "JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter v4",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": "Raydium AMM",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": "Raydium CLMM",
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": "Raydium CPMM",
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": "Orca Whirlpool",
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": "Meteora DLMM",
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": "Meteora Pools",
    "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb": "Wormhole Portal",
    "NTtAaoDJhkeHeaVUHnyhwbPNAN6WgBpHkHBTc6d7vLu": "Wormhole NTT",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": "SPL Token",
}
ETH_TOKEN_CACHE = {}


def load_erc20_cache():
    if ERC20_CACHE_FILE.exists():
        data = json.load(open(ERC20_CACHE_FILE, encoding="utf-8"))
        for addr, info in data.items():
            ETH_TOKEN_CACHE[addr.lower()] = (info.get("symbol") or addr, info.get("decimals") or 18)


def get_eth_token_info(addr):
    addr = addr.lower()
    if addr in ETH_TOKEN_CACHE:
        return ETH_TOKEN_CACHE[addr]
    ETH_TOKEN_CACHE[addr] = (addr, 18)
    return (addr, 18)


# ── 全局 RPS 限速 (SOL + ETH 共享) ──
class RateLimiter:
    def __init__(self, rps):
        self.min_interval = 1.0 / rps
        self._lock = threading.Lock()
        self._next_t = 0.0

    def wait(self):
        with self._lock:
            now = time.time()
            if self._next_t <= now:
                self._next_t = now + self.min_interval
                return
            wait_s = self._next_t - now
            self._next_t += self.min_interval
        time.sleep(wait_s)


limiter = RateLimiter(GLOBAL_RPS)


def _rpc(url, method, params, retries=3):
    for attempt in range(retries):
        limiter.wait()
        try:
            resp = requests.post(url, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            }, timeout=30)
            if resp.status_code == 429:
                time.sleep(1 * (attempt + 1))
                continue
            data = resp.json()
            if "error" in data:
                code = (data.get("error") or {}).get("code", 0)
                if code == 429 or code == -32005:
                    time.sleep(1 * (attempt + 1))
                    continue
                return None, data["error"].get("message", "err")[:60]
            return data.get("result"), None
        except Exception as e:
            if attempt == retries - 1:
                return None, str(e)[:60]
            time.sleep(0.5)
    return None, "retries"


def parse_sol_tx(sig):
    result, err = _rpc(SOL_RPC, "getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
    }])
    if err or not result:
        return {"sig": sig, "error": err or "rpc_failed"}

    meta = result.get("meta") or {}
    tx_msg = (result.get("transaction") or {}).get("message") or {}
    block_time = result.get("blockTime", 0)
    slot = result.get("slot", 0)

    if meta.get("err"):
        return {"sig": sig, "ts": block_time, "slot": slot, "error": "tx_failed"}

    account_keys = tx_msg.get("accountKeys") or []
    accounts = []
    for ak in account_keys:
        if isinstance(ak, dict):
            accounts.append(ak.get("pubkey", ""))
        else:
            accounts.append(ak)

    pre_balances = meta.get("preBalances", [])
    post_balances = meta.get("postBalances", [])
    sol_changes = []
    for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
        diff = (post - pre) / 1e9
        if abs(diff) > 1e-9 and i < len(accounts):
            sol_changes.append({"account": accounts[i], "change": round(diff, 9)})

    pre_tokens = {(t["accountIndex"], t.get("mint", "")): t for t in meta.get("preTokenBalances", [])}
    post_tokens = {(t["accountIndex"], t.get("mint", "")): t for t in meta.get("postTokenBalances", [])}
    all_keys = set(pre_tokens.keys()) | set(post_tokens.keys())
    token_changes = []
    for key in all_keys:
        pre_t = pre_tokens.get(key, {})
        post_t = post_tokens.get(key, {})
        mint = pre_t.get("mint") or post_t.get("mint", "")
        owner = pre_t.get("owner") or post_t.get("owner", "")
        pre_amt = float((pre_t.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        post_amt = float((post_t.get("uiTokenAmount") or {}).get("uiAmount") or 0)
        change = post_amt - pre_amt
        if abs(change) > 1e-10:
            token_changes.append({
                "mint": mint,
                "symbol": MINT_SYMBOLS.get(mint, mint),
                "owner": owner,
                "change": change,
            })

    programs = []
    for ix in tx_msg.get("instructions", []):
        pid = ix.get("programId", "")
        name = KNOWN_PROGRAMS.get(pid, "")
        if name and name not in programs:
            programs.append(name)
    for ix in meta.get("innerInstructions", []):
        for inner in ix.get("instructions", []):
            pid = inner.get("programId", "")
            name = KNOWN_PROGRAMS.get(pid, "")
            if name and name not in programs:
                programs.append(name)

    signer = ""
    for ak in account_keys:
        if isinstance(ak, dict) and ak.get("signer"):
            signer = ak.get("pubkey", "")
            break
    if not signer and accounts:
        signer = accounts[0]

    sold, bought = [], []
    for tc in token_changes:
        if tc.get("owner") != signer:
            continue
        if tc["change"] < -1e-8:
            sold.append({"token": tc["symbol"], "amount": -tc["change"]})
        elif tc["change"] > 1e-8:
            bought.append({"token": tc["symbol"], "amount": tc["change"]})

    return {
        "sig": sig,
        "ts": block_time,
        "slot": slot,
        "fee_sol": meta.get("fee", 0) / 1e9,
        "signer": signer,
        "programs": programs,
        "token_changes": token_changes,
        "sol_changes": sol_changes[:5],
        "summary": {"sold": sold, "bought": bought},
    }


def parse_eth_tx(txhash):
    receipt, err = _rpc(ETH_RPC, "eth_getTransactionReceipt", [txhash])
    if err or not receipt:
        return {"hash": txhash, "error": err or "rpc_failed"}
    tx_detail, err2 = _rpc(ETH_RPC, "eth_getTransactionByHash", [txhash])
    if err2 or not tx_detail:
        tx_detail = {}

    tx_from = (tx_detail.get("from") or "").lower()
    tx_to = (tx_detail.get("to") or "").lower()
    try:
        tx_value = int(tx_detail.get("value", "0x0"), 16)
    except Exception:
        tx_value = 0
    try:
        block_num = int(receipt.get("blockNumber", "0x0"), 16)
    except Exception:
        block_num = 0
    try:
        gas_used = int(receipt.get("gasUsed", "0x0"), 16)
        gas_price = int(receipt.get("effectiveGasPrice", "0x0"), 16)
        fee_eth = gas_used * gas_price / 1e18
    except Exception:
        gas_used = 0; fee_eth = 0
    status = "success" if receipt.get("status") == "0x1" else "failed"

    block_ts = 0
    for log in receipt.get("logs", []):
        bt = log.get("blockTimestamp")
        if bt:
            try:
                block_ts = int(bt, 16)
            except Exception:
                pass
            break

    events = []
    eth_value = tx_value / 1e18
    if eth_value > 0:
        events.append({
            "type": "eth_transfer", "token": "ETH",
            "from": tx_from, "to": tx_to, "amount": eth_value,
        })

    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if not topics or topics[0] != TRANSFER_TOPIC or len(topics) != 3:
            continue
        contract = log.get("address", "").lower()
        frm = "0x" + topics[1][-40:]
        to = "0x" + topics[2][-40:]
        try:
            raw = int(log.get("data", "0x0"), 16)
        except Exception:
            raw = 0
        sym, dec = get_eth_token_info(contract)
        amount = raw / (10 ** dec) if dec else raw
        events.append({
            "type": "erc20_transfer", "token": sym, "contract": contract,
            "from": frm, "to": to, "amount": amount,
        })

    # 以 tx_from 视角做 summary (后面 detect 阶段可按需重新聚合)
    sold, bought = [], []
    outflow, inflow = {}, {}
    perspective = tx_from
    for e in events:
        token = e.get("token", "?")
        if e.get("from", "").lower() == perspective:
            outflow[token] = outflow.get(token, 0) + e["amount"]
        if e.get("to", "").lower() == perspective:
            inflow[token] = inflow.get(token, 0) + e["amount"]
    for t in set(outflow.keys()) | set(inflow.keys()):
        if outflow.get(t, 0) > 1e-8:
            sold.append({"token": t, "amount": outflow[t]})
        if inflow.get(t, 0) > 1e-8:
            bought.append({"token": t, "amount": inflow[t]})

    return {
        "hash": txhash,
        "ts": block_ts,
        "block": block_num,
        "status": status,
        "from": tx_from,
        "to": tx_to,
        "fee_eth": fee_eth,
        "gas_used": gas_used,
        "events": events,
        "summary": {"sold": sold, "bought": bought},
    }


def load_cache_keys(path):
    """读 JSONL 取所有已解析的 key (sig 或 hash)。"""
    if not path.exists():
        return set()
    keys = set()
    key_field = "sig" if "sol_" in path.name else "hash"
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                k = obj.get(key_field)
                if k:
                    keys.add(k)
            except Exception:
                continue
    return keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="只跑 strict 子集 (66K)")
    args = ap.parse_args()

    load_erc20_cache()
    print(f"ERC20 cache loaded: {len(ETH_TOKEN_CACHE)}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"加载 matched_context.json ...")
    t0 = time.time()
    d = json.load(open(INPUT, encoding="utf-8"))
    records = d["records"]
    print(f"  total: {len(records)}  ({time.time()-t0:.0f}s)")

    # 筛子集
    if args.strict:
        subset = [r for r in records
                  if r.get("sol_context_flag") is None and r.get("eth_context_flag") is None
                  and len(r["sol_context"]) == 21 and len(r["eth_context"]) == 21]
        print(f"  strict subset: {len(subset)}")
    else:
        subset = [r for r in records
                  if r.get("sol_context_flag") is None and r.get("eth_context_flag") is None]
        print(f"  both_ok subset: {len(subset)}")

    # 收集唯一 sig / hash
    sol_sigs = set()
    eth_hashes = set()
    for r in subset:
        sol_sigs.add(r["sol_sig"])
        eth_hashes.add(r["eth_hash"])
        for s in r["sol_context"]:
            if s.get("sig"):
                sol_sigs.add(s["sig"])
        for e in r["eth_context"]:
            if e.get("hash"):
                eth_hashes.add(e["hash"])
    print(f"  unique SOL sigs: {len(sol_sigs):,}")
    print(f"  unique ETH hashes: {len(eth_hashes):,}")

    # 读缓存
    print(f"\n读 resume cache ...")
    sol_done = load_cache_keys(SOL_OUT)
    eth_done = load_cache_keys(ETH_OUT)
    print(f"  sol cached: {len(sol_done):,}")
    print(f"  eth cached: {len(eth_done):,}")

    sol_todo = list(sol_sigs - sol_done)
    eth_todo = list(eth_hashes - eth_done)
    print(f"  sol todo: {len(sol_todo):,}")
    print(f"  eth todo: {len(eth_todo):,}")

    errors = {}
    if ERR_OUT.exists():
        errors = json.load(open(ERR_OUT, encoding="utf-8"))

    # 两侧并行写,各自用 lock 控制 append
    sol_lock = threading.Lock()
    eth_lock = threading.Lock()
    sol_f = open(SOL_OUT, "a", encoding="utf-8")
    eth_f = open(ETH_OUT, "a", encoding="utf-8")

    counts = {"sol_done": 0, "eth_done": 0, "sol_err": 0, "eth_err": 0}
    t1 = time.time()
    stop_flag = threading.Event()

    def sol_worker(sig):
        res = parse_sol_tx(sig)
        with sol_lock:
            sol_f.write(json.dumps(res, ensure_ascii=False) + "\n")
            sol_f.flush()
            counts["sol_done"] += 1
            if res.get("error"):
                counts["sol_err"] += 1
                errors[f"sol:{sig}"] = res["error"]

    def eth_worker(h):
        res = parse_eth_tx(h)
        with eth_lock:
            eth_f.write(json.dumps(res, ensure_ascii=False) + "\n")
            eth_f.flush()
            counts["eth_done"] += 1
            if res.get("error"):
                counts["eth_err"] += 1
                errors[f"eth:{h}"] = res["error"]

    def reporter():
        last_sol, last_eth, last_t = counts["sol_done"], counts["eth_done"], time.time()
        while not stop_flag.is_set():
            time.sleep(30)
            now = time.time()
            ds = counts["sol_done"] - last_sol
            de = counts["eth_done"] - last_eth
            dt = now - last_t
            total_todo = len(sol_todo) + len(eth_todo)
            total_done = counts["sol_done"] + counts["eth_done"]
            rate = (ds + de) / dt if dt else 0
            eta = (total_todo - total_done) / rate if rate else 0
            print(f"  [{time.strftime('%H:%M:%S')}] sol={counts['sol_done']:,}/{len(sol_todo):,} "
                  f"eth={counts['eth_done']:,}/{len(eth_todo):,} "
                  f"err=s{counts['sol_err']}/e{counts['eth_err']} "
                  f"rate={rate:.0f}/s ETA={eta/60:.0f}min",
                  flush=True)
            # 定期保存 errors
            with open(ERR_OUT, "w", encoding="utf-8") as f:
                json.dump(errors, f, ensure_ascii=False)
            last_sol, last_eth, last_t = counts["sol_done"], counts["eth_done"], now

    print(f"\n开始解析 (SOL_W={SOL_WORKERS}, ETH_W={ETH_WORKERS}, RPS={GLOBAL_RPS}) ...")
    reporter_th = threading.Thread(target=reporter, daemon=True)
    reporter_th.start()

    try:
        # 两个独立 pool 并行跑,避免 FIFO 排队饿死 ETH
        sol_pool = ThreadPoolExecutor(max_workers=SOL_WORKERS, thread_name_prefix="sol")
        eth_pool = ThreadPoolExecutor(max_workers=ETH_WORKERS, thread_name_prefix="eth")
        sol_futs = [sol_pool.submit(sol_worker, sig) for sig in sol_todo]
        eth_futs = [eth_pool.submit(eth_worker, h) for h in eth_todo]
        sol_pool.shutdown(wait=False)
        eth_pool.shutdown(wait=False)
        all_futs = sol_futs + eth_futs
        for f in as_completed(all_futs):
            pass
    finally:
        stop_flag.set()
        sol_f.close()
        eth_f.close()
        with open(ERR_OUT, "w", encoding="utf-8") as f:
            json.dump(errors, f, ensure_ascii=False)

    elapsed = time.time() - t1
    print(f"\n完成: sol={counts['sol_done']} (err={counts['sol_err']}) "
          f"eth={counts['eth_done']} (err={counts['eth_err']}) "
          f"耗时: {elapsed/60:.1f}min")
    print(f"  {SOL_OUT}")
    print(f"  {ETH_OUT}")
    print(f"  {ERR_OUT}")


if __name__ == "__main__":
    main()
