#!/usr/bin/env python3
"""
Retrieve failed liquidation transaction signatures

Logic:
1. Fetch Kamino program-related signatures
2. Only process failed transactions (err is not None)
3. For each failed transaction, call getTransaction; if it contains a liquidation instruction, treat it as a "failed liquidation"
4. Output these signatures (can be saved to JSON/text)

Usage:
    python get_failed_liquidate_signatures.py --limit 5000
    python get_failed_liquidate_signatures.py --limit 5000 --before <signature>
    python get_failed_liquidate_signatures.py --limit 5000 -o ./data/failed_liquidate.json
"""

import os
import sys
import json
import time
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any, List, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Reuse constants and detection logic from step1
KAMINO_LENDING_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"
MAX_REQUESTS_PER_SECOND = 50

BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def base58_decode(s: str) -> bytes:
    num = 0
    for char in s:
        num = num * 58 + BASE58_ALPHABET.index(char)
    result = []
    while num > 0:
        num, rem = divmod(num, 256)
        result.append(rem)
    for char in s:
        if char == '1':
            result.append(0)
        else:
            break
    return bytes(reversed(result))


def compute_discriminator(name: str) -> str:
    preimage = f"global:{name}".encode()
    return hashlib.sha256(preimage).digest()[:8].hex()


LIQUIDATION_DISCRIMINATORS: Set[str] = {
    compute_discriminator('liquidateObligationAndRedeemReserveCollateral'),
    compute_discriminator('liquidateObligationAndRedeemReserveCollateralV2'),
    compute_discriminator(
        'liquidate_obligation_and_redeem_reserve_collateral'),
    compute_discriminator(
        'liquidate_obligation_and_redeem_reserve_collateral_v2'),
}


def load_env():
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()


def get_helius_api_key() -> str:
    load_env()
    api_key = os.environ.get('HELIUS_API_KEY')
    if not api_key:
        raise ValueError("HELIUS_API_KEY not found, please configure it in .env")
    return api_key


def is_liquidation_instruction(data: str) -> bool:
    if not data or len(data) < 11:
        return False
    try:
        data_bytes = base58_decode(data)
        if len(data_bytes) < 8:
            return False
        return data_bytes[:8].hex() in LIQUIDATION_DISCRIMINATORS
    except Exception:
        return False


def has_liquidation_instruction(tx: Dict) -> bool:
    if not tx:
        return False
    message = tx.get('transaction', {}).get('message', {})
    account_keys = message.get('accountKeys', [])
    accounts = [acc.get('pubkey', '') if isinstance(
        acc, dict) else acc for acc in account_keys]
    for instr in message.get('instructions', []):
        program_id = instr.get('programId', '')
        if not program_id and 'programIdIndex' in instr:
            idx = instr['programIdIndex']
            if idx < len(accounts):
                program_id = accounts[idx]
        if program_id == KAMINO_LENDING_PROGRAM_ID and is_liquidation_instruction(instr.get('data', '')):
            return True
    return False


class RateLimiter:
    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last_time = time.time()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.time()
            self.tokens = min(self.rate, self.tokens +
                              (now - self.last_time) * self.rate)
            self.last_time = now
            if self.tokens < 1:
                time.sleep((1 - self.tokens) / self.rate)
                self.tokens = 0
            else:
                self.tokens -= 1


