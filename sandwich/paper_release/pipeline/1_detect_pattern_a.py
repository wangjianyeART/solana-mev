#!/usr/bin/env python3
"""Step 1 — Wallet-flow-based Pattern-A sandwich detection.

For each Jito bundle of length >= 3, enumerate ordered triples (i, k, j) and
search for any wallet W such that:
  * tx[i] and tx[j] show opposite token-pair deltas for W (front + back legs)
  * some other wallet in tx[k] (i<k<j) moves the quote leg in the same
    direction as W's front leg (the victim)

Critically, we do NOT require feePayer(tx[i]) == feePayer(tx[j]).
Real attackers usually use a separate tipper wallet, so a signer-based
prefilter discards >99% of true positives.

Input:
  data/tx_cache/txs_1000_joined.jsonl   (Helius-enriched txs joined with Jito
                                         bundle metadata via the `_bundle` key)

Output:
  data/sandwiches_a.jsonl               (one record per bundle, with verdict)

NOTE: txs_1000_joined.jsonl is the raw 6 GB+ Helius cache and is NOT shipped
in this open-source bundle. This script is included for completeness and
reproducibility on a private cache. The downstream pipeline starts from
data/sandwiches_recall_wide.jsonl, which IS shipped.
"""
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "tx_cache" / "txs_1000_joined.jsonl"
OUT = ROOT / "data" / "sandwiches_a.jsonl"

SOL_MINT = "So11111111111111111111111111111111111111112"
SYM_TOL = 0.10
MIN_QUOTE = 1e-6


def tx_deltas(r):
    d = defaultdict(lambda: defaultdict(float))
    for t in (r.get("tokenTransfers") or []):
        amt = t.get("tokenAmount") or 0
        if not amt:
            continue
        mint = t.get("mint")
        if not mint:
            continue
        fu = t.get("fromUserAccount")
        tu = t.get("toUserAccount")
        if fu:
            d[fu][mint] -= amt
        if tu:
            d[tu][mint] += amt
    for t in (r.get("nativeTransfers") or []):
        lam = t.get("amount") or 0
        if not lam:
            continue
        sol = lam / 1e9
        fu = t.get("fromUserAccount")
        tu = t.get("toUserAccount")
        if fu:
            d[fu][SOL_MINT] -= sol
        if tu:
            d[tu][SOL_MINT] += sol
    out = {}
    for w, m in d.items():
        kept = {k: v for k, v in m.items() if abs(v) > 1e-9}
        if kept:
            out[w] = kept
    return out


def pick_pair_for_wallet(df, db):
    mints = set(df) | set(db)
    base_order = [SOL_MINT] if SOL_MINT in mints else []
    base_order += [m for m in mints if m != SOL_MINT]
    cands = []
    for base in base_order:
        for quote in mints:
            if quote == base:
                continue
            fb, fq = df.get(base, 0), df.get(quote, 0)
            bb, bq = db.get(base, 0), db.get(quote, 0)
            if fb < 0 and fq > 0 and bb > 0 and bq < 0:
                cands.append((base, quote, "buy", fb, fq, bb, bq))
            elif fb > 0 and fq < 0 and bb < 0 and bq > 0:
                cands.append((base, quote, "sell", fb, fq, bb, bq))
    return cands


def sym_of(c):
    fq = abs(c[4])
    bq = abs(c[6])
    return abs(fq - bq) / max(fq, bq) if max(fq, bq) > 0 else 1.0


