#!/usr/bin/env python3
"""
取 matched.json 里所有 SOL/ETH 地址,抓过去 1y 的轻量 sig/hash 列表。

SOL 地址: Chainstack RPC `getSignaturesForAddress` (RPS 100 parallel, 分页 serial per addr)
ETH 地址: Etherscan txlist (API key 已有)

只存简洁信息,每条 tx 仅 {sig/hash, slot/block, ts, err/status, from?, to?, value?}。

输出:
  recent_1y/matched/addresses.json        — 去重地址清单
  recent_1y/matched/address_txs/sol_sigs.json
  recent_1y/matched/address_txs/eth_txs.json
  recent_1y/matched/address_txs/errors.json

支持 resume。

用法: python wormhole_data/fetch_address_txs_1y.py
"""

import json
import time
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_1y" / "matched"
MATCHED = ROOT / "matched.json"
ADDR_OUT = ROOT / "addresses.json"
OUT_DIR = ROOT / "address_txs"
SOL_OUT = OUT_DIR / "sol_sigs.json"
ETH_OUT = OUT_DIR / "eth_txs.json"
ERR_OUT = OUT_DIR / "errors.json"

OUT_DIR.mkdir(exist_ok=True)

SOL_RPC = "https://solana-mainnet.core.chainstack.com/a5ad9b6d3d7980c24ba45cec09d85d2a"
ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
ETHERSCAN_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"

# 1y 窗口: 与 recent_1y ETH 过滤一致
SINCE_TS = int(datetime(2025, 4, 12, tzinfo=timezone.utc).timestamp())

SOL_WORKERS = 15         # 并行地址数 (per-call 最多 100/s 总量)
SOL_MAX_RPS = 100        # Chainstack SOL RPS 上限 (按 100 控)
SOL_SIGS_CAP = 300_000   # 单地址 sigs 上限 — 超 cap 就停分页 (MEV bot 可能有千万级 sigs)
ETH_WORKERS = 8          # Etherscan 并行
ETH_DELAY = 0.08         # 每请求延迟, 防 rate limit
SAVE_EVERY = 20          # 更勤保存 (killed 丢失 20 条, 改 20)

# Token-bucket 限速 (全局 SOL RPS)
class RateLimiter:
    def __init__(self, rps):
        self.rps = rps
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

sol_limiter = RateLimiter(SOL_MAX_RPS)


def fetch_sol_sigs(addr):
    """获取 SOL 地址过去 1y 的 sigs (分页直到 ts < SINCE_TS)"""
    all_sigs = []
    before = None
    calls = 0
    while True:
        params = {"limit": 1000}
        if before:
            params["before"] = before
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [addr, params],
        }
        sol_limiter.wait()
        for attempt in range(3):
            try:
                resp = requests.post(SOL_RPC, json=payload, timeout=30)
                if resp.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    return all_sigs, f"http_{resp.status_code}"
                result = resp.json().get("result", [])
                break
            except Exception as e:
                if attempt == 2:
                    return all_sigs, str(e)[:60]
                time.sleep(1)
        else:
            return all_sigs, "retries"

        calls += 1
        if not result:
            break

        hit_floor = False
        for s in result:
            bt = s.get("blockTime")
            if bt is not None and bt < SINCE_TS:
                hit_floor = True
                continue
            all_sigs.append({
                "sig": s["signature"],
                "slot": s.get("slot"),
                "ts": bt,
                "err": s.get("err"),
            })
        if hit_floor or len(result) < 1000:
            break
        if len(all_sigs) >= SOL_SIGS_CAP:
            # 命中 cap, 停止分页 — sigs 仍保存
            break
        before = result[-1]["signature"]

    return all_sigs, None


