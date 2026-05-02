#!/usr/bin/env python3
"""Step 2 — Recall-oriented classification of Pattern-A candidates.

Step 1 (1_detect_pattern_a.py) emits sandwich candidates with a strict
"sandwich_confirmed" verdict that requires positive bot profit. This script
keeps the same wallet-flow evidence but assigns confidence tiers based on
quote-inventory symmetry alone:

  strong   : victim aligned, sym_ratio <= 0.10
  probable : victim aligned, sym_ratio <= 0.25
  possible : victim aligned, sym_ratio <= 0.50
  weak     : victim aligned, sym_ratio <= 1.00 (only if --weak-sym set)

Profit is reported but never used as a validity filter — losing sandwiches
are a central object of study.

Input:  data/sandwiches_a.jsonl
Output: data/sandwiches_recall_wide.jsonl  (use --weak-sym 1.0)
        data/sandwiches_recall_wide_summary.txt
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IN = ROOT / "data" / "sandwiches_a.jsonl"
DEFAULT_OUT = ROOT / "data" / "sandwiches_recall_wide.jsonl"
DEFAULT_SUMMARY = ROOT / "data" / "sandwiches_recall_wide_summary.txt"


def victim_aligned(rec):
    return any(v.get("aligned") for v in rec.get("victims") or [])


def tier_for(rec, strong_sym, probable_sym, possible_sym, weak_sym=None):
    if not victim_aligned(rec):
        return None
    sym = rec.get("sym_ratio")
    if sym is None:
        return None
    if sym <= strong_sym:
        return "strong"
    if sym <= probable_sym:
        return "probable"
    if sym <= possible_sym:
        return "possible"
    if weak_sym is not None and sym <= weak_sym:
        return "weak"
    return None


def compact_record(rec, tier):
    profit_sol = None
    if rec.get("base_mint") == "So11111111111111111111111111111111111111112":
        profit_sol = (rec.get("front_base") or 0) + (rec.get("back_base") or 0)
    elif rec.get("quote_mint") == "So11111111111111111111111111111111111111112":
        profit_sol = (rec.get("front_quote") or 0) + (rec.get("back_quote") or 0)

    out = {
        "confidence": tier,
        "bundle_id": rec.get("bundle_id"),
        "slot": rec.get("slot"),
        "ts": rec.get("ts"),
        "attacker": rec.get("attacker"),
        "front_sig": rec.get("front_sig"),
        "back_sig": rec.get("back_sig"),
        "front_signer": rec.get("front_signer"),
        "back_signer": rec.get("back_signer"),
        "same_signer": rec.get("same_signer"),
        "i": rec.get("i"),
        "j": rec.get("j"),
        "base_mint": rec.get("base_mint"),
        "quote_mint": rec.get("quote_mint"),
        "direction": rec.get("direction"),
        "front_base": rec.get("front_base"),
        "front_quote": rec.get("front_quote"),
        "back_base": rec.get("back_base"),
        "back_quote": rec.get("back_quote"),
        "sym_ratio": rec.get("sym_ratio"),
        "profit_base": rec.get("profit_base"),
        "profit_sol": round(profit_sol, 12) if profit_sol is not None else None,
        "old_verdict": rec.get("verdict"),
        "victims": rec.get("victims") or [],
    }
    if profit_sol is not None:
        out["profit_sol_positive"] = profit_sol > 0
    return out


def pct(n, d):
    return 100.0 * n / d if d else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=DEFAULT_IN)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("--strong-sym", type=float, default=0.10)
    ap.add_argument("--probable-sym", type=float, default=0.25)
    ap.add_argument("--possible-sym", type=float, default=0.50)
    ap.add_argument("--weak-sym", type=float, default=1.0,
                    help="Wider recall tier (default 1.0 = up to 100%% asymmetry).")
    args = ap.parse_args()

    if not (args.strong_sym <= args.probable_sym <= args.possible_sym):
        raise SystemExit("Require strong-sym <= probable-sym <= possible-sym")
    if args.weak_sym is not None and args.weak_sym < args.possible_sym:
        raise SystemExit("Require possible-sym <= weak-sym")

    counts = Counter()
    old_verdicts = Counter()
    tier_old = defaultdict(Counter)
    profit_sol_by_tier = defaultdict(Counter)
    total = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.input.open() as f, args.output.open("w") as out:
        for line in f:
            total += 1
            rec = json.loads(line)
            old_verdicts[rec.get("verdict", "<none>")] += 1
            tier = tier_for(rec, args.strong_sym, args.probable_sym,
                            args.possible_sym, args.weak_sym)
            if tier is None:
                counts["excluded"] += 1
                continue
            counts[tier] += 1
            tier_old[tier][rec.get("verdict", "<none>")] += 1
            compact = compact_record(rec, tier)
            profit_sol = compact.get("profit_sol")
            if profit_sol is not None:
                profit_sol_by_tier[tier][
                    "positive" if profit_sol > 0 else "non_positive"
                ] += 1
            out.write(json.dumps(compact, separators=(",", ":")) + "\n")

    tiers = ["strong", "probable", "possible"]
    if args.weak_sym is not None:
        tiers.append("weak")

    kept = sum(counts[tier] for tier in tiers)
    lines = []
    lines.append("Recall-oriented sandwich classification")
    lines.append(f"input:  {args.input}")
    lines.append(f"output: {args.output}")
    lines.append(
        f"thresholds: strong<={args.strong_sym}, probable<={args.probable_sym}, "
        f"possible<={args.possible_sym}"
    )
    if args.weak_sym is not None:
        lines[-1] += f", weak<={args.weak_sym}"
    lines.append("")
    lines.append(f"total candidates read: {total:,}")
    lines.append(f"kept recall sample:    {kept:,} ({pct(kept, total):.2f}%)")
    for tier in tiers:
        n = counts[tier]
        pos = profit_sol_by_tier[tier]["positive"]
        non_pos = profit_sol_by_tier[tier]["non_positive"]
        lines.append(
            f"  {tier:8s}: {n:7,} ({pct(n, kept):5.1f}% of kept), "
            f"SOL profit>0={pos:,}, SOL profit<=0={non_pos:,}"
        )
    lines.append(f"  excluded: {counts['excluded']:,}")
    lines.append("")
    lines.append("old verdicts in input:")
    for k, v in old_verdicts.most_common():
        lines.append(f"  {k:28s} {v:8,}")
    lines.append("")
    lines.append("old verdict composition by new tier:")
    for tier in tiers:
        lines.append(f"  {tier}:")
        for k, v in tier_old[tier].most_common():
            lines.append(f"    {k:26s} {v:8,}")

    text = "\n".join(lines) + "\n"
    args.summary.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
