#!/usr/bin/env python3
"""
Step 2: Parse Kamino liquidation transaction

Features:
- Parse Kamino instructions using IDL
- Extract key liquidation-related information
- Parse liquidation data from logs

Usage:
    python 02_parse_transaction.py <input_json>
    python 02_parse_transaction.py ./data/5gcWCkHxdYKJCH_raw.json
"""

import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

# Import Kamino decoder
from kamino_decoder import KaminoDecoder, KAMINO_LENDING_PROGRAM_ID


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class TokenTransfer:
    """Token transfer record"""
    mint: str
    symbol: str
    amount: float
    decimals: int
    from_account: str
    to_account: str
    direction: str  # 'in' or 'out'


@dataclass
class BalanceChange:
    """Balance change"""
    account: str
    pre_balance: float
    post_balance: float
    change: float
    token_mint: Optional[str] = None
    token_symbol: Optional[str] = None


@dataclass
class LiquidationData:
    """Liquidation data"""
    # Basic information
    signature: str
    slot: int
    timestamp: int
    datetime_str: str
    success: bool

    # Liquidation parameters
    debt_amount: float          # Repaid debt amount
    debt_token: str             # Debt token (e.g. USDC)
    debt_decimals: int

    collateral_amount: float    # Collateral received amount
    collateral_token: str       # Collateral token (e.g. SOL)
    collateral_decimals: int

    # Fees
    transaction_fee: float      # Transaction fee (SOL)
    priority_fee: float         # Priority fee (SOL)
    protocol_fee: float         # Protocol fee (SOL)

    # Accounts
    liquidator: str             # Liquidator
    obligation_owner: str       # Liquidated party

    # Raw data
    raw_logs: List[str]
    kamino_instructions: List[Dict]


# ============================================================================
# Known Token Information
# ============================================================================

KNOWN_TOKENS = {
    "So11111111111111111111111111111111111111112": {
        "symbol": "SOL",
        "decimals": 9,
        "name": "Wrapped SOL"
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "symbol": "USDC",
        "decimals": 6,
        "name": "USD Coin"
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "symbol": "USDT",
        "decimals": 6,
        "name": "Tether USD"
    },
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {
        "symbol": "mSOL",
        "decimals": 9,
        "name": "Marinade staked SOL"
    },
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": {
        "symbol": "stSOL",
        "decimals": 9,
        "name": "Lido Staked SOL"
    },
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {
        "symbol": "JitoSOL",
        "decimals": 9,
        "name": "Jito Staked SOL"
    },
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": {
        "symbol": "bSOL",
        "decimals": 9,
        "name": "BlazeStake Staked SOL"
    },
}


def get_token_info(mint: str) -> Dict[str, Any]:
    """Get token information"""
    if mint in KNOWN_TOKENS:
        return KNOWN_TOKENS[mint]
    return {
        "symbol": mint[:8] + "...",
        "decimals": 9,  # Default assumption of 9 decimals
        "name": "Unknown Token"
    }


# ============================================================================
# Parser
# ============================================================================