def fetch_eth_txs(addr):
    """用 Etherscan txlist + tokentx 取 ETH 地址过去 1y 的 tx 列表"""
    out = []
    for action in ("txlist", "tokentx"):
        page = 1
        while True:
            params = {
                "chainid": 1,
                "module": "account",
                "action": action,
                "address": addr,
                "startblock": 0,
                "endblock": 99999999,
                "page": page,
                "offset": 10000,
                "sort": "desc",
                "apikey": ETHERSCAN_KEY,
            }
            try:
                resp = requests.get(ETHERSCAN_URL, params=params, timeout=30)
                data = resp.json()
            except Exception as e:
                return out, str(e)[:60]

            if data.get("status") != "1":
                msg = data.get("message", "")
                if "No transactions" in msg or "result" not in data:
                    break
                if "rate limit" in msg.lower():
                    time.sleep(2)
                    continue
                break

            records = data.get("result") or []
            if not records:
                break

            stop = False
            for tx in records:
                try:
                    ts = int(tx.get("timeStamp", 0))
                except Exception:
                    ts = 0
                if ts < SINCE_TS:
                    stop = True
                    break
                item = {
                    "hash": tx.get("hash"),
                    "ts": ts,
                    "block": int(tx.get("blockNumber", 0) or 0),
                    "from": tx.get("from"),
                    "to": tx.get("to"),
                    "kind": action,
                }
                if action == "txlist":
                    item["value"] = tx.get("value")
                    item["methodId"] = tx.get("methodId")
                    item["isError"] = tx.get("isError")
                else:
                    item["contract"] = tx.get("contractAddress")
                    item["token"] = tx.get("tokenSymbol")
                    item["amount"] = tx.get("value")  # raw
                out.append(item)

            if stop or len(records) < 10000:
                break
            page += 1
            time.sleep(ETH_DELAY)
    return out, None


