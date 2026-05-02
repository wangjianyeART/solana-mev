#!/usr/bin/env python3
"""Step 3 — Apply two cleaning rules to the strong tier.

R1 (self-sandwich): drop records where the attacker wallet appears as a
    victim signer or aligned wallet in the same bundle. These are usually
    arbitrage routes that look like sandwiches but have no external victim.

R2 (pool / market-maker contamination): drop records whose attacker wallet
    behaves like an AMM pool vault. The heuristic uses ALL recall tiers
    (strong + probable + possible + weak) to compute per wallet:
      n_bundles         : how many bundles wallet was flagged as attacker
      direction_balance : min(buy_n, sell_n) / max(buy_n, sell_n)
      mean_profit_sol   : mean profit_sol across its records
    A wallet is flagged as a pool/MM if:
      n_bundles >= 200 AND direction_balance >= 0.30 AND |mean_profit_sol| <= 0.01

Input:  data/sandwiches_recall_wide.jsonl   (output of step 2)
Output: data/sandwiches_strong_clean.jsonl  (academically defensible set)
        data/strong_pool_blacklist.txt       (suspect pool wallets)
"""
import gzip
import json
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "sandwiches_recall_wide.jsonl"
OUT = ROOT / "data" / "sandwiches_strong_clean.jsonl"
POOL_LIST = ROOT / "data" / "strong_pool_blacklist.txt"


def open_text(path):
    if not path.exists() and path.suffix != ".gz":
        gz_path = Path(str(path) + ".gz")
        if gz_path.exists():
            path = gz_path
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else open(path, encoding="utf-8")


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    # pass 1: per-wallet aggregate stats across ALL tiers
    agg = defaultdict(lambda: {
        "n": 0, "buy": 0, "sell": 0, "victims": set(),
        "profits": [], "tiers": Counter(),
    })
    with open_text(SRC) as f:
        for l in f:
            r = json.loads(l)
            a = r["attacker"]
            s = agg[a]
            s["n"] += 1
            s["tiers"][r.get("confidence", "?")] += 1
            s[r["direction"]] += 1
            s["profits"].append(r.get("profit_sol", 0))
            for v in r.get("victims") or []:
                sig = v.get("signer")
                if sig:
                    s["victims"].add(sig)

    # pass 2: identify pool/MM blacklist
    def pool_score(stats):
        n = stats["n"]
        if n < 200:
            return None
        buy, sell = stats["buy"], stats["sell"]
        if max(buy, sell) == 0:
            return None
        db = min(buy, sell) / max(buy, sell)
        if db < 0.30:
            return None
        profits = stats["profits"]
        mean_p = sum(profits) / len(profits)
        if abs(mean_p) > 0.01:
            return None
        return (n, db, mean_p, len(stats["victims"]))

    pool_blacklist = {}
    for w, st in agg.items():
        score = pool_score(st)
        if score is not None:
            pool_blacklist[w] = score

    with open(POOL_LIST, "w") as f:
        f.write(f"# pool/MM blacklist - {len(pool_blacklist)} wallets\n")
        f.write("# columns: wallet  n_bundles  dir_balance  mean_profit_sol  n_victims  tier_counts\n")
        for w, (n, db, mp, nv) in sorted(pool_blacklist.items(), key=lambda x: -x[1][0]):
            tiers = dict(agg[w]["tiers"])
            f.write(f"{w}\t{n}\t{db:.3f}\t{mp:+.5f}\t{nv}\t{tiers}\n")

    print(f"pool/MM blacklist: {len(pool_blacklist)} wallets -> {POOL_LIST}")
    print("top 10 by bundle count:")
    for w, (n, db, mp, nv) in sorted(pool_blacklist.items(), key=lambda x: -x[1][0])[:10]:
        print(f"  {w[:14]}...  n={n:>5}  dir_bal={db:.2f}  mean_p={mp:+.5f}  victims={nv}")

    # pass 3: filter strong records
    kept = []
    dropped_self = 0
    dropped_pool = 0
    with open_text(SRC) as f:
        for l in f:
            r = json.loads(l)
            if r.get("confidence") != "strong":
                continue
            a = r["attacker"]
            v_signers = set()
            for v in r.get("victims") or []:
                if v.get("signer"):
                    v_signers.add(v["signer"])
                if v.get("aligned_wallet"):
                    v_signers.add(v["aligned_wallet"])
            if a in v_signers:
                dropped_self += 1
                continue
            if a in pool_blacklist:
                dropped_pool += 1
                continue
            kept.append(r)

    with open(OUT, "w") as f:
        for r in kept:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")

    print("\n--- filtering strong tier ---")
    total = dropped_self + dropped_pool + len(kept)
    print(f"  total strong:          {total:,}")
    print(f"  dropped self-sandwich: {dropped_self:,}")
    print(f"  dropped pool/MM:       {dropped_pool:,}")
    print(f"  KEPT:                  {len(kept):,}  ({len(kept)/total*100:.1f}%)")

    def stats(xs, label):
        xs = sorted(xs)
        if not xs:
            print(f"  {label}: empty")
            return
        pct = lambda p: xs[max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))]
        print(f"  {label:30s}  n={len(xs):,}  sum={sum(xs):+9.3f}  med={pct(50):+.5f}  "
              f"p25={pct(25):+.5f}  p75={pct(75):+.5f}")

    print("\n--- profit_sol BEFORE / AFTER cleaning ---")
    all_strong_profits = []
    with open_text(SRC) as f:
        for l in f:
            r = json.loads(l)
            if r.get("confidence") == "strong":
                all_strong_profits.append(r.get("profit_sol", 0))
    stats(all_strong_profits, "ALL strong (before)")
    stats([r.get("profit_sol", 0) for r in kept], "KEPT strong (after)")

    before_pos = sum(1 for p in all_strong_profits if p > 0)
    after_pos = sum(1 for r in kept if r.get("profit_sol", 0) > 0)
    print(f"\n  profitable BEFORE: {before_pos:>6,}/{len(all_strong_profits):,} "
          f"({before_pos/len(all_strong_profits)*100:.1f}%)")
    print(f"  profitable AFTER:  {after_pos:>6,}/{len(kept):,} "
          f"({after_pos/len(kept)*100:.1f}%)")

    print(f"\noutput: {OUT}")


if __name__ == "__main__":
    main()
