"""
Batch scan all JSON transaction logs in data_collection/data folder and count transactions containing liquidation-related keywords.
"""
import json
import os
import re
import sys

# Force flush output
sys.stdout.reconfigure(line_buffering=True)

# Data directory
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data_collection", "data")
OUTPUT_PATH = os.path.join(os.path.dirname(
    __file__), "batch_liquidation_summary.json")

# Liquidation-related keywords (case-insensitive, partial match)
LIQUIDATION_PATTERN = re.compile(r'liquidat', re.IGNORECASE)


def extract_slot_range(filename):
    """Extract slot range from filename"""
    match = re.search(r'mev_(\d+)_(\d+)\.json', filename)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return filename


def scan_liquidation(file_path):
    """Scan the file for transactions containing liquidation-related logs, distinguishing success and failure"""
    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except Exception as e:
            print(f"    Parse error: {e}", flush=True)
            return 0, 0, 0

    total_tx = 0
    liquidation_success = 0
    liquidation_fail = 0

    for slot_id, transactions in data.items():
        for tx in transactions:
            total_tx += 1
            logs = tx.get('logs') or []
            has_err = tx.get('has_err', False)

            # Check if logs contain liquidation-related keywords
            for log in logs:
                if LIQUIDATION_PATTERN.search(log):
                    if has_err:
                        liquidation_fail += 1
                    else:
                        liquidation_success += 1
                    break  # Count each transaction only once

    return total_tx, liquidation_success, liquidation_fail


def main():
    if not os.path.exists(DATA_DIR):
        print(f"Error: data directory not found {DATA_DIR}")
        return

    # Get all JSON files (exclude macOS temporary files starting with ._)
    json_files = [f for f in os.listdir(DATA_DIR)
                  if f.endswith('.json') and not f.startswith('._')]
    json_files.sort()

    print(f"Found {len(json_files)} JSON files in total\n", flush=True)

    # Try to load existing results for resume capability
    summary = {}
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
                summary = json.load(f)
            print(f"Loaded previous results containing {len(summary)} records, will skip already processed files\n", flush=True)
        except:
            pass

    for i, filename in enumerate(json_files):
        file_path = os.path.join(DATA_DIR, filename)
        slot_range = extract_slot_range(filename)

        # Skip already processed files
        if slot_range in summary:
            print(f"[{i+1}/{len(json_files)}] Skipping (already processed): {filename}", flush=True)
            continue

        print(f"[{i+1}/{len(json_files)}] Processing: {filename}", flush=True)

        total_tx, liquidation_success, liquidation_fail = scan_liquidation(
            file_path)
        liquidation_total = liquidation_success + liquidation_fail

        summary[slot_range] = {
            "total_tx": total_tx,
            "liquidation_success": liquidation_success,
            "liquidation_fail": liquidation_fail,
            "liquidation_total": liquidation_total
        }

        print(
            f"    Total tx: {total_tx}, Liquidation success: {liquidation_success}, Liquidation fail: {liquidation_fail}", flush=True)

        # Save after each file to prevent progress loss on error
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    # Final save of summary results
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"Summary results saved to: {OUTPUT_PATH}")
    print(f"{'='*60}")

    # Print overall statistics
    total_all_tx = sum(v['total_tx'] for v in summary.values())
    total_liq_success = sum(v['liquidation_success'] for v in summary.values())
    total_liq_fail = sum(v['liquidation_fail'] for v in summary.values())
    total_liq_all = total_liq_success + total_liq_fail

    print(f"\nOverall statistics:")
    print(f"  Total transactions:              {total_all_tx}")
    print(f"  Successful liquidation tx:       {total_liq_success}")
    print(f"  Failed liquidation tx:           {total_liq_fail}")
    print(f"  Total liquidation-related tx:    {total_liq_all}")
    if total_all_tx > 0:
        print(f"  Liquidation tx percentage:       {total_liq_all / total_all_tx * 100:.2f}%")


if __name__ == "__main__":
    main()
