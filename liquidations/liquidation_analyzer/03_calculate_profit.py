#!/usr/bin/env python3
"""
Step 3: Calculate Kamino liquidation profit

Features:
- Calculate liquidation profit based on parsed transaction data
- Support manual SOL price input or automatic fetching
- Output detailed profit breakdown

Usage:
    python 03_calculate_profit.py <parsed_json> [--sol-price <price>]
    python 03_calculate_profit.py ./data/tx_parsed.json --sol-price 204.92

Profit formula:
    Net profit = (Liquidation proceeds SOL - Protocol fee - Priority fee) x SOL price - Repaid debt USDC
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict
import argparse


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class ProfitCalculation:
    """Profit calculation result"""
    # Input data
    signature: str
    timestamp: str
    sol_price: float

    # Liquidation data
    debt_amount: float          # Repaid debt (USDC)
    debt_token: str
    collateral_amount: float    # Collateral received (SOL)
    collateral_token: str
    protocol_fee: float         # Protocol fee (SOL)
    priority_fee: float         # Priority fee (SOL)

    # Calculation process
    net_collateral: float       # Net collateral = collateral - protocol fee - priority fee
    collateral_value_usd: float  # Net collateral value (USD)

    # Final result
    net_profit_usd: float       # Net profit (USD)
    profit_rate: float          # Profit rate (%)
    is_profitable: bool


# ============================================================================
# Price Fetching
# ============================================================================

def get_sol_price_coingecko() -> Optional[float]:
    """
    Get current SOL price from CoinGecko

    Returns:
        SOL price (USD) or None
    """
    url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"

    try:
        request = urllib.request.Request(
            url,
            headers={'Accept': 'application/json'}
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data.get('solana', {}).get('usd')

    except Exception as e:
        print(f"Failed to fetch price: {e}")
        return None


def get_sol_price_binance() -> Optional[float]:
    """
    Get current SOL price from Binance

    Returns:
        SOL price (USD) or None
    """
    url = "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT"

    try:
        request = urllib.request.Request(
            url,
            headers={'Accept': 'application/json'}
        )

        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return float(data.get('price', 0))

    except Exception as e:
        print(f"Failed to fetch price: {e}")
        return None


def get_sol_price() -> Optional[float]:
    """
    Get current SOL price (tries multiple data sources)

    Returns:
        SOL price (USD) or None
    """
    # Try Binance first (faster)
    price = get_sol_price_binance()
    if price:
        return price

    # Then try CoinGecko
    price = get_sol_price_coingecko()
    if price:
        return price

    return None


# ============================================================================
# Profit Calculation
# ============================================================================

class ProfitCalculator:
    """Liquidation profit calculator"""

    def __init__(self, sol_price: Optional[float] = None):
        """
        Initialize calculator

        Args:
            sol_price: SOL price (USD), if None then automatically fetched
        """
        self.sol_price = sol_price

    def calculate(
        self,
        parsed_data: Dict[str, Any],
        sol_price: Optional[float] = None,
        protocol_fee: Optional[float] = None
    ) -> ProfitCalculation:
        """
        Calculate liquidation profit

        Args:
            parsed_data: Parsed transaction data (output from Step 2)
            sol_price: SOL price (optional, overrides initialization price)
            protocol_fee: Protocol fee (optional, overrides value parsed from transaction)

        Returns:
            ProfitCalculation object
        """
        # Determine SOL price
        price = sol_price or self.sol_price
        if price is None:
            print("Fetching current SOL price...")
            price = get_sol_price()
            if price is None:
                raise ValueError("Unable to fetch SOL price, please specify --sol-price manually")
            print(f"Current SOL price: ${price:.2f}")

        # Extract liquidation details
        details = parsed_data.get('liquidation_details', {})
        if not details:
            raise ValueError("Liquidation details not found, please ensure the transaction was correctly parsed")

        # Get basic data
        debt_amount = details.get('debt_amount', 0)
        debt_token = details.get('debt_token', 'USDC')

        # Use collateral_amount (= total_collateral) for calculation
        # This way protocol fee is only deducted once
        collateral_amount = details.get('collateral_amount', 0)
        if collateral_amount == 0:
            collateral_amount = details.get('total_collateral', 0)
        if collateral_amount == 0:
            collateral_amount = details.get('received_collateral', 0)

        collateral_token = details.get('collateral_token', 'SOL')

        # Fees
        tx_fee = details.get('transaction_fee', 0)
        priority_fee = details.get('priority_fee', 0)

        # Protocol fee: use manual value if specified, otherwise extract from transaction
        if protocol_fee is not None:
            prot_fee = protocol_fee
        else:
            prot_fee = details.get('protocol_fee', 0)

            # If protocol fee not parsed, estimate from Token changes
            if prot_fee == 0:
                prot_fee = self._estimate_protocol_fee(parsed_data)

        # ============================================
        # Core calculation formula
        # ============================================
        # Net profit = (Net SOL x SOL price) - Repaid debt USDC
        # Net SOL = Liquidation proceeds - Protocol fee - Priority fee
        # ============================================

        # Step 1: Calculate net collateral
        net_collateral = collateral_amount - prot_fee - priority_fee

        # Step 2: Convert to USD
        collateral_value_usd = net_collateral * price

        # Step 3: Calculate net profit
        net_profit_usd = collateral_value_usd - debt_amount

        # Calculate profit rate
        profit_rate = (net_profit_usd / debt_amount *
                       100) if debt_amount > 0 else 0

        return ProfitCalculation(
            signature=parsed_data.get('signature', ''),
            timestamp=parsed_data.get('datetime', ''),
            sol_price=price,

            debt_amount=debt_amount,
            debt_token=debt_token,
            collateral_amount=collateral_amount,
            collateral_token=collateral_token,
            protocol_fee=prot_fee,
            priority_fee=priority_fee,

            net_collateral=net_collateral,
            collateral_value_usd=collateral_value_usd,

            net_profit_usd=net_profit_usd,
            profit_rate=profit_rate,
            is_profitable=net_profit_usd > 0
        )

    def _estimate_protocol_fee(self, parsed_data: Dict[str, Any]) -> float:
        """
        Estimate protocol fee from transaction data

        Typically the protocol fee appears as a separate SOL transfer or Token transfer
        """
        # Look for possible protocol fee accounts (usually Kamino's fee vault)
        sol_changes = parsed_data.get('sol_balance_changes', [])

        # Find small positive SOL changes (likely protocol fee)
        for change in sol_changes:
            # Protocol fee is typically a small positive change to a specific account
            if 0 < change['change'] < 0.01:  # Assume protocol fee is less than 0.01 SOL
                # Could further check if account is a known fee vault
                return change['change']

        return 0

    def calculate_from_raw_values(
        self,
        collateral_amount: float,
        protocol_fee: float,
        priority_fee: float,
        debt_amount: float,
        sol_price: float
    ) -> ProfitCalculation:
        """
        Calculate profit directly from raw values

        Args:
            collateral_amount: Collateral received (SOL)
            protocol_fee: Protocol fee (SOL)
            priority_fee: Priority fee (SOL)
            debt_amount: Repaid debt (USDC)
            sol_price: SOL price (USD)

        Returns:
            ProfitCalculation object
        """
        # Step 1: Calculate net collateral
        net_collateral = collateral_amount - protocol_fee - priority_fee

        # Step 2: Convert to USD
        collateral_value_usd = net_collateral * sol_price

        # Step 3: Calculate net profit
        net_profit_usd = collateral_value_usd - debt_amount

        # Profit rate
        profit_rate = (net_profit_usd / debt_amount *
                       100) if debt_amount > 0 else 0

        return ProfitCalculation(
            signature='manual_calculation',
            timestamp=datetime.now().isoformat(),
            sol_price=sol_price,

            debt_amount=debt_amount,
            debt_token='USDC',
            collateral_amount=collateral_amount,
            collateral_token='SOL',
            protocol_fee=protocol_fee,
            priority_fee=priority_fee,

            net_collateral=net_collateral,
            collateral_value_usd=collateral_value_usd,

            net_profit_usd=net_profit_usd,
            profit_rate=profit_rate,
            is_profitable=net_profit_usd > 0
        )


def print_profit_report(calc: ProfitCalculation) -> None:
    """Print profit report"""
    print("\n" + "=" * 70)
    print("Kamino Liquidation Profit Calculation Report")
    print("=" * 70)

    print(f"""
