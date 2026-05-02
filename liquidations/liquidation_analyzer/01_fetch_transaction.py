#!/usr/bin/env python3
"""
Step 1: Fetch Solana transaction data using Helius API

Features:
- Fetch full transaction details via Helius RPC
- Support fetching token balance changes of transactions
- Save transaction data as JSON file

Usage:
    python 01_fetch_transaction.py <signature>
    python 01_fetch_transaction.py 5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any


# ============================================================================
# Configuration
# ============================================================================

def load_env():
    """Load environment variables from .env file"""
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()


def get_helius_api_key() -> str:
    """Get Helius API Key"""
    load_env()
    api_key = os.environ.get('HELIUS_API_KEY')
    if not api_key:
        raise ValueError("HELIUS_API_KEY not found, please configure it in the .env file")
    return api_key


# ============================================================================
# Helius API Client
# ============================================================================

class HeliusClient:
    """Helius API Client"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    def _make_rpc_request(self, method: str, params: list) -> Dict[str, Any]:
        """Send RPC request"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }

        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}

        request = urllib.request.Request(
            self.rpc_url,
            data=data,
            headers=headers,
            method='POST'
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))

                if 'error' in result:
                    raise Exception(f"RPC error: {result['error']}")

                return result.get('result')

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8') if e.fp else ''
            raise Exception(f"HTTP error {e.code}: {error_body}")

        except urllib.error.URLError as e:
            raise Exception(f"Network error: {e.reason}")

    def get_transaction(
        self,
        signature: str,
        encoding: str = "jsonParsed",
        max_supported_version: int = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Get transaction details

        Args:
            signature: Transaction signature
            encoding: Encoding format (json, jsonParsed, base58, base64)
            max_supported_version: Maximum supported version

        Returns:
            Transaction data dictionary
        """
        params = [
            signature,
            {
                "encoding": encoding,
                "maxSupportedTransactionVersion": max_supported_version
            }
        ]

        return self._make_rpc_request("getTransaction", params)

    def get_parsed_transaction(self, signature: str) -> Optional[Dict[str, Any]]:
        """
        Get parsed transaction data (with all detailed information)

        Args:
            signature: Transaction signature

        Returns:
            Dictionary containing full transaction information
        """
        tx = self.get_transaction(signature, encoding="jsonParsed")

        if not tx:
            return None

        # Extract key information
        meta = tx.get('meta', {})
        transaction = tx.get('transaction', {})
        message = transaction.get('message', {})

        # Account list
        account_keys = message.get('accountKeys', [])
        accounts = []
        for acc in account_keys:
            if isinstance(acc, dict):
                accounts.append(acc.get('pubkey', ''))
            else:
                accounts.append(acc)

        # Extract instructions
        instructions = message.get('instructions', [])
        inner_instructions = meta.get('innerInstructions', [])

        # Process instruction data
        parsed_instructions = []
        for idx, instr in enumerate(instructions):
            parsed_instr = self._parse_instruction(instr, accounts)
            parsed_instr['index'] = idx
            parsed_instructions.append(parsed_instr)

            # Add inner instructions
            for inner in inner_instructions:
                if inner.get('index') == idx:
                    for inner_instr in inner.get('instructions', []):
                        parsed_inner = self._parse_instruction(
                            inner_instr, accounts)
                        parsed_inner['is_inner'] = True
                        parsed_inner['parent_index'] = idx
                        parsed_instructions.append(parsed_inner)

        # Build result
        result = {
            'signature': signature,
            'slot': tx.get('slot', 0),
            'blockTime': tx.get('blockTime', 0),
            'datetime': '',
            'success': not meta.get('err'),
            'fee': meta.get('fee', 0),
            'accounts': accounts,
            'instructions': parsed_instructions,
            'preBalances': meta.get('preBalances', []),
            'postBalances': meta.get('postBalances', []),
            'preTokenBalances': meta.get('preTokenBalances', []),
            'postTokenBalances': meta.get('postTokenBalances', []),
            'logMessages': meta.get('logMessages', []),
            'raw': tx
        }

        # Convert timestamp
        if result['blockTime']:
            result['datetime'] = datetime.fromtimestamp(
                result['blockTime']
            ).isoformat()

        return result

    def _parse_instruction(
        self,
        instr: Dict[str, Any],
        accounts: list
    ) -> Dict[str, Any]:
        """Parse a single instruction"""
        result = {
            'programId': '',
            'data': '',
            'accounts': [],
            'parsed': None
        }

        # Get programId
        if 'programId' in instr:
            result['programId'] = instr['programId']
        elif 'programIdIndex' in instr and instr['programIdIndex'] >= 0:
            idx = instr['programIdIndex']
            if idx < len(accounts):
                result['programId'] = accounts[idx]

        # Get data
        if 'data' in instr:
            result['data'] = instr['data']

        # Get accounts
        if 'accounts' in instr:
            acc_indices = instr['accounts']
            for idx in acc_indices:
                if isinstance(idx, int) and idx < len(accounts):
                    result['accounts'].append(accounts[idx])
                elif isinstance(idx, str):
                    result['accounts'].append(idx)

        # Get parsed data
        if 'parsed' in instr:
            result['parsed'] = instr['parsed']

        return result