class HeliusClient:
    def __init__(self):
        self.api_key = get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        self.rate_limiter = RateLimiter(MAX_REQUESTS_PER_SECOND)

    def _rpc(self, method: str, params: list, retries: int = 3) -> Any:
        self.rate_limiter.acquire()
        payload = {"jsonrpc": "2.0", "id": 1,
                   "method": method, "params": params}
        data = json.dumps(payload).encode('utf-8')
        for attempt in range(retries):
            try:
                req = urllib.request.Request(self.rpc_url, data=data, headers={
                                             'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(req, timeout=30) as resp:
                    out = json.loads(resp.read().decode('utf-8'))
                    if 'error' in out:
                        raise Exception(out['error'])
                    return out.get('result')
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except Exception:
                if attempt < retries - 1:
                    time.sleep(0.5)
                else:
                    raise

    def get_signatures_for_address(self, address: str, limit: int = 1000, before: Optional[str] = None) -> List[Dict]:
        params = [address, {"limit": min(limit, 1000)}]
        if before:
            params[1]["before"] = before
        return self._rpc("getSignaturesForAddress", params)

    def get_transaction(self, signature: str) -> Optional[Dict]:
        return self._rpc("getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])


def run(limit: int = 5000, before: Optional[str] = None, workers: int = 20, output_path: Optional[str] = None):
    client = HeliusClient()
    failed_sig_infos = []
    all_sigs = []
    cursor = before

    print("Fetching Kamino signatures (including successful and failed)...")
    while len(all_sigs) < limit:
        batch = min(1000, limit - len(all_sigs))
        sigs = client.get_signatures_for_address(
            KAMINO_LENDING_PROGRAM_ID, limit=batch, before=cursor)
        if not sigs:
            break
        all_sigs.extend(sigs)
        for s in sigs:
            if s.get('err') is not None:
                failed_sig_infos.append(s)
        cursor = sigs[-1]['signature']
        print(f"  Fetched {len(all_sigs)} signatures, {len(failed_sig_infos)} failed")

    print(f"\nFailed transactions to check: {len(failed_sig_infos)}")
    if not failed_sig_infos:
        print("No failed transactions, exiting")
        return [], []

    failed_liquidate_signatures = []
    lock = threading.Lock()
    done = [0]

    def check_one(sig_info):
        sig = sig_info['signature']
        try:
            tx = client.get_transaction(sig)
            if tx and has_liquidation_instruction(tx):
                with lock:
                    failed_liquidate_signatures.append({
                        "signature": sig,
                        "slot": sig_info.get("slot"),
                        "blockTime": sig_info.get("blockTime"),
                        "err": sig_info.get("err"),
                    })
        except Exception:
            pass
        finally:
            with lock:
                done[0] += 1
            if done[0] % 50 == 0 or done[0] == len(failed_sig_infos):
                print(
                    f"  Progress: {done[0]}/{len(failed_sig_infos)}, failed liquidations found: {len(failed_liquidate_signatures)}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(check_one, info) for info in failed_sig_infos]
        for _ in as_completed(futures):
            pass

    signatures_only = [x["signature"] for x in failed_liquidate_signatures]
    print(f"\nFailed liquidation count: {len(signatures_only)}")
    for i, s in enumerate(signatures_only[:20]):
        print(f"  {i+1}. {s}")
    if len(signatures_only) > 20:
        print(f"  ... {len(signatures_only)} total")

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == '.json':
            with open(out, 'w', encoding='utf-8') as f:
                json.dump({"signatures": signatures_only,
                          "details": failed_liquidate_signatures}, f, indent=2, ensure_ascii=False)
        else:
            with open(out, 'w', encoding='utf-8') as f:
                for s in signatures_only:
                    f.write(s + "\n")
        print(f"Written to: {out}")

    return signatures_only, failed_liquidate_signatures


def main():
    import argparse
    p = argparse.ArgumentParser(description='Retrieve failed liquidation transaction signatures')
    p.add_argument('--limit', type=int, default=5000, help='Maximum number of signatures to fetch')
    p.add_argument('--before', type=str, default=None,
                   help='Cursor signature, fetch backwards (older history) from this signature')
    p.add_argument('--workers', type=int, default=20, help='Concurrency level')
    p.add_argument('-o', '--output', type=str, default=None,
                   help='Output file; .json saves details, otherwise one signature per line')
    args = p.parse_args()
    run(limit=args.limit, before=args.before,
        workers=args.workers, output_path=args.output)


if __name__ == "__main__":
    main()
