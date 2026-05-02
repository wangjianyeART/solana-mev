"""
05_reclean_merged.py
Apply additional false-positive filters to merged_arbs/ to remove records
that are actually one-way swaps misclassified as arbitrages:
  Case 1: wSOL → token swap     (sol_lamports < -1000)
  Case 2: token → wSOL swap     (arb_io[wSOL].input_raw == 0 and sol_lamports > 1000)
  Case 3: arb_io shows a one-leg buy or a >2× input/output imbalance for any mint

Outputs:
  merged_arbs_clean/merged_*.json           — cleaned data
  jup_arb_data_cursor_std/cursor_stats.json — patched in place (cnt_arb / cnt_non_arb)
"""

import json
import glob
from pathlib import Path
from collections import defaultdict

WSOL       = "So11111111111111111111111111111111111111112"
IN_DIR     = Path("merged_arbs")
OUT_DIR    = Path("merged_arbs_clean")
STATS_FILE = Path("jup_arb_data_cursor_std/cursor_stats.json")

OUT_DIR.mkdir(exist_ok=True)


def is_false_positive(r: dict) -> bool:
    """True iff this record is a misclassified one-way swap that should be dropped."""
    # Case 3: any mint in arb_io has a >2× input/output imbalance → one-leg swap.
    # Also covers output_raw=0 with input_raw>0 (sold but not bought back).
    arb_io = r.get("arb_io", {})
    for tok, io in arb_io.items():
        inp = io.get("input_raw", 0)
        out = io.get("output_raw", 0)
        if inp > 0 and out == 0:
            return True   # bought but never sold back → one-leg
        if inp > 0 and out > 0:
            if out / inp > 2 or inp / out > 2:
                return True

    if WSOL not in r.get("arb_token_mints", []):
        return False  # non-wSOL arbitrage; nothing further to check

    sol = r.get("sol_lamports", 0)
    wsol_io    = r.get("arb_io", {}).get(WSOL, {})
    input_raw  = wsol_io.get("input_raw", 0)

    # Case 1: wSOL net dropped > 1000 lamports → sold wSOL for token (one-way swap)
    if sol < -1_000:
        return True

    # Case 2: wSOL input=0 but net up > 1000 → sold token for wSOL (one-way swap)
    if input_raw == 0 and sol > 1_000:
        return True

    return False


# ── Step 1: filter merged files; record per-cursor removal counts ────
removed_by_cursor = defaultdict(int)   # cursor_ts_str → removed count
total_before = total_after = total_removed = 0

files = sorted(IN_DIR.glob("merged_*.json"))
print(f"Processing {len(files)} merged files...\n")

for f in files:
    data = json.load(open(f))
    clean = []
    n_removed = 0
    for r in data:
        if is_false_positive(r):
            removed_by_cursor[r["cursor_ts_str"]] += 1
            n_removed += 1
        else:
            clean.append(r)

    out_file = OUT_DIR / f.name
    out_file.write_text(json.dumps(clean, ensure_ascii=False, indent=2))

    total_before  += len(data)
    total_after   += len(clean)
    total_removed += n_removed

    if n_removed:
        print(f"  {f.name}  {len(data)} → {len(clean)}  (removed {n_removed})")
    else:
        print(f"  {f.name}  {len(data)} → {len(clean)}")

print(f"\nTotal removed: {total_removed}  ({total_removed/total_before*100:.3f}%)")
print(f"Output directory: {OUT_DIR}/\n")

# ── Step 2: patch cursor_stats.json in place ─────────────────────────
stats = json.load(open(STATS_FILE))

# build a cursor_ts_str → index lookup
idx_map = {s["cursor_ts_str"]: i for i, s in enumerate(stats)}

stats_updated = 0
for cts, cnt in removed_by_cursor.items():
    if cts not in idx_map:
        print(f"  [warn] cursor_stats has no entry for: {cts}")
        continue
    i = idx_map[cts]
    stats[i]["cnt_arb"]     -= cnt
    stats[i]["cnt_non_arb"] += cnt
    # recompute rates
    parsed = stats[i]["cnt_parsed"]
    if parsed > 0:
        stats[i]["arb_rate_pct"]     = round(stats[i]["cnt_arb"]     / parsed * 100, 2)
        stats[i]["non_arb_rate_pct"] = round(stats[i]["cnt_non_arb"] / parsed * 100, 2)
    stats_updated += 1

STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2))
print(f"cursor_stats.json updated for {stats_updated} records")
print(f"  cursor windows touched: {stats_updated}")
print(f"  total arbs removed    : {total_removed}")
