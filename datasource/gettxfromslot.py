"""
Fetch all non-vote transactions within a slot range, supporting 1 or 2 signers (including Jito tips and other dual-signer scenarios).
Output includes num_signers, logs, etc., for arbitrage detection and transaction chain length analysis.
"""
import os
import json
import time
import requests
import concurrent.futures
from dotenv import load_dotenv

load_dotenv()
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")
if not HELIUS_API_KEY:
    raise ValueError("Please set HELIUS_API_KEY in .env")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
CONCURRENT_WORKERS = 20
VOTE_PROGRAM_ID = "Vote111111111111111111111111111111111111111"


def get_num_signers(transaction: dict) -> int:
    """
    Parse the number of signers from transaction.message, compatible with both Legacy and Versioned transactions.
    Dual-signer scenarios (e.g., primary signer + Jito tips) will return 2.
    """
    message = transaction.get("message") or {}
    if isinstance(message, dict):
        header = message.get("header") or {}
        n = header.get("numRequiredSignatures")
        if n is not None:
            return int(n)
        # Some RPCs may use different fields
        if "accountKeys" in message:
            # Without header, cannot distinguish; conservatively use 1; could infer from signature list if available
            pass
    return 1


def fetch_block_transactions(slot: int):
    """
    Fetch all non-vote transactions for a single slot.
    maxSupportedTransactionVersion=0 to get full meta for MEV transactions (including logMessages).
    """
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
                "rewards": False,
            },
        ],
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
            message = transaction.get("message", {}) or {}
            account_keys = message.get("accountKeys", [])

            if VOTE_PROGRAM_ID in account_keys:
                continue

            num_signers = get_num_signers(transaction)

            non_vote_data.append({
                "sig": (transaction.get("signatures") or [""])[0],
                "slot": slot,
                "accounts": account_keys,
                "num_signers": num_signers,
                "fee": meta.get("fee"),
                "has_err": meta.get("err") is not None,
                "inner_instructions": meta.get("innerInstructions", []),
                "pre_token_balances": meta.get("preTokenBalances", []),
                "post_token_balances": meta.get("postTokenBalances", []),
                "logs": meta.get("logMessages", []),
                "pre_balances": meta.get("preBalances", []),
                "post_balances": meta.get("postBalances", []),
            })

        return slot, non_vote_data

    except Exception as e:
        print(f"Error fetching slot {slot}: {e}")
        return slot, []


def main_process(start_slot: int, end_slot: int, output_dir: str = None):
    """Concurrently fetch [start_slot, end_slot] and save. If output_dir is None, saves to the current directory."""
    slots = list(range(start_slot, end_slot + 1))
    all_data = {}
    out_dir = output_dir or os.getcwd()

    print(f"Starting to fetch slots: {start_slot} -> {end_slot} (supports 1/2 signers)")

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS) as executor:
        future_to_slot = {executor.submit(
            fetch_block_transactions, s): s for s in slots}
        for future in concurrent.futures.as_completed(future_to_slot):
            slot, transactions = future.result()
            if transactions:
                all_data[str(slot)] = transactions
                n_two = sum(1 for t in transactions if t.get(
                    "num_signers", 1) == 2)
                print(
                    f"  Slot {slot} done | valid txs: {len(transactions)} (of which 2-signer: {n_two})")
            else:
                print(f"  Slot {slot} skipped or no data")

    output_path = os.path.join(
        out_dir, f"arb_{start_slot}_{end_slot}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"\nData saved: {output_path}")
    return output_path


if __name__ == "__main__":
    START = 324115988
    END = 324115990
    main_process(START, END)
