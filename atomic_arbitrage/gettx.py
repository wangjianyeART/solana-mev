"""
按 slot 区间抓取区块内所有非投票交易，支持 1 或 2 个 signer（含 Jito tips 等双签场景）。
输出包含 num_signers、logs 等，供套利检测与交易链长度研究使用。
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
    raise ValueError("请在 .env 中设置 HELIUS_API_KEY")

RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
CONCURRENT_WORKERS = 20
VOTE_PROGRAM_ID = "Vote111111111111111111111111111111111111111"


def get_num_signers(transaction: dict) -> int:
    """
    从 transaction 解析签名者数量：优先用 signatures 数组长度（每个 signer 一个签名），
    否则用 message.header.numRequiredSignatures。双签（如主签 + Jito tips）会正确返回 2。
    """
    sigs = transaction.get("signatures")
    if sigs is not None and len(sigs) > 0:
        return len(sigs)
    message = transaction.get("message") or {}
    if isinstance(message, dict):
        header = message.get("header") or {}
        n = header.get("numRequiredSignatures")
        if n is not None:
            return int(n)
    return 1


def fetch_block_transactions(slot: int):
    """
    抓取单个 Slot 的所有非投票交易。
    maxSupportedTransactionVersion=0 以获取 MEV 交易的完整 Meta（含 logMessages）。
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
    """并发抓取 [start_slot, end_slot] 并保存。output_dir 为 None 时保存到当前目录。"""
    if start_slot > end_slot:
        start_slot, end_slot = end_slot, start_slot
        print("已自动交换区间为: start <= end\n")
    slots = list(range(start_slot, end_slot + 1))
    all_data = {}
    out_dir = output_dir or os.getcwd()

    print(f"开始抓取 Slot: {start_slot} -> {end_slot} (支持 1/2 signer)")

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
                    f"  Slot {slot} 完成 | 有效交易: {len(transactions)} (其中 2 signer: {n_two})")
            else:
                print(f"  Slot {slot} 跳过或无数据")

    output_path = os.path.join(
        out_dir, f"arb_{start_slot}_{end_slot}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"\n数据已保存: {output_path}")
    return output_path


if __name__ == "__main__":
    START = 324115888
    END = 324115990
    main_process(START, END)
