"""
Batch detect arbitrage transactions in all JSON files under data_collection/data folder and aggregate statistics.
"""
import json
import os
import re
import sys
from detect_arbitrage import analyze_file

# Force flush output
sys.stdout.reconfigure(line_buffering=True)

# Data directory
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data_collection", "data")
OUTPUT_PATH = os.path.join(os.path.dirname(
    __file__), "batch_arbitrage_summary.json")


def extract_slot_range(filename):
    """Extract slot range from filename, e.g. mev_252308623_252308773.json -> '252308623_252308773'"""
    match = re.search(r'mev_(\d+)_(\d+)\.json', filename)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    return filename


def main():
    if not os.path.exists(DATA_DIR):
        print(f"Error: data directory not found {DATA_DIR}")
        return

    # Get all JSON files (exclude macOS temporary files starting with ._)
    json_files = [f for f in os.listdir(DATA_DIR)
                  if f.endswith('.json') and not f.startswith('._')]
    json_files.sort()

    print(f"Found {len(json_files)} JSON files in total\n")

    summary = {}

    for i, filename in enumerate(json_files):
        file_path = os.path.join(DATA_DIR, filename)
        slot_range = extract_slot_range(filename)

        print(f"[{i+1}/{len(json_files)}] Processing: {filename}", flush=True)

        # Analyze file (silent mode, no detail printing)
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
            except Exception as e:
                print(f"    Parse error: {e}", flush=True)
                continue

        from detect_arbitrage import detect_sol_arbitrage, detect_stablecoin_arbitrage, USDC_MINT, USDT_MINT
        arbs = []
        success_count = 0
        fail_count = 0

        for slot_id, transactions in data.items():
            for tx in transactions:
                # Count successful/failed transactions
                if tx.get('has_err'):
                    fail_count += 1
                else:
                    success_count += 1

                sol_arb = detect_sol_arbitrage(tx, slot_id)
                if sol_arb:
                    arbs.append(sol_arb)
                usdc_arb = detect_stablecoin_arbitrage(
                    tx, slot_id, USDC_MINT, "USDC")
                if usdc_arb:
                    arbs.append(usdc_arb)
                usdt_arb = detect_stablecoin_arbitrage(
                    tx, slot_id, USDT_MINT, "USDT")
                if usdt_arb:
                    arbs.append(usdt_arb)

        # Count arbitrage occurrences and profits by type
        sol_arbs = [a for a in arbs if a['type'] == 'SOL']
        usdc_arbs = [a for a in arbs if a['type'] == 'USDC']
        usdt_arbs = [a for a in arbs if a['type'] == 'USDT']
        sol_count = len(sol_arbs)
        usdc_count = len(usdc_arbs)
        usdt_count = len(usdt_arbs)
        total_count = len(arbs)
        sol_profit = sum(a['profit'] for a in sol_arbs)
        usdc_profit = sum(a['profit'] for a in usdc_arbs)
        usdt_profit = sum(a['profit'] for a in usdt_arbs)

        # Save to summary
        summary[slot_range] = {
            "success_tx": success_count,
            "fail_tx": fail_count,
            "sol_count": sol_count,
            "usdc_count": usdc_count,
            "usdt_count": usdt_count,
            "total_arb": total_count,
            "sol_profit": round(sol_profit, 9),
            "usdc_profit": round(usdc_profit, 6),
            "usdt_profit": round(usdt_profit, 6),
        }

        print(f"    Success tx: {success_count}, Failed tx: {fail_count} | SOL: {sol_count} ({sol_profit:.6f}), USDC: {usdc_count} ({usdc_profit:.4f}), USDT: {usdt_count} ({usdt_profit:.4f}), Arbitrage: {total_count}", flush=True)

    # Save summary results
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"Summary results saved to: {OUTPUT_PATH}")
    print(f"{'='*60}")

    # Print overall statistics (including profits)
    total_success = sum(v['success_tx'] for v in summary.values())
    total_fail = sum(v['fail_tx'] for v in summary.values())
    total_sol = sum(v['sol_count'] for v in summary.values())
    total_usdc = sum(v['usdc_count'] for v in summary.values())
    total_usdt = sum(v['usdt_count'] for v in summary.values())
    total_arb = sum(v['total_arb'] for v in summary.values())
    total_sol_profit = sum(v['sol_profit'] for v in summary.values())
    total_usdc_profit = sum(v['usdc_profit'] for v in summary.values())
    total_usdt_profit = sum(v['usdt_profit'] for v in summary.values())

    print(f"\nOverall statistics:")
    print(f"  Total successful tx:  {total_success}")
    print(f"  Total failed tx:      {total_fail}")
    print(f"  Total SOL arbitrage:  {total_sol}  |  Total profit: {total_sol_profit:.6f} SOL")
    print(f"  Total USDC arbitrage: {total_usdc}  |  Total profit: {total_usdc_profit:.4f} USDC")
    print(f"  Total USDT arbitrage: {total_usdt}  |  Total profit: {total_usdt_profit:.4f} USDT")
    print(f"  Total arbitrage tx:   {total_arb}")


if __name__ == "__main__":
    main()
