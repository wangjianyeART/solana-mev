"""
Main Program - Solana Lending Protocol Liquidation MEV Monitor
"""
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any
from tabulate import tabulate
import config
from helius_client import HeliusClient
from rpc_fallback import RPCFallbackClient
from mev_calculator import MEVCalculator


async def fetch_liquidations_helius(hours: int = 1) -> Dict[str, List[Any]]:
    """
    Fetch liquidation data using the Helius API

    Args:
        hours: Time range (in hours)

    Returns:
        Liquidation events grouped by protocol
    """
    if not config.HELIUS_API_KEY:
        print("Error: HELIUS_API_KEY environment variable is not set")
        print("Please create a .env file in the project root directory and add:")
        print("HELIUS_API_KEY=your_api_key_here")
        return {}

    async with HeliusClient(config.HELIUS_API_KEY) as client:
        results = await client.get_all_liquidations(hours)
        return results


async def fetch_liquidations_rpc(hours: int = 1) -> Dict[str, List[Any]]:
    """
    Fetch liquidation data using the RPC fallback

    Args:
        hours: Time range (in hours)

    Returns:
        Liquidation events grouped by protocol
    """
    results = {}

    async with RPCFallbackClient(config.SOLANA_RPC_URL) as client:
        for protocol_name, protocol_address in config.LENDING_PROTOCOLS.items():
            liquidations = await client.get_liquidations_for_protocol(
                protocol_name, protocol_address, hours
            )
            results[protocol_name] = liquidations

    return results


async def calculate_mev(liquidations: Dict[str, List[Any]]) -> Dict[str, List[Dict]]:
    """
    Calculate MEV profit for all liquidation events

    Args:
        liquidations: Liquidation events (grouped by protocol)

    Returns:
        Liquidation events with MEV data
    """
    enriched_results = {}

    async with MEVCalculator() as calculator:
        for protocol, events in liquidations.items():
            if events:
                print(f"\nCalculating MEV profit for {protocol}...")
                enriched = await calculator.enrich_liquidation_events(events)
                enriched_results[protocol] = enriched
            else:
                enriched_results[protocol] = []

    return enriched_results


def format_address(address: str, length: int = 8) -> str:
    """Format address for display"""
    if not address or address == "Unknown":
        return "Unknown"
    if len(address) <= length:
        return address
    return f"{address[:length//2]}...{address[-length//2:]}"


def format_token(mint: str, known_tokens: Dict[str, str] = None) -> str:
    """Format token for display"""
    if not mint or mint == "Unknown":
        return "Unknown"

    # Common token mapping
    common_tokens = {
        "So11111111111111111111111111111111111111112": "SOL",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
        "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": "stSOL",
    }

    if known_tokens:
        common_tokens.update(known_tokens)

    return common_tokens.get(mint, format_address(mint, 6))


def print_results_table(enriched_data: Dict[str, List[Dict]]):
    """Print results in table format"""
    print("\n" + "=" * 150)
    print("Solana Lending Protocol Liquidation MEV Monitor Report")
    print("=" * 150)

    total_liquidations = sum(len(events) for events in enriched_data.values())
    print(f"\nTotal: {total_liquidations} liquidation transactions found\n")

    for protocol, events in enriched_data.items():
        if not events:
            print(f"\n{protocol}: No liquidations found")
            continue

        print(f"\n{protocol}: {len(events)} liquidations")
        print("-" * 150)

        # Prepare table data
        table_data = []
        for event in events:
            row = [
                event.get("timestamp", "")[:19] if isinstance(event.get("timestamp"), str) else "",
                format_address(event.get("tx_signature", "")),
                format_address(event.get("liquidator", "")),
                f"{event.get('debt_amount', 0):.2f} {format_token(event.get('debt_token', ''))}",
                f"{event.get('collateral_amount', 0):.4f} {format_token(event.get('collateral_token', ''))}",
                f"${event.get('gross_profit_usd', 0):.2f}",
                f"{event.get('roi_percent', 0):.2f}%",
                f"${event.get('tx_fee_usd', 0):.4f}",
            ]
            table_data.append(row)

        headers = [
            "Time",
            "Tx Hash",
            "Liquidator",
            "Debt Repaid",
            "Collateral Received",
            "MEV Profit",
            "ROI",
            "Gas Fee",
        ]

        print(tabulate(table_data, headers=headers, tablefmt="grid"))

        # Statistics
        total_profit = sum(e.get("gross_profit_usd", 0) for e in events)
        avg_profit = total_profit / len(events) if events else 0
        avg_roi = sum(e.get("roi_percent", 0) for e in events) / len(events) if events else 0

        print(f"\n{protocol} Statistics:")
        print(f"  Total MEV Profit: ${total_profit:.2f}")
        print(f"  Average MEV Profit: ${avg_profit:.2f}")
        print(f"  Average ROI: {avg_roi:.2f}%")


def save_results_json(enriched_data: Dict[str, List[Dict]], filename: str = None):
    """Save results as a JSON file"""
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"liquidations_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(enriched_data, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to: {filename}")


async def main(hours: int = 1, use_rpc: bool = False, save_json: bool = True):
    """
    Main function

    Args:
        hours: Query time range (in hours)
        use_rpc: Whether to use the RPC fallback
        save_json: Whether to save JSON file
    """
    print("=" * 150)
    print("Solana Lending Protocol Liquidation MEV Monitor")
    print("=" * 150)
    print(f"\nQuery range: past {hours} hours")
    print(f"Monitored protocols: {', '.join(config.LENDING_PROTOCOLS.keys())}")
    print(f"Data source: {'RPC Fallback' if use_rpc else 'Helius API'}")
    print()

    # Fetch liquidation data
    if use_rpc:
        liquidations = await fetch_liquidations_rpc(hours)
    else:
        liquidations = await fetch_liquidations_helius(hours)

    # Calculate MEV
    enriched_data = await calculate_mev(liquidations)

    # Display results
    print_results_table(enriched_data)

    # Save JSON
    if save_json:
        save_results_json(enriched_data)

    print("\n" + "=" * 150)
    print("Done!")
    print("=" * 150)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Solana Lending Protocol Liquidation MEV Monitor")
    parser.add_argument(
        "--hours",
        type=int,
        default=1,
        help="Query time range (in hours), default 1 hour",
    )
    parser.add_argument(
        "--rpc",
        action="store_true",
        help="Use RPC fallback (instead of Helius API)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save JSON file",
    )

    args = parser.parse_args()

    asyncio.run(main(hours=args.hours, use_rpc=args.rpc, save_json=not args.no_save))