def main():
    t0 = time.time()
    print("=" * 60)
    print("加载 matched.json ...")
    if not MATCHED.exists():
        print(f"  ERROR: {MATCHED} 不存在")
        return
    doc = json.load(open(MATCHED, encoding="utf-8"))
    records = doc["records"]
    print(f"  matched: {len(records)}")

    # 收集地址
    sol_addrs = set()
    eth_addrs = set()
    for r in records:
        for k in ("sol_sender", "sol_dst_recipient", "src_sender", "dst_receiver"):
            v = r.get(k)
            if not v:
                continue
            vs = str(v)
            if vs.startswith("0x") and len(vs) == 42:
                eth_addrs.add(vs.lower())
            elif 32 <= len(vs) <= 45 and not vs.startswith("0x"):
                sol_addrs.add(vs)
        for k in ("eth_from", "eth_dst_recipient"):
            v = r.get(k)
            if v and str(v).startswith("0x") and len(str(v)) == 42:
                eth_addrs.add(str(v).lower())

    sol_addrs = sorted(sol_addrs)
    eth_addrs = sorted(eth_addrs)
    print(f"  unique SOL addrs: {len(sol_addrs)}")
    print(f"  unique ETH addrs: {len(eth_addrs)}")

    with open(ADDR_OUT, "w", encoding="utf-8") as f:
        json.dump({"sol_addrs": sol_addrs, "eth_addrs": eth_addrs,
                   "since_ts": SINCE_TS,
                   "since_iso": datetime.fromtimestamp(SINCE_TS, tz=timezone.utc).isoformat()},
                  f, ensure_ascii=False, indent=2)

    errors = {}
    if ERR_OUT.exists():
        errors = json.load(open(ERR_OUT, encoding="utf-8"))

    # === SOL ===
    sol_results = {}
    if SOL_OUT.exists():
        sol_results = json.load(open(SOL_OUT, encoding="utf-8"))
        print(f"\n[SOL] 已缓存 {len(sol_results)} 地址")
    sol_todo = [a for a in sol_addrs if a not in sol_results and f"sol:{a}" not in errors]
    print(f"[SOL] 待抓: {len(sol_todo)}")

    if sol_todo:
        t1 = time.time()
        done = ok = err = 0
        total_sigs = 0
        lock = threading.Lock()
        def _do_sol(a):
            sigs, e = fetch_sol_sigs(a)
            return a, sigs, e
        with ThreadPoolExecutor(max_workers=SOL_WORKERS) as pool:
            futs = [pool.submit(_do_sol, a) for a in sol_todo]
            for f in as_completed(futs):
                a, sigs, e = f.result()
                done += 1
                if e is None:
                    sol_results[a] = sigs
                    total_sigs += len(sigs)
                    ok += 1
                else:
                    errors[f"sol:{a}"] = e
                    err += 1
                if done % 20 == 0:
                    rate = done / (time.time() - t1)
                    eta = (len(sol_todo) - done) / rate if rate else 0
                    print(f"  [SOL] {done}/{len(sol_todo)} ok={ok} err={err} sigs={total_sigs} "
                          f"({rate:.1f}/s ETA {eta:.0f}s)")
                if done % SAVE_EVERY == 0:
                    with lock:
                        with open(SOL_OUT, "w", encoding="utf-8") as fp:
                            json.dump(sol_results, fp, ensure_ascii=False)
                        with open(ERR_OUT, "w", encoding="utf-8") as fp:
                            json.dump(errors, fp, ensure_ascii=False)
        with open(SOL_OUT, "w", encoding="utf-8") as f:
            json.dump(sol_results, f, ensure_ascii=False)
        print(f"  [SOL] 完成 ok={ok} err={err} total_sigs={total_sigs} ({time.time()-t1:.0f}s)")

    # === ETH ===
    eth_results = {}
    if ETH_OUT.exists():
        eth_results = json.load(open(ETH_OUT, encoding="utf-8"))
        print(f"\n[ETH] 已缓存 {len(eth_results)} 地址")
    eth_todo = [a for a in eth_addrs if a not in eth_results and f"eth:{a}" not in errors]
    print(f"[ETH] 待抓: {len(eth_todo)}")

    if eth_todo:
        t1 = time.time()
        done = ok = err = 0
        total_txs = 0
        lock = threading.Lock()
        def _do_eth(a):
            txs, e = fetch_eth_txs(a)
            return a, txs, e
        with ThreadPoolExecutor(max_workers=ETH_WORKERS) as pool:
            futs = [pool.submit(_do_eth, a) for a in eth_todo]
            for f in as_completed(futs):
                a, txs, e = f.result()
                done += 1
                if e is None:
                    eth_results[a] = txs
                    total_txs += len(txs)
                    ok += 1
                else:
                    errors[f"eth:{a}"] = e
                    err += 1
                if done % 20 == 0:
                    rate = done / (time.time() - t1)
                    eta = (len(eth_todo) - done) / rate if rate else 0
                    print(f"  [ETH] {done}/{len(eth_todo)} ok={ok} err={err} txs={total_txs} "
                          f"({rate:.1f}/s ETA {eta:.0f}s)")
                if done % SAVE_EVERY == 0:
                    with lock:
                        with open(ETH_OUT, "w", encoding="utf-8") as fp:
                            json.dump(eth_results, fp, ensure_ascii=False)
                        with open(ERR_OUT, "w", encoding="utf-8") as fp:
                            json.dump(errors, fp, ensure_ascii=False)
        with open(ETH_OUT, "w", encoding="utf-8") as f:
            json.dump(eth_results, f, ensure_ascii=False)
        print(f"  [ETH] 完成 ok={ok} err={err} total_txs={total_txs} ({time.time()-t1:.0f}s)")

    with open(ERR_OUT, "w", encoding="utf-8") as f:
        json.dump(errors, f, ensure_ascii=False)

    print(f"\n输出:")
    print(f"  {ADDR_OUT}")
    print(f"  {SOL_OUT}    ({sum(len(v) for v in sol_results.values())} sigs)")
    print(f"  {ETH_OUT}    ({sum(len(v) for v in eth_results.values())} txs)")
    print(f"  errors: {len(errors)}")
    print(f"总耗时: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
