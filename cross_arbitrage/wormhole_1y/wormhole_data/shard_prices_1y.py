#!/usr/bin/env python3
"""
把 series_eth_1m.json (804MB) 按 token 分片到 prices/eth_1m_shards/ 目录,
每个 token 一个小文件, 便于 lazy loading, 避免整块 JSON 加载导致内存爆炸。

分片命名: 用 token_key 的 sha1 前 16 位 hex 作为文件名 (避免长文件名)。
索引文件: eth_1m_shards/_index.json  {token_key: shard_filename}

exact_sol.json 只有 3.6MB, 不分片 (整块加载成本可接受)。
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent / "use" / "portal_full" / "recent_1y" / "matched" / "arbitrage"
PRICES = ROOT / "prices"
ETH_1M = PRICES / "series_eth_1m.json"
SHARD_DIR = PRICES / "eth_1m_shards"
INDEX_FILE = SHARD_DIR / "_index.json"


def shard_name(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".json"


def main():
    if not ETH_1M.exists():
        print(f"missing: {ETH_1M}")
        return
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"loading {ETH_1M.name} ({ETH_1M.stat().st_size/1e6:.0f} MB) ...", flush=True)
    with open(ETH_1M, encoding="utf-8") as f:
        raw = json.load(f)
    print(f"  {len(raw)} tokens loaded", flush=True)

    index = {}
    n_written = 0
    for k, series in raw.items():
        fn = shard_name(k)
        out = SHARD_DIR / fn
        with open(out, "w", encoding="utf-8") as f:
            json.dump(series, f, ensure_ascii=False)
        index[k] = fn
        n_written += 1
        if n_written % 20 == 0:
            print(f"  {n_written}/{len(raw)} shards written", flush=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"wrote {n_written} shards to {SHARD_DIR}")
    print(f"index at {INDEX_FILE}")


if __name__ == "__main__":
    main()
