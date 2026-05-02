#!/usr/bin/env python3
"""
补抓 Etherscan 10K cap 丢失的 1y ETH 交易。

策略:
  - cap-hit 地址 (任一 action count >= 9990): 用 startblock/endblock cursor 向前翻页
  - 空列表地址 (len=0 且被 matched 引用): 加上 txlistinternal 再试

输入:
  use/portal_full/recent_1y/matched/matched.json
  use/portal_full/recent_1y/matched/address_txs/eth_txs.json

输出:
  use/portal_full/recent_1y/matched/address_txs/eth_txs.json       (就地更新,含备份)
  use/portal_full/recent_1y/matched/address_txs/eth_txs_backup.json
  use/portal_full/recent_1y/matched/address_txs/backfill_errors.json

用法: python wormhole_data/backfill_eth_txs_1y.py
"""

import json
import shutil
import time
import threading
import requests
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_1y" / "matched"
MATCHED = ROOT / "matched.json"
ETH_OUT = ROOT / "address_txs" / "eth_txs.json"
ETH_BAK = ROOT / "address_txs" / "eth_txs_backup.json"
ERR_OUT = ROOT / "address_txs" / "backfill_errors.json"

ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
ETHERSCAN_KEY = "NRMUEST1XCCGI75BYCGU35XNY2Z954U3Y7"
SINCE_TS = int(datetime(2025, 4, 12, tzinfo=timezone.utc).timestamp())

WORKERS = 8
DELAY = 0.1          # per-request delay (全局 5 RPS 上限,保守)
SAVE_EVERY = 50
CAP_THRESHOLD = 9990  # 超过就视为 cap-hit


def normalize(tx, action):
    try:
        ts = int(tx.get("timeStamp", 0))
    except Exception:
        ts = 0
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
    elif action == "tokentx":
        item["contract"] = tx.get("contractAddress")
        item["token"] = tx.get("tokenSymbol")
        item["amount"] = tx.get("value")
    elif action == "txlistinternal":
        item["value"] = tx.get("value")
        item["isError"] = tx.get("isError")
    return item


def fetch_chunked(addr, action, floor_ts, max_loops=30):
    """用 endblock cursor 分段拉,直到返回 <10K 或打到 floor_ts。"""
    all_items = []
    endblock = 99999999
    loops = 0
    while loops < max_loops:
        loops += 1
        params = {
            "chainid": 1, "module": "account", "action": action,
            "address": addr, "startblock": 0, "endblock": endblock,
            "page": 1, "offset": 10000, "sort": "desc",
            "apikey": ETHERSCAN_KEY,
        }
        for attempt in range(3):
            try:
                resp = requests.get(ETHERSCAN_URL, params=params, timeout=30)
                data = resp.json()
                break
            except Exception as e:
                if attempt == 2:
                    return all_items, f"{action}:{str(e)[:60]}"
                time.sleep(1)

        status = data.get("status")
        msg = data.get("message", "")
        if status != "1":
            if "No transactions" in msg or "result" not in data:
                break
            if "rate limit" in msg.lower() or "max calls" in msg.lower():
                time.sleep(3)
                continue
            break

        records = data.get("result") or []
        if not records:
            break

        hit_floor = False
        last_block = endblock
        for tx in records:
            ts = int(tx.get("timeStamp", 0))
            if ts < floor_ts:
                hit_floor = True
                continue
            all_items.append(normalize(tx, action))
            last_block = int(tx.get("blockNumber", 0))

        if hit_floor or len(records) < 10000:
            break
        # 光标前进: 下次 endblock = 本轮最后一条的 block - 1
        if last_block <= 0:
            break
        endblock = last_block - 1
        time.sleep(DELAY)

    return all_items, None


def merge_into_list(existing, new_items):
    """按 (hash, kind) 去重合并,保持时间降序。"""
    seen = {(t.get("hash"), t.get("kind")) for t in existing}
    added = 0
    for it in new_items:
        k = (it.get("hash"), it.get("kind"))
        if k in seen:
            continue
        seen.add(k)
        existing.append(it)
        added += 1
    existing.sort(key=lambda t: -(t.get("ts") or 0))
    return added


