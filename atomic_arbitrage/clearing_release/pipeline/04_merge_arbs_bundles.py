"""
04_merge_arbs_bundles.py
Join arbs_*.json with the matching bundles_*.json by signature.

Added fields:
  in_bundle              bool | None   (None = not queried)
  bundle_id              str | None
  bundle_slot            int | None
  bundle_validator       str | None
  bundle_tip_lamports    int | None    (actual landed tip recorded by Jito)
  bundle_tx_count        int | None    (number of tx in the bundle)
  bundle_tx_index        int | None    (0-based position of this tx within the bundle)
  bundle_tx_signatures   list | None   (signatures of all tx in the bundle)
  bundle_timestamp       str | None
  bundle_tippers         list | None

Usage : python 04_merge_arbs_bundles.py
Output: merged_arbs/merged_*.json (resumable: existing files are skipped)
"""

import json
import glob
from pathlib import Path

ARBS_DIR    = Path("jup_arb_data_cursor_std")
BUNDLES_DIR = Path("jito_bundle_results")
OUT_DIR     = Path("merged_arbs")
OUT_DIR.mkdir(exist_ok=True)


def load_bundle_map(bundles_file: Path) -> dict[str, dict]:
    """Return {signature: bundle_record}."""
    data = json.loads(bundles_file.read_text())
    return {r["signature"]: r for r in data}


def merge_record(arb: dict, bundle_rec: dict | None) -> dict:
    result = dict(arb)

    if bundle_rec is None:
        # No matching bundles file (not queried)
        result["in_bundle"]            = None
        result["bundle_id"]            = None
        result["bundle_slot"]          = None
        result["bundle_validator"]     = None
        result["bundle_tip_lamports"]  = None
        result["bundle_tx_count"]      = None
        result["bundle_tx_index"]      = None
        result["bundle_tx_signatures"] = None
        result["bundle_timestamp"]     = None
        result["bundle_tippers"]       = None
        return result

    b_detail  = bundle_rec.get("bundle") or {}
    tx_sigs   = b_detail.get("txSignatures", [])
    sig       = arb["signature"]

    # 0-based position of this tx in the bundle
    try:
        tx_index = tx_sigs.index(sig)
    except ValueError:
        tx_index = None

    result["in_bundle"]            = bundle_rec.get("in_bundle", False)
    result["bundle_id"]            = bundle_rec.get("bundle_id")
    result["bundle_slot"]          = b_detail.get("slot")
    result["bundle_validator"]     = b_detail.get("validator")
    result["bundle_tip_lamports"]  = b_detail.get("landedTipLamports")
    result["bundle_tx_count"]      = len(tx_sigs) if tx_sigs else None
    result["bundle_tx_index"]      = tx_index
    result["bundle_tx_signatures"] = tx_sigs if tx_sigs else None
    result["bundle_timestamp"]     = b_detail.get("timestamp")
    result["bundle_tippers"]       = b_detail.get("tippers")
    return result


def main():
    arbs_files = sorted(ARBS_DIR.glob("arbs_*.json"))
    print(f"arbs files: {len(arbs_files)}\n")

    total_arbs = total_in_bundle = total_no_bundle_file = 0

    for arbs_file in arbs_files:
        num         = arbs_file.stem.split("_")[1]       # "0001"
        bundles_file = BUNDLES_DIR / f"bundles_{num}.json"
        out_file     = OUT_DIR     / f"merged_{num}.json"

        if out_file.exists():
            data = json.loads(out_file.read_text())
            ib   = sum(1 for r in data if r.get("in_bundle") is True)
            print(f"  [skip] merged_{num}.json already exists  "
                  f"in_bundle={ib}/{len(data)}  ({ib/len(data)*100:.1f}%)")
            total_arbs      += len(data)
            total_in_bundle += ib
            continue

        arbs = json.loads(arbs_file.read_text())

        if bundles_file.exists():
            bundle_map      = load_bundle_map(bundles_file)
            has_bundle_file = True
        else:
            bundle_map      = {}
            has_bundle_file = False
            total_no_bundle_file += len(arbs)

        merged = [
            merge_record(arb, bundle_map.get(arb["signature"]) if has_bundle_file else None)
            for arb in arbs
        ]

        in_bundle   = sum(1 for r in merged if r.get("in_bundle") is True)
        multi_tx    = sum(1 for r in merged if (r.get("bundle_tx_count") or 0) > 1)
        out_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2))

        status = "ok" if has_bundle_file else "WARN (no bundle file)"
        print(f"  {status} merged_{num}.json  "
              f"in_bundle={in_bundle}/{len(merged)} ({in_bundle/len(merged)*100:.1f}%)  "
              f"multi-tx bundle={multi_tx}")

        total_arbs      += len(merged)
        total_in_bundle += in_bundle

    print(f"\n─── summary ───")
    print(f"  total arbs   : {total_arbs:,}")
    print(f"  in_bundle    : {total_in_bundle:,}  ({total_in_bundle/total_arbs*100:.1f}%)")
    if total_no_bundle_file:
        print(f"  no bundle file: {total_no_bundle_file:,} records (in_bundle=None)")
    print(f"\nOutput directory: {OUT_DIR}/")


if __name__ == "__main__":
    main()
