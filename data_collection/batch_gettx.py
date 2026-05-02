"""
Batch-read slot ranges from merged_volatility_minutes.csv, fetch transaction data, and save to the data folder.
"""
import os
import csv
import json
import time
import requests
import concurrent.futures
from dotenv import load_dotenv

# Load configuration
load_dotenv()
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise ValueError("Please set HELIUS_API_KEY in .env")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
CONCURRENT_WORKERS = 20
VOTE_PROGRAM_ID = "Vote111111111111111111111111111111111111111"

# Data output directory
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)


def fetch_block_transactions(slot: int):
    """Fetch transaction data for a single slot."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBlock",
        "params": [
            slot,
            {
                "encoding": "json",
                "maxSupportedTransactionVersion": 0,
                "transactionDetails": "full",
                "rewards": False
            }
        ]
    }

    try:
        response = requests.post(RPC_URL, json=payload, timeout=30)
        if response.status_code == 429:
            time.sleep(1)
            return fetch_block_transactions(slot)

        data = response.json()

        if "result" not in data or data["result"] is None:
            return slot, []

        block = data["result"]
        non_vote_data = []

        for tx in block.get("transactions", []):
            meta = tx.get("meta")
            if not meta:
                continue

            transaction = tx.get("transaction", {})
            message = transaction.get("message", {})
            account_keys = message.get("accountKeys", [])

            if VOTE_PROGRAM_ID not in account_keys:
                non_vote_data.append({
                    "sig": transaction.get("signatures", [""])[0],
                    "slot": slot,
                    "accounts": account_keys,
                    "fee": meta.get("fee"),
                    "has_err": meta.get("err") is not None,
                    "inner_instructions": meta.get("innerInstructions", []),
                    "pre_token_balances": meta.get("preTokenBalances", []),
                    "post_token_balances": meta.get("postTokenBalances", []),
                    "logs": meta.get("logMessages", []),
                    "pre_balances": meta.get("preBalances", []),
                    "post_balances": meta.get("postBalances", [])
                })

        return slot, non_vote_data

    except Exception as e:
        print(f"    Error fetching slot {slot}: {e}")
        return slot, []


def process_slot_range(start_slot: int, end_slot: int, output_path: str):
    """Process a single slot range and save to the specified path."""
    slots = list(range(start_slot, end_slot + 1))
    all_data = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        future_to_slot = {executor.submit(
            fetch_block_transactions, s): s for s in slots}

        for future in concurrent.futures.as_completed(future_to_slot):
            slot, transactions = future.result()
            if transactions:
                all_data[slot] = transactions

    # Save JSON
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    return len(all_data)


def main():
    csv_path = os.path.join(os.path.dirname(__file__),
                            "merged_volatility_minutes.csv")

    if not os.path.exists(csv_path):
        print(f"Error: File not found {csv_path}")
        return

    # Read CSV
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("slot_start") and r.get("slot_end") and r["slot_start"] != "N/A":
                rows.append(r)

    print(f"Total {len(rows)} rows to process\n")

    # Deduplicate: process each unique slot range only once
    seen = set()
    unique_rows = []
    for r in rows:
        key = (r["slot_start"], r["slot_end"])
        if key not in seen:
            seen.add(key)
            unique_rows.append(r)

    print(f"After deduplication: {len(unique_rows)} unique slot ranges\n")

    for i, r in enumerate(unique_rows):
        slot_start = int(r["slot_start"])
        slot_end = int(r["slot_end"])
        filename = f"mev_{slot_start}_{slot_end}.json"
        output_path = os.path.join(DATA_DIR, filename)

        # Skip files that already exist
        if os.path.exists(output_path):
            print(f"[{i+1}/{len(unique_rows)}] Skipped (already exists): {filename}")
            continue

        print(f"[{i+1}/{len(unique_rows)}] Processing: {r['category']} | {r['datetime_utc']} | slot {slot_start}-{slot_end}")

        slots_with_data = process_slot_range(slot_start, slot_end, output_path)
        print(f"    -> Saved {filename}, {slots_with_data} slots with data\n")

    print("All done!")


if __name__ == "__main__":
    main()
