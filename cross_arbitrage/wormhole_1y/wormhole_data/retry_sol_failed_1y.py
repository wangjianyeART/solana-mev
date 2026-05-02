#!/usr/bin/env python3
"""
快速补拉 Stage 6 中 RPC 失败 (retries) 的 SOL sig。

策略:
  - 只重试 error == "retries" 的 sig (tx_failed 是链上事实,跳过)
  - 重试用更长 timeout + 更多 retries
  - 成功则 append 到 sol_parsed.jsonl (downstream 用 last-wins 语义)
  - 从 parse_errors.json 移除已成功的 sig

输入:
  context_parse_cache/parse_errors.json
输出 (就地更新):
  context_parse_cache/sol_parsed.jsonl   (append 成功结果)
  context_parse_cache/parse_errors.json  (移除已恢复的 sig)
"""

import json
import threading
import time
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_1y" / "matched"
CACHE_DIR = ROOT / "context_parse_cache"
SOL_OUT = CACHE_DIR / "sol_parsed.jsonl"
ERR_OUT = CACHE_DIR / "parse_errors.json"

SOL_RPC = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"

WORKERS = 60
GLOBAL_RPS = 200
RETRIES = 6
TIMEOUT = 60

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


def _rpc(method, params):
    for attempt in range(RETRIES):
        limiter.wait()
        try:
            resp = requests.post(SOL_RPC, json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            }, timeout=TIMEOUT)
            if resp.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            data = resp.json()
            if "error" in data:
                code = (data.get("error") or {}).get("code", 0)
                if code == 429 or code == -32005:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                return None, data["error"].get("message", "err")[:60]
            return data.get("result"), None
        except Exception as e:
            if attempt == RETRIES - 1:
                return None, str(e)[:60]
            time.sleep(1.0 * (attempt + 1))
    return None, "retries"


def parse_sol_tx(sig):
    result, err = _rpc("getTransaction", [sig, {
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
        accounts.append(ak.get("pubkey", "") if isinstance(ak, dict) else ak)

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
                "mint": mint, "symbol": MINT_SYMBOLS.get(mint, mint),
                "owner": owner, "change": change,
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
        "sig": sig, "ts": block_time, "slot": slot,
        "fee_sol": meta.get("fee", 0) / 1e9,
        "signer": signer, "programs": programs,
        "token_changes": token_changes, "sol_changes": sol_changes[:5],
        "summary": {"sold": sold, "bought": bought},
    }


def main():
    print("加载 parse_errors.json ...")
    errs = json.load(open(ERR_OUT, encoding="utf-8"))
    sol_retry_sigs = [k[4:] for k, v in errs.items() if k.startswith("sol:") and v == "retries"]
    print(f"  total errors: {len(errs):,}")
    print(f"  sol retries 待补: {len(sol_retry_sigs):,}")

    sol_f = open(SOL_OUT, "a", encoding="utf-8")
    write_lock = threading.Lock()
    err_lock = threading.Lock()
    counts = {"ok": 0, "tx_failed": 0, "still_err": 0}
    new_errors = {}
    fixed_keys = []

    def worker(sig):
        res = parse_sol_tx(sig)
        with write_lock:
            sol_f.write(json.dumps(res, ensure_ascii=False) + "\n")
            sol_f.flush()
        with err_lock:
            if res.get("error") == "tx_failed":
                counts["tx_failed"] += 1
                fixed_keys.append(f"sol:{sig}")
                # tx_failed 也算修复了 (不再是 retries 错误)
            elif res.get("error"):
                counts["still_err"] += 1
                new_errors[f"sol:{sig}"] = res["error"]
            else:
                counts["ok"] += 1
                fixed_keys.append(f"sol:{sig}")

    print(f"\n开始重试 (workers={WORKERS}, RPS={GLOBAL_RPS}, retries={RETRIES}, timeout={TIMEOUT}s) ...")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(worker, sig) for sig in sol_retry_sigs]
        last_print = time.time()
        for i, _ in enumerate(as_completed(futs)):
            if time.time() - last_print > 30:
                done = counts["ok"] + counts["tx_failed"] + counts["still_err"]
                rate = done / (time.time() - t0)
                eta = (len(sol_retry_sigs) - done) / rate if rate else 0
                print(f"  {done}/{len(sol_retry_sigs)}  ok={counts['ok']} tx_failed={counts['tx_failed']} "
                      f"err={counts['still_err']}  {rate:.0f}/s  ETA {eta:.0f}s")
                last_print = time.time()
    sol_f.close()

    # 更新 errors.json: 移除 fixed_keys, 加入 new_errors
    print(f"\n更新 parse_errors.json ...")
    new_err_dict = dict(errs)
    for k in fixed_keys:
        new_err_dict.pop(k, None)
    for k, v in new_errors.items():
        new_err_dict[k] = v
    with open(ERR_OUT, "w", encoding="utf-8") as f:
        json.dump(new_err_dict, f, ensure_ascii=False)

    elapsed = time.time() - t0
    recovered = counts["ok"] + counts["tx_failed"]
    print(f"\n完成: 处理 {len(sol_retry_sigs):,} sigs / {elapsed:.0f}s")
    print(f"  解析成功: {counts['ok']:,}")
    print(f"  实为 tx_failed: {counts['tx_failed']:,}  (链上失败,正常)")
    print(f"  仍失败: {counts['still_err']:,}")
    print(f"  覆盖率: {recovered / len(sol_retry_sigs) * 100:.1f}%")
    print(f"  errors.json: {len(errs):,} → {len(new_err_dict):,}")


if __name__ == "__main__":
    main()
