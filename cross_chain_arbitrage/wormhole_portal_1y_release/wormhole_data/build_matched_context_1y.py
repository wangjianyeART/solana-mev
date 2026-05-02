#!/usr/bin/env python3
"""
Stage 5: 为 1y matched.json 的每条跨链记录构建 ±W 窗口上下文。

输入:
  use/portal_full/recent_1y/matched/matched.json                 (113K recs)
  use/portal_full/recent_1y/matched/address_txs/sol_sigs.json    (1.05GB)
  use/portal_full/recent_1y/matched/address_txs/eth_txs.json     (512MB)
  use/portal_full/recent_1y/matched/address_txs/errors.json      (53 errs)

输出:
  use/portal_full/recent_1y/matched/matched_context.json

结构 (mirror 30d):
  records: [
    {
      ...matched record fields...,
      sol_context: [{sig, ts, slot, err}, ...],   # 降序,长度 ≤ 2W+1
      sol_context_pos: int,                       # bridge sig 在数组中的位置
      sol_context_flag: null | "synthetic" | "addr_error" | "not_found",
      eth_context: [{hash, ts, block, from, to}, ...],
      eth_context_pos: int,
      eth_context_flag: null | "synthetic" | "addr_error" | "not_found",
    }
  ]

用法: python wormhole_data/build_matched_context_1y.py
"""

import json
import os
import time
from collections import Counter
from pathlib import Path

RECENT = Path(__file__).parent / "use" / "portal_full" / "recent_1y"
RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
ROOT = RECENT / (f"matched_{RUN_TAG}" if RUN_TAG else "matched")
BASE_ROOT = RECENT / "matched"
MATCHED = ROOT / "matched.json"
SOL_SIGS = BASE_ROOT / "address_txs" / "sol_sigs.json"
ETH_TXS = BASE_ROOT / "address_txs" / "eth_txs.json"
ERRORS = BASE_ROOT / "address_txs" / "errors.json"
OUT = ROOT / "matched_context.json"

WINDOW = 10  # ±10 条


_idx_cache = {}  # id(lst) -> {key_value: idx}


def find_idx(lst, key_field, key_value):
    """返回 key_value 在 lst 中的索引,找不到返回 -1。对单个 list 缓存倒排。"""
    cache_key = (id(lst), key_field)
    d = _idx_cache.get(cache_key)
    if d is None:
        d = {item.get(key_field): i for i, item in enumerate(lst)}
        _idx_cache[cache_key] = d
    return d.get(key_value, -1)


def insert_synthetic_by_ts(lst, synthetic):
    """lst 按 ts 降序 — 找到第一个 ts < synthetic.ts 的位置插入。返回 (new_list, insert_idx)。"""
    ts = synthetic["ts"]
    idx = len(lst)
    for i, item in enumerate(lst):
        if item.get("ts", 0) < ts:
            idx = i
            break
    new_list = lst[:idx] + [synthetic] + lst[idx:]
    return new_list, idx


def slice_window(lst, idx, w):
    """取 ±w 窗口。返回 (slice, pos_in_slice)。"""
    start = max(0, idx - w)
    end = min(len(lst), idx + w + 1)
    return lst[start:end], idx - start


def sol_trim(item):
    return {
        "sig": item.get("sig"),
        "ts": item.get("ts"),
        "slot": item.get("slot"),
        "err": item.get("err"),
    }


def eth_trim(item):
    return {
        "hash": item.get("hash"),
        "ts": item.get("ts"),
        "block": item.get("block"),
        "from": item.get("from"),
        "to": item.get("to"),
        "kind": item.get("kind"),
    }


