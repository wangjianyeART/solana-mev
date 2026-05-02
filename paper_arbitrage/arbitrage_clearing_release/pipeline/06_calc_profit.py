"""
06_calc_profit.py
Add derived profit columns to every (or one) file in merged_arbs_clean/.

Fields written:
  arb_cost_lamports          — fee + tip (lamports)
  arb_gross_profit_lamports  — sol_lamports (wSOL arbitrages)
  arb_net_profit_lamports    — gross - cost (wSOL arbitrages)
  arb_gross_profit_raw       — output_raw - input_raw (non-wSOL, token raw units)
  arb_gross_profit_rate_pct  — gross / input_raw × 100
  arb_net_profit_rate_pct    — net / input_raw × 100 (wSOL only)

Usage:
  python 06_calc_profit.py            # process all (skip files that already have the columns)
  python 06_calc_profit.py --file 0010
  python 06_calc_profit.py --force    # recompute even if columns already exist
"""

import json
import argparse
from pathlib import Path

WSOL = "So11111111111111111111111111111111111111112"
DIR  = Path("merged_arbs_clean")


def calc(r: dict) -> dict:
    in_b = r.get("in_bundle")
    btc  = r.get("bundle_tx_count") or 0
    fee  = r.get("tx_fee_lamports", 0) or 0

    if in_b is True and btc >= 2:
        tip = r.get("bundle_tip_lamports") or 0
    elif in_b is True:
        tip = r.get("jito_tip_lamports") or 0
    else:
        tip = 0

    cost = fee + tip
    r["arb_cost_lamports"] = cost

    mints  = r.get("arb_token_mints", [])
    arb_io = r.get("arb_io", {})

    if not mints:
        return r

    tok = mints[0]
    io  = arb_io.get(tok, {})
    inp = io.get("input_raw", 0) or 0
    out = io.get("output_raw", 0) or 0

    if tok == WSOL:
        gross = r.get("sol_lamports", 0) or 0
        net   = gross - cost
        r["arb_gross_profit_lamports"] = gross
        r["arb_net_profit_lamports"]   = net
        r["arb_gross_profit_raw"]      = None
        r["arb_gross_profit_rate_pct"] = round(gross / inp * 100, 6) if inp > 0 else None
        r["arb_net_profit_rate_pct"]   = round(net   / inp * 100, 6) if inp > 0 else None
    else:
        gross_raw = out - inp
        r["arb_gross_profit_raw"]      = gross_raw
        r["arb_gross_profit_lamports"] = None
        r["arb_net_profit_lamports"]   = None
        r["arb_gross_profit_rate_pct"] = round(gross_raw / inp * 100, 6) if inp > 0 else None
        r["arb_net_profit_rate_pct"]   = None

    return r


def process_file(path: Path, force: bool):
    data = json.loads(path.read_text())
    if not force and data and "arb_cost_lamports" in data[0]:
        print(f"  [skip] {path.name}  (profit columns already present; use --force to recompute)")
        return

    updated = [calc(r) for r in data]
    path.write_text(json.dumps(updated, ensure_ascii=False, indent=2))

    wsol = [r for r in updated if WSOL in r.get("arb_token_mints", [])]
    pos  = sum(1 for r in wsol if (r.get("arb_net_profit_lamports") or 0) > 0)
    neg  = sum(1 for r in wsol if (r.get("arb_net_profit_lamports") or 0) <= 0)
    print(f"  done {path.name}  total={len(updated):,}  wSOL={len(wsol):,}  net>0: {pos:,}  net<=0: {neg:,}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file",  type=str, default=None, help="only process one file, e.g. --file 0010")
    parser.add_argument("--force", action="store_true",    help="force recomputation (overwrite existing columns)")
    args = parser.parse_args()

    if args.file:
        files = [DIR / f"merged_{args.file}.json"]
    else:
        files = sorted(DIR.glob("merged_*.json"))

    print(f"Processing {len(files)} files (force={args.force})...\n")
    for f in files:
        if not f.exists():
            print(f"  [missing] {f}")
            continue
        process_file(f, args.force)

    print("\nDone.")


if __name__ == "__main__":
    main()