# ============================================================================
# Main function
# ============================================================================

def fetch_transaction(
    signature: str,
    output_file: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Fetch transaction data

    Args:
        signature: Transaction signature
        output_file: Output file path (optional)
        verbose: Whether to print detailed information

    Returns:
        Transaction data dictionary
    """
    if verbose:
        print("=" * 70)
        print("Kamino Liquidation Analysis - Step 1: Fetch Transaction Data")
        print("=" * 70)
        print(f"\nTransaction signature: {signature}")

    # Create client
    client = HeliusClient()

    if verbose:
        print("\nFetching transaction data...")

    # Fetch transaction
    tx_data = client.get_parsed_transaction(signature)

    if not tx_data:
        raise Exception(f"Unable to fetch transaction: {signature}")

    if verbose:
        print(f"Successfully fetched transaction")
        print(f"  Slot: {tx_data['slot']}")
        print(f"  Time: {tx_data['datetime']}")
        print(f"  Success: {tx_data['success']}")
        print(f"  Fee: {tx_data['fee'] / 1e9:.9f} SOL")
        print(f"  Instruction count: {len(tx_data['instructions'])}")

        # Show involved programs
        programs = set()
        for instr in tx_data['instructions']:
            programs.add(instr['programId'])

        print(f"\nInvolved programs:")
        for prog in programs:
            label = ""
            if prog == "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD":
                label = " [Kamino Lending]"
            elif prog == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                label = " [Token Program]"
            elif prog == "ComputeBudget111111111111111111111111111111":
                label = " [Compute Budget]"
            elif prog == "11111111111111111111111111111111":
                label = " [System Program]"
            print(f"  - {prog}{label}")

    # Save to file
    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(tx_data, f, indent=2, ensure_ascii=False)

        if verbose:
            print(f"\nSaved to: {output_file}")

    return tx_data


def main():
    """Command line entry point"""
    if len(sys.argv) < 2:
        print("Usage: python 01_fetch_transaction.py <signature> [output_file]")
        print("\nExamples:")
        print("  python 01_fetch_transaction.py 5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg")
        print("  python 01_fetch_transaction.py <sig> ./data/transaction.json")
        sys.exit(1)

    signature = sys.argv[1]
    output_file = sys.argv[2] if len(
        sys.argv) > 2 else f"./data/{signature[:16]}_raw.json"

    try:
        tx_data = fetch_transaction(signature, output_file)
        print(f"\n{'=' * 70}")
        print("Step 1 complete! You can proceed to Step 2 to parse the transaction")
        print(f"  python 02_parse_transaction.py {output_file}")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