def main():
    t0 = time.time()
    print("=" * 60)
    print("加载 matched.json + eth_txs.json ...")
    m = json.load(open(MATCHED, encoding="utf-8"))
    eth_txs = json.load(open(ETH_OUT, encoding="utf-8"))
    print(f"  records: {len(m['records'])}")
    print(f"  eth addrs: {len(eth_txs)}")

    # matched 引用的 eth 地址
    refs = set()
    for r in m["records"]:
        ef = (r.get("eth_from") or "").lower()
        if ef: refs.add(ef)
        if r["direction"] == "SOL→ETH":
            dr = (r.get("dst_receiver") or "").lower()
            if dr: refs.add(dr)
    print(f"  matched refs: {len(refs)}")

    # 目标
    addr_kinds = {a: Counter(t.get("kind") for t in lst) for a, lst in eth_txs.items()}
    cap_hit = [a for a, kc in addr_kinds.items() if any(v >= CAP_THRESHOLD for v in kc.values())]
    empty = [a for a, lst in eth_txs.items() if len(lst) == 0]
    t_cap = [a for a in cap_hit if a in refs]
    t_empty = [a for a in empty if a in refs]
    targets = list(set(t_cap) | set(t_empty))
    print(f"  cap-hit targets: {len(t_cap)}")
    print(f"  empty targets: {len(t_empty)}")
    print(f"  总目标: {len(targets)}")

    # 备份
    if not ETH_BAK.exists():
        print(f"  备份 → {ETH_BAK.name}")
        shutil.copy2(ETH_OUT, ETH_BAK)

    errors = {}
    lock = threading.Lock()
    added_per_addr = {}

    def backfill(addr):
        existing = eth_txs.get(addr, [])
        is_empty = len(existing) == 0
        is_cap = any(v >= CAP_THRESHOLD for v in Counter(t.get("kind") for t in existing).values())

        actions = []
        if is_cap:
            # 对超 cap 的那个 action 做 cursor 翻页
            kc = Counter(t.get("kind") for t in existing)
            for act in ("txlist", "tokentx"):
                if kc.get(act, 0) >= CAP_THRESHOLD:
                    actions.append(act)
        if is_empty:
            actions = ["txlist", "tokentx", "txlistinternal"]

        total_added = 0
        total_err = None
        for act in actions:
            new_items, err = fetch_chunked(addr, act, SINCE_TS)
            if err:
                total_err = err
                continue
            if new_items:
                with lock:
                    lst = eth_txs.setdefault(addr, [])
                    added = merge_into_list(lst, new_items)
                    total_added += added
        return addr, total_added, total_err

    print(f"\n开始 backfill ({WORKERS} workers, delay={DELAY}s)...")
    t1 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = [pool.submit(backfill, a) for a in targets]
        for f in as_completed(futs):
            addr, added, err = f.result()
            done += 1
            added_per_addr[addr] = added
            if err:
                errors[addr] = err
            if done % 50 == 0:
                rate = done / (time.time() - t1)
                eta = (len(targets) - done) / rate if rate else 0
                total_added = sum(added_per_addr.values())
                print(f"  {done}/{len(targets)} added={total_added} errs={len(errors)} "
                      f"({rate:.1f}/s ETA {eta:.0f}s)")
            if done % SAVE_EVERY == 0:
                with lock:
                    with open(ETH_OUT, "w", encoding="utf-8") as f:
                        json.dump(eth_txs, f, ensure_ascii=False)

    # 最终保存
    with open(ETH_OUT, "w", encoding="utf-8") as f:
        json.dump(eth_txs, f, ensure_ascii=False)
    with open(ERR_OUT, "w", encoding="utf-8") as f:
        json.dump(errors, f, ensure_ascii=False)

    total_added = sum(added_per_addr.values())
    nonzero = sum(1 for v in added_per_addr.values() if v > 0)
    print(f"\n完成: {done} 地址 / +{total_added} txs / {nonzero} 非零 / {len(errors)} errors")
    print(f"耗时: {time.time()-t0:.0f}s")
    print(f"写入: {ETH_OUT}")
    print(f"错误: {ERR_OUT}")


if __name__ == "__main__":
    main()