class LiquidationParser:
    """Liquidation transaction parser"""

    def __init__(self, idl_path: Optional[str] = None):
        """
        Initialize the parser

        Args:
            idl_path: Kamino IDL file path
        """
        if idl_path is None:
            idl_path = str(Path(__file__).parent / "kamino_lending_idl.json")

        self.decoder = KaminoDecoder(idl_path)

    def parse_transaction(self, tx_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse transaction data

        Args:
            tx_data: Transaction data from Step 1

        Returns:
            Parsed transaction data
        """
        result = {
            'signature': tx_data.get('signature', ''),
            'slot': tx_data.get('slot', 0),
            'blockTime': tx_data.get('blockTime', 0),
            'datetime': tx_data.get('datetime', ''),
            'success': tx_data.get('success', False),
            'fee': tx_data.get('fee', 0),

            # Kamino related
            'is_kamino_tx': False,
            'is_liquidation': False,
            'kamino_instructions': [],

            # Balance changes
            'sol_balance_changes': [],
            'token_balance_changes': [],

            # Log parsing
            'parsed_logs': {},

            # Liquidation details (if it's a liquidation transaction)
            'liquidation_details': None
        }

        # Parse Kamino instructions
        instructions = tx_data.get('instructions', [])
        kamino_instructions = []

        for instr in instructions:
            if instr.get('programId') == KAMINO_LENDING_PROGRAM_ID:
                result['is_kamino_tx'] = True

                # Parse using decoder
                data = instr.get('data', '')
                accounts = instr.get('accounts', [])

                parsed = self.decoder.decode_instruction_data(data, accounts)

                if parsed:
                    kamino_instructions.append({
                        'name': parsed.name,
                        'type': parsed.instruction_type,
                        'discriminator': parsed.discriminator,
                        'args': parsed.args,
                        'accounts': parsed.accounts,
                    })

                    # Check if it's a liquidation instruction
                    if parsed.instruction_type == 'liquidation':
                        result['is_liquidation'] = True

        result['kamino_instructions'] = kamino_instructions

        # Parse SOL balance changes
        accounts = tx_data.get('accounts', [])
        pre_balances = tx_data.get('preBalances', [])
        post_balances = tx_data.get('postBalances', [])

        for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
            if pre != post:
                change = (post - pre) / 1e9  # Convert to SOL
                result['sol_balance_changes'].append({
                    'account': accounts[i] if i < len(accounts) else f'account_{i}',
                    'pre_balance': pre / 1e9,
                    'post_balance': post / 1e9,
                    'change': change
                })

        # Parse Token balance changes
        pre_token = tx_data.get('preTokenBalances', [])
        post_token = tx_data.get('postTokenBalances', [])

        token_changes = self._parse_token_balance_changes(
            pre_token, post_token, accounts
        )
        result['token_balance_changes'] = token_changes

        # Parse logs
        logs = tx_data.get('logMessages', [])
        result['parsed_logs'] = self._parse_logs(logs)
        result['raw_logs'] = logs

        # If it's a liquidation transaction, extract detailed information
        if result['is_liquidation']:
            result['liquidation_details'] = self._extract_liquidation_details(
                tx_data, result, kamino_instructions
            )

        return result

    def _parse_token_balance_changes(
        self,
        pre_token: List[Dict],
        post_token: List[Dict],
        accounts: List[str]
    ) -> List[Dict]:
        """Parse Token balance changes"""
        changes = []

        # Build pre and post mappings
        pre_map = {}
        for item in pre_token:
            acc_idx = item.get('accountIndex', -1)
            owner = item.get('owner', '')
            mint = item.get('mint', '')
            amount = item.get('uiTokenAmount', {})
            key = f"{acc_idx}_{owner}_{mint}"
            pre_map[key] = {
                'accountIndex': acc_idx,
                'owner': owner,
                'mint': mint,
                'amount': float(amount.get('uiAmount', 0) or 0),
                'decimals': amount.get('decimals', 0)
            }

        post_map = {}
        for item in post_token:
            acc_idx = item.get('accountIndex', -1)
            owner = item.get('owner', '')
            mint = item.get('mint', '')
            amount = item.get('uiTokenAmount', {})
            key = f"{acc_idx}_{owner}_{mint}"
            post_map[key] = {
                'accountIndex': acc_idx,
                'owner': owner,
                'mint': mint,
                'amount': float(amount.get('uiAmount', 0) or 0),
                'decimals': amount.get('decimals', 0)
            }

        # Calculate changes
        all_keys = set(pre_map.keys()) | set(post_map.keys())

        for key in all_keys:
            pre = pre_map.get(key, {'amount': 0, 'decimals': 0})
            post = post_map.get(key, {'amount': 0, 'decimals': 0})

            pre_amount = pre.get('amount', 0)
            post_amount = post.get('amount', 0)
            change = post_amount - pre_amount

            if abs(change) > 1e-10:  # Ignore extremely small changes
                mint = post.get('mint') or pre.get('mint', '')
                token_info = get_token_info(mint)

                changes.append({
                    'owner': post.get('owner') or pre.get('owner', ''),
                    'mint': mint,
                    'symbol': token_info['symbol'],
                    'pre_amount': pre_amount,
                    'post_amount': post_amount,
                    'change': change,
                    'decimals': post.get('decimals') or pre.get('decimals', 0)
                })

        return changes

    def _parse_logs(self, logs: List[str]) -> Dict[str, Any]:
        """Parse key information from logs"""
        result = {
            'program_invokes': [],
            'kamino_events': [],
            'errors': [],
            'liquidation_log': None
        }

        for log in logs:
            # Program invocations
            if 'invoke' in log.lower():
                result['program_invokes'].append(log)

            # Errors
            if 'error' in log.lower() or 'failed' in log.lower():
                result['errors'].append(log)

            # Kamino specific logs
            if 'Kamino' in log or 'kamino' in log:
                result['kamino_events'].append(log)

            # Liquidation related logs - look for numeric patterns
            if 'liquidat' in log.lower():
                result['liquidation_log'] = log

        return result

    def _extract_liquidation_details(
        self,
        tx_data: Dict[str, Any],
        parsed_result: Dict[str, Any],
        kamino_instructions: List[Dict]
    ) -> Dict[str, Any]:
        """Extract liquidation details"""
        details = {
            'liquidator': '',
            'obligation_owner': '',
            'debt_token': '',
            'debt_amount': 0.0,
            'debt_decimals': 6,
            'collateral_token': '',
            'total_collateral': 0.0,        # Total collateral (before protocol fee deduction)
            'received_collateral': 0.0,      # Collateral actually received by liquidator
            'collateral_amount': 0.0,        # Collateral used for calculation (= total collateral)
            'collateral_decimals': 9,
            'protocol_fee': 0.0,
            'protocol_fee_deducted': False,  # Whether protocol fee has been deducted from received_collateral
            'transaction_fee': tx_data.get('fee', 0) / 1e9,
            'priority_fee': 0.0
        }

        # Extract from Token balance changes
        token_changes = parsed_result.get('token_balance_changes', [])

        # First find the liquidator (who paid USDC/USDT)
        for change in token_changes:
            if change['change'] < 0 and change['symbol'] in ['USDC', 'USDT']:
                details['debt_token'] = change['symbol']
                details['debt_amount'] = abs(change['change'])
                details['debt_decimals'] = change['decimals']
                details['liquidator'] = change['owner']
                break

        # Find the liquidated party (SOL-type token decreasing, not the liquidator)
        for change in token_changes:
            if change['change'] < -0.01 and change['symbol'] in ['SOL', 'mSOL', 'stSOL', 'JitoSOL', 'bSOL']:
                if change['owner'] != details['liquidator']:
                    details['obligation_owner'] = change['owner']
                    details['total_collateral'] = abs(change['change'])
                    details['collateral_token'] = change['symbol']
                    break

        # Find collateral received by the liquidator
        if details['liquidator']:
            for change in token_changes:
                if change['owner'] == details['liquidator'] and change['change'] > 0:
                    if change['symbol'] in ['SOL', 'mSOL', 'stSOL', 'JitoSOL', 'bSOL']:
                        details['received_collateral'] = change['change']
                        if not details['collateral_token']:
                            details['collateral_token'] = change['symbol']
                        break

        # Find protocol fee (small SOL amount received by liquidated party)
        if details['obligation_owner']:
            for change in token_changes:
                if change['owner'] == details['obligation_owner']:
                    if 0 < change['change'] < 0.01 and change['symbol'] in ['SOL', 'mSOL', 'stSOL', 'JitoSOL', 'bSOL']:
                        details['protocol_fee'] = change['change']
                        break

        # Verify whether protocol fee has been deducted from received_collateral
        # If received_collateral + protocol_fee ~= total_collateral, then it has been deducted
        if details['total_collateral'] > 0 and details['protocol_fee'] > 0:
            if abs(details['received_collateral'] + details['protocol_fee'] - details['total_collateral']) < 0.0001:
                details['protocol_fee_deducted'] = True

        # Collateral used for calculation = total collateral (so protocol fee is only deducted once)
        details['collateral_amount'] = details['total_collateral'] if details['total_collateral'] > 0 else details['received_collateral']

        # Calculate priority fee
        base_fee = 5000 / 1e9
        total_fee = details['transaction_fee']
        details['priority_fee'] = max(0, total_fee - base_fee)

        return details


# ============================================================================
# Main function
# ============================================================================

def parse_transaction(
    input_file: str,
    output_file: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Parse transaction

    Args:
        input_file: Input JSON file (output from Step 1)
        output_file: Output file path (optional)
        verbose: Whether to print detailed information

    Returns:
        Parsed data
    """
    if verbose:
        print("=" * 70)
        print("Kamino Liquidation Analysis - Step 2: Parse Transaction")
        print("=" * 70)

    # Load transaction data
    with open(input_file, 'r', encoding='utf-8') as f:
        tx_data = json.load(f)

    if verbose:
        print(f"\nInput file: {input_file}")
        print(f"Transaction signature: {tx_data.get('signature', '')[:32]}...")

    # Create parser
    parser = LiquidationParser()

    # Parse transaction
    result = parser.parse_transaction(tx_data)

    if verbose:
        print(f"\nParse results:")
        print(f"  Is Kamino transaction: {result['is_kamino_tx']}")
        print(f"  Is liquidation transaction: {result['is_liquidation']}")
        print(f"  Kamino instruction count: {len(result['kamino_instructions'])}")

        if result['kamino_instructions']:
            print(f"\n  Kamino instructions:")
            for instr in result['kamino_instructions']:
                print(f"    - {instr['name']} ({instr['type']})")

        if result['sol_balance_changes']:
            print(f"\n  SOL balance changes:")
            for change in result['sol_balance_changes'][:5]:
                direction = "+" if change['change'] > 0 else ""
                print(
                    f"    {change['account'][:16]}... : {direction}{change['change']:.9f} SOL")

        if result['token_balance_changes']:
            print(f"\n  Token balance changes:")
            for change in result['token_balance_changes']:
                direction = "+" if change['change'] > 0 else ""
                print(
                    f"    {change['owner'][:16]}... : {direction}{change['change']:.6f} {change['symbol']}")

        if result['liquidation_details']:
            details = result['liquidation_details']
            print(f"\n  Liquidation details:")
            print(f"    Liquidator: {details['liquidator'][:32]}...")
            print(
                f"    Repaid debt: {details['debt_amount']:.6f} {details['debt_token']}")
            print(
                f"    Collateral received: {details['collateral_amount']:.9f} {details['collateral_token']}")
            print(f"    Transaction fee: {details['transaction_fee']:.9f} SOL")
            print(f"    Priority fee: {details['priority_fee']:.9f} SOL")

    # Save result
    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        if verbose:
            print(f"\nSaved to: {output_file}")

    return result


def main():
    """Command line entry point"""
    if len(sys.argv) < 2:
        print("Usage: python 02_parse_transaction.py <input_json> [output_json]")
        print("\nExamples:")
        print("  python 02_parse_transaction.py ./data/5gcWCkHxdYKJCH_raw.json")
        print("  python 02_parse_transaction.py ./data/tx_raw.json ./data/tx_parsed.json")
        sys.exit(1)

    input_file = sys.argv[1]

    # Generate default output file name
    input_path = Path(input_file)
    default_output = input_path.parent / f"{input_path.stem}_parsed.json"
    output_file = sys.argv[2] if len(sys.argv) > 2 else str(default_output)

    try:
        result = parse_transaction(input_file, output_file)
        print(f"\n{'=' * 70}")
        print("Step 2 complete! You can proceed to Step 3 to calculate profit")
        print(f"  python 03_calculate_profit.py {output_file}")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