def main():
    t0 = time.time()
    print("=" * 60)

    print("[1/4] 加载 matched.json ...")
    m = json.load(open(MATCHED, encoding="utf-8"))
    records = m["records"]
    print(f"  records: {len(records)}")

    print("[2/4] 加载 sol_sigs.json (1GB, 约需 30s) ...")
    sol_sigs = json.load(open(SOL_SIGS, encoding="utf-8"))
    print(f"  sol addrs: {len(sol_sigs)}")

    print("[3/4] 加载 eth_txs.json (512MB, 约需 15s) ...")
    eth_txs = json.load(open(ETH_TXS, encoding="utf-8"))
    print(f"  eth addrs: {len(eth_txs)}")

    errors = {}
    if ERRORS.exists():
        errors = json.load(open(ERRORS, encoding="utf-8"))
    sol_err_addrs = {k for k in errors if not k.startswith("eth:")}
    eth_err_addrs = {k[4:].lower() for k in errors if k.startswith("eth:")}
    print(f"  sol errored: {len(sol_err_addrs)} | eth errored: {len(eth_err_addrs)}")

    print(f"\n[4/4] 构建 context (window=±{WINDOW}) ...")
    flag_cnt = Counter()
    out_records = []
    t1 = time.time()

    for i, r in enumerate(records):
        sol_sender = r.get("sol_sender")
        eth_from = (r.get("eth_from") or "").lower()
        sol_sig = r.get("sol_sig")
        eth_hash = r.get("eth_hash")
        sol_ts = r.get("sol_ts") or 0
        eth_ts = r.get("eth_ts") or 0

        # --- SOL side ---
        sol_ctx, sol_pos, sol_flag = [], -1, None
        if not sol_sender:
            sol_flag = "no_addr"
        elif sol_sender in sol_err_addrs:
            sol_flag = "addr_error"
        else:
            lst = sol_sigs.get(sol_sender)
            if lst is None:
                sol_flag = "addr_missing"
            else:
                idx = find_idx(lst, "sig", sol_sig)
                if idx < 0:
                    synthetic = {"sig": sol_sig, "ts": sol_ts, "slot": None, "err": None}
                    lst2, idx = insert_synthetic_by_ts(lst, synthetic)
                    sl, pos = slice_window(lst2, idx, WINDOW)
                    sol_flag = "synthetic"
                else:
                    sl, pos = slice_window(lst, idx, WINDOW)
                sol_ctx = [sol_trim(x) for x in sl]
                sol_pos = pos

        # --- ETH side ---
        eth_ctx, eth_pos, eth_flag = [], -1, None
        if not eth_from:
            eth_flag = "no_addr"
        elif eth_from in eth_err_addrs:
            eth_flag = "addr_error"
        else:
            lst = eth_txs.get(eth_from)
            if lst is None:
                eth_flag = "addr_missing"
            else:
                idx = find_idx(lst, "hash", eth_hash)
                if idx < 0:
                    synthetic = {
                        "hash": eth_hash, "ts": eth_ts, "block": None,
                        "from": eth_from, "to": None, "kind": "bridge",
                    }
                    lst2, idx = insert_synthetic_by_ts(lst, synthetic)
                    sl, pos = slice_window(lst2, idx, WINDOW)
                    eth_flag = "synthetic"
                else:
                    sl, pos = slice_window(lst, idx, WINDOW)
                eth_ctx = [eth_trim(x) for x in sl]
                eth_pos = pos

        flag_cnt[f"sol_{sol_flag or 'ok'}"] += 1
        flag_cnt[f"eth_{eth_flag or 'ok'}"] += 1

        new_rec = dict(r)
        new_rec["sol_context"] = sol_ctx
        new_rec["sol_context_pos"] = sol_pos
        new_rec["sol_context_flag"] = sol_flag
        new_rec["eth_context"] = eth_ctx
        new_rec["eth_context_pos"] = eth_pos
        new_rec["eth_context_flag"] = eth_flag
        out_records.append(new_rec)

        if (i + 1) % 20000 == 0:
            rate = (i + 1) / (time.time() - t1)
            eta = (len(records) - i - 1) / rate if rate else 0
            print(f"  {i+1}/{len(records)}  {rate:.0f}/s  ETA {eta:.0f}s")

    print(f"\n  context build done in {time.time()-t1:.0f}s")
    print(f"  flags: {dict(flag_cnt)}")

    # 保存
    print("\n保存 matched_context.json ...")
    meta = dict(m.get("meta", {}))
    meta["context_window"] = WINDOW
    meta["context_flag_counts"] = dict(flag_cnt)
    out = {"meta": meta, "records": out_records}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"  写入: {OUT} ({size_mb:.1f} MB)")
    print(f"  总耗时: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