Basic Information
   Transaction signature: {calc.signature[:48]}{'...' if len(calc.signature) > 48 else ''}
   Transaction time: {calc.timestamp}
   SOL price: ${calc.sol_price:.2f}

Calculation Formula
   Net profit = (Net SOL x SOL price) - Repaid debt USDC
   Net SOL    = Liquidation proceeds - Protocol fee - Priority fee

Step 1: Calculate net SOL
   Liquidation proceeds:  {calc.collateral_amount:.9f} {calc.collateral_token}
   - Protocol fee:        {calc.protocol_fee:.9f} {calc.collateral_token}
   - Priority fee:        {calc.priority_fee:.9f} {calc.collateral_token}
   ---------------------------------
   Net SOL =              {calc.net_collateral:.9f} {calc.collateral_token}

Step 2: Convert to USD
   {calc.net_collateral:.9f} {calc.collateral_token} x ${calc.sol_price:.2f} = ${calc.collateral_value_usd:.4f}

Step 3: Calculate net profit
   Total revenue (USD):    ${calc.collateral_value_usd:.4f}
   - Repaid debt:          ${calc.debt_amount:.6f} {calc.debt_token}
   ---------------------------------""")

    # Profit result
    if calc.is_profitable:
        print(
            f"""   Net profit =          ${calc.net_profit_usd:.4f} USD  (+{calc.profit_rate:.4f}%)""")
    else:
        print(
            f"""   Net loss =            ${abs(calc.net_profit_usd):.4f} USD  ({calc.profit_rate:.4f}%)""")

    print("\n" + "=" * 70)


# ============================================================================
# Main function
# ============================================================================

def calculate_profit(
    input_file: str,
    sol_price: Optional[float] = None,
    protocol_fee: Optional[float] = None,
    output_file: Optional[str] = None,
    verbose: bool = True
) -> ProfitCalculation:
    """
    Calculate liquidation profit

    Args:
        input_file: Input parsed JSON file (output from Step 2)
        sol_price: SOL price (optional)
        protocol_fee: Protocol fee (optional)
        output_file: Output file path (optional)
        verbose: Whether to print detailed information

    Returns:
        ProfitCalculation object
    """
    if verbose:
        print("=" * 70)
        print("Kamino Liquidation Analysis - Step 3: Calculate Profit")
        print("=" * 70)

    # Load parsed data
    with open(input_file, 'r', encoding='utf-8') as f:
        parsed_data = json.load(f)

    if verbose:
        print(f"\nInput file: {input_file}")

    # Check if it's a liquidation transaction
    if not parsed_data.get('is_liquidation'):
        print("Warning: This transaction may not be a liquidation transaction")

    # Create calculator
    calculator = ProfitCalculator(sol_price)

    # Calculate profit
    result = calculator.calculate(
        parsed_data,
        sol_price=sol_price,
        protocol_fee=protocol_fee
    )

    # Print report
    if verbose:
        print_profit_report(result)

    # Save result
    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(asdict(result), f, indent=2, ensure_ascii=False)

        if verbose:
            print(f"Saved to: {output_file}")

    return result


def main():
    """Command line entry point"""
    parser = argparse.ArgumentParser(
        description='Kamino Liquidation Profit Calculator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python 03_calculate_profit.py ./data/tx_parsed.json
  python 03_calculate_profit.py ./data/tx_parsed.json --sol-price 204.92
  python 03_calculate_profit.py ./data/tx_parsed.json --sol-price 204.92 --protocol-fee 0.0013

Profit formula:
  Net profit = (Liquidation proceeds - Protocol fee - Priority fee) x SOL price - Repaid debt
        """
    )

    parser.add_argument(
        'input_file',
        help='Parsed transaction JSON file (output from Step 2)'
    )
    parser.add_argument(
        '--sol-price',
        type=float,
        help='SOL price (USD), if not specified the current price will be fetched automatically'
    )
    parser.add_argument(
        '--protocol-fee',
        type=float,
        help='Protocol fee (SOL), if not specified it will be parsed from the transaction'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file path'
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Quiet mode, only output result'
    )

    args = parser.parse_args()

    # Generate default output file name
    if not args.output:
        input_path = Path(args.input_file)
        args.output = str(input_path.parent / f"{input_path.stem}_profit.json")

    try:
        result = calculate_profit(
            args.input_file,
            sol_price=args.sol_price,
            protocol_fee=args.protocol_fee,
            output_file=args.output,
            verbose=not args.quiet
        )

        if args.quiet:
            # Quiet mode only outputs final profit
            print(f"Net profit: ${result.net_profit_usd:.4f} USD")
        else:
            print(f"\nStep 3 complete! Liquidation analysis flow finished.")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