def classify_bundle(bid, entries):
    entries = [e for e in entries if e["pos"] is not None]
    entries.sort(key=lambda e: e["pos"])
    if len(entries) < 3:
        return None
    best = None
    for i in range(len(entries)):
        for j in range(i + 2, len(entries)):
            di, dj = entries[i]["deltas"], entries[j]["deltas"]
            shared = set(di) & set(dj)
            for attacker in shared:
                cands = pick_pair_for_wallet(di[attacker], dj[attacker])
                if not cands:
                    continue
                c = min(cands, key=sym_of)
                base, quote, direction, fb, fq, bb, bq = c
                sym = sym_of(c)
                if abs(fq) < MIN_QUOTE or abs(bq) < MIN_QUOTE:
                    continue
                profit = fb + bb
                front_q_sign = 1 if fq > 0 else -1
                victims_info = []
                for k in range(i + 1, j):
                    dk = entries[k]["deltas"]
                    aligned_here = False
                    vsigner = None
                    vq = 0
                    for w, md in dk.items():
                        if w == attacker:
                            continue
                        q = md.get(quote, 0)
                        if q == 0:
                            continue
                        qsign = 1 if q > 0 else -1
                        if qsign == front_q_sign and abs(q) >= MIN_QUOTE:
                            aligned_here = True
                            vsigner = w
                            vq = q
                            break
                    victims_info.append({
                        "pos": entries[k]["pos"],
                        "sig": entries[k]["sig"],
                        "signer": entries[k].get("signer"),
                        "aligned_wallet": vsigner,
                        "victim_quote": round(vq, 9),
                        "aligned": aligned_here,
                    })
                any_aligned = any(v["aligned"] for v in victims_info)
                verdict = "sandwich_confirmed"
                if sym > SYM_TOL:
                    verdict = "rejected_dump"
                elif not any_aligned:
                    verdict = "rejected_opposite_direction"
                elif profit <= 0:
                    verdict = "rejected_no_profit"
                this = {
                    "bundle_id": bid,
                    "i": entries[i]["pos"], "j": entries[j]["pos"],
                    "attacker": attacker,
                    "front_sig": entries[i]["sig"], "back_sig": entries[j]["sig"],
                    "front_signer": entries[i].get("signer"),
                    "back_signer": entries[j].get("signer"),
                    "same_signer": entries[i].get("signer") == entries[j].get("signer"),
                    "slot": entries[i].get("slot"), "ts": entries[i].get("ts"),
                    "base_mint": base, "quote_mint": quote, "direction": direction,
                    "front_base": fb, "front_quote": fq, "back_base": bb, "back_quote": bq,
                    "sym_ratio": round(sym, 4),
                    "profit_base": round(profit, 9),
                    "victims": victims_info,
                    "verdict": verdict,
                }
                if best is None:
                    best = this
                elif this["verdict"] == "sandwich_confirmed" and best["verdict"] != "sandwich_confirmed":
                    best = this
                elif this["verdict"] == best["verdict"] and this["sym_ratio"] < best["sym_ratio"]:
                    best = this
    return best


def main():
    if not SRC.exists():
        print(f"ERROR: {SRC} not found.", file=sys.stderr)
        print("This script needs the raw Helius tx cache, which is not shipped", file=sys.stderr)
        print("in the open-source bundle (6 GB+). The downstream pipeline can", file=sys.stderr)
        print("start from data/sandwiches_recall_wide.jsonl instead.", file=sys.stderr)
        sys.exit(1)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    size = os.path.getsize(SRC)
    pending = {}
    n = 0
    bytes_read = 0
    stats = defaultdict(int)
    flushed = 0

    with open(SRC, "rb", buffering=1 << 20) as f, open(OUT, "w", buffering=1 << 16) as out:
        while True:
            line = f.readline()
            if not line:
                break
            bytes_read += len(line)
            try:
                r = json.loads(line)
            except Exception:
                continue
            b = r.get("_bundle") or {}
            bid = b.get("bundle_id")
            cnt = b.get("bundle_tx_count")
            sig = r.get("signature")
            if not (bid and sig and cnt):
                continue
            entry = {
                "pos": b.get("bundle_tx_pos"),
                "sig": sig,
                "signer": r.get("feePayer"),
                "slot": r.get("slot"),
                "ts": r.get("timestamp"),
                "deltas": tx_deltas(r),
            }
            buf = pending.get(bid)
            if buf is None:
                buf = {"entries": [], "expected": cnt}
                pending[bid] = buf
            buf["entries"].append(entry)
            if len(buf["entries"]) >= buf["expected"]:
                entries = pending.pop(bid)["entries"]
                if len(entries) < 3:
                    stats["too_short"] += 1
                else:
                    res = classify_bundle(bid, entries)
                    if res is None:
                        stats["no_wallet_swap_pair"] += 1
                    else:
                        stats[res["verdict"]] += 1
                        out.write(json.dumps(res, separators=(",", ":")) + "\n")
                flushed += 1
            n += 1
            if n % 500_000 == 0:
                pct = bytes_read / size * 100
                rate = n / (time.time() - t0)
                print(f"  {n:>10,} tx  {pct:5.1f}%  {rate:>7,.0f} tx/s  pending={len(pending):,}  flushed={flushed:,}", flush=True)

        for bid, buf in pending.items():
            entries = buf["entries"]
            if len(entries) < 3:
                stats["too_short"] += 1
                continue
            res = classify_bundle(bid, entries)
            if res is None:
                stats["no_wallet_swap_pair"] += 1
            else:
                stats[res["verdict"]] += 1
                out.write(json.dumps(res, separators=(",", ":")) + "\n")

    dt = time.time() - t0
    total = sum(stats.values())
    print(f"\ndone in {dt/60:.1f} min")
    print(f"\n=== verdict counts (over {total:,} bundles) ===")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k:35s} {v:7d}   {v/total*100:5.2f}%")
    print(f"\noutput: {OUT}")


if __name__ == "__main__":
    main()
