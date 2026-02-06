import json
import csv
import os

# Common Solana program ID mapping
PROGRAM_MAP = {
    "675k1q2wEBajSbdPYDrVvEDX1JD6GGM88NScB1F8T82": "Raydium",
    "JUP6LkbZbjS1jKKccwg9K73pL4Y6YhD9G2SScK9eX99": "Jupiter",
    "whirLbMiicVdio4qvUfM99N9H3xsxidS6s7Y9f15oT8": "Orca",
    "CAMMCzo5YL8w4VFF8KVHrS76L3xXqu1fQU6tcaF27U9": "Raydium CLMM",
    "Eo7WjKq67rjJQSZ6A6v7t7V7GfDpsK8uL5W2yS3S9zL": "Meteora",
    "JitoTip1ccEsSTDs2psY2pA688m9zS93zfYdtmsqXW": "Jito Tip"
}


def analyze_arb_file(input_json):
    if not os.path.exists(input_json):
        print(f"File not found: {input_json}")
        return

    with open(input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    results = []

    for slot, txs in data.items():
        for tx in txs:
            if tx.get('has_err'):
                continue

            # 1. Profit calculation (SOL)
            # signer is typically the first account
            net_change_lamports = tx['post_balances'][0] - \
                tx['pre_balances'][0]
            profit_sol = net_change_lamports / 1e9

            # 2. Only record if profit > 0 (or very large absolute value indicates significant activity)
            if profit_sol > 0.0001:  # Filter out tiny fluctuations / fee refunds

                # 3. Identify involved protocols
                involved_programs = []
                for acc in tx['accounts']:
                    if acc in PROGRAM_MAP:
                        involved_programs.append(PROGRAM_MAP[acc])

                # 4. Check if Jito tip was paid (strong MEV indicator)
                has_jito_tip = any("JitoTip" in acc for acc in tx['accounts'])

                results.append({
                    "slot": slot,
                    "signature": tx['sig'],
                    "profit_sol": round(profit_sol, 6),
                    "fee_lamports": tx['fee'],
                    "protocols": "|".join(set(involved_programs)) if involved_programs else "Unknown",
                    "is_mev_tip": "Yes" if has_jito_tip else "No",
                    "signer": tx['accounts'][0]
                })

    # Save to CSV
    output_file = "arbitrage_results.csv"
    if results:
        keys = results[0].keys()
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            dict_writer = csv.DictWriter(f, fieldnames=keys)
            dict_writer.writeheader()
            dict_writer.writerows(results)
        print(f"Scan complete! Found {len(results)} potential arbitrages.")
        print(f"Results saved to: {os.path.abspath(output_file)}")
    else:
        print("No results found. There may be no closed-loop SOL arbitrage in this range.")


if __name__ == "__main__":
    # Use the filename from your latest data fetch
    latest_file = "mev_full_analysis_324115988_324115989.json"
    analyze_arb_file(latest_file)
