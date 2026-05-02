#!/usr/bin/env python3
"""Deduplicate by mint across 4 Mayan Solana token JSON files and count unique tokens."""

import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FILES = [
    "mayan_tokens_solana_nonPortal_true_spl2022.json",
    "mayan_tokens_solana_nonPortal_false_spl2022.json",
    "mayan_tokens_solana_nonPortal_true_spl.json",
    "mayan_tokens_solana_nonPortal_false_spl.json",
]


def main():
    all_mints = set()
    per_file = {}

    for fname in FILES:
        path = os.path.join(SCRIPT_DIR, fname)
        if not os.path.exists(path):
            print(f"WARNING: File not found: {fname}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tokens = data.get("solana") or []
        mints = {t.get("mint") for t in tokens if t.get("mint")}
        per_file[fname] = len(mints)
        all_mints |= mints

    print("Token count per file (by entry count, not deduplicated):")
    for fname, count in per_file.items():
        print(f"  {fname}: {count}")
    print()
    print("After deduplication by mint, total unique tokens across 4 JSONs:", len(all_mints))

    out_path = os.path.join(SCRIPT_DIR, "mayan_solana_unique_mints.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sorted(all_mints), f, indent=0)
    print(f"Unique mint list saved to: {out_path}")


if __name__ == "__main__":
    main()
