#!/usr/bin/env python3
"""
DefiLlama Cross-Chain Bridge Analysis Tool
Fetch major bridge protocol data between Solana and Ethereum,
including volume and transaction count.

Usage:
    pip install requests pandas tabulate
    python defillama_bridge_analyzer.py
"""

import requests
import json
from datetime import datetime
from typing import Optional, Dict, List, Any

# DefiLlama Bridges API
BRIDGES_API = "https://bridges.llama.fi"


def format_volume(vol: Optional[float]) -> str:
    """Format volume for display."""
    if vol is None or vol == 0:
        return "-"
    if vol >= 1_000_000_000:
        return f"${vol/1_000_000_000:.2f}B"
    elif vol >= 1_000_000:
        return f"${vol/1_000_000:.2f}M"
    elif vol >= 1_000:
        return f"${vol/1_000:.2f}K"
    else:
        return f"${vol:.2f}"


def format_txs(txs: Optional[int]) -> str:
    """Format transaction count for display."""
    if txs is None or txs == 0:
        return "-"
    if txs >= 1_000_000:
        return f"{txs/1_000_000:.2f}M"
    elif txs >= 1_000:
        return f"{txs/1_000:.1f}K"
    else:
        return str(txs)


def get_all_bridges() -> Optional[Dict]:
    """Get list of all bridges."""
    url = f"{BRIDGES_API}/bridges?includeChains=true"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"ERROR: Failed to fetch bridge list: {e}")
        return None


def get_bridge_details(bridge_id: int) -> Optional[Dict]:
    """Get detailed data for a specific bridge."""
    url = f"{BRIDGES_API}/bridge/{bridge_id}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  WARNING: Failed to fetch details for bridge {bridge_id}: {e}")
        return None


def get_bridge_volume_history(chain: str) -> Optional[List]:
    """Get bridge transaction history for a specific chain."""
    url = f"{BRIDGES_API}/bridgevolume/{chain}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"ERROR: Failed to fetch historical data for {chain}: {e}")
        return None


def extract_txs(txs_data: Dict) -> int:
    """Extract total transaction count from transaction data."""
    if not txs_data:
        return 0
    deposits = txs_data.get('deposits', 0) or 0
    withdrawals = txs_data.get('withdrawals', 0) or 0
    return deposits + withdrawals


def analyze_solana_eth_bridges():
    """Analyze cross-chain bridges supporting both Solana and Ethereum."""

    print("=" * 80)
    print("DefiLlama Cross-Chain Bridge Analysis: Solana <-> Ethereum")
    print(f"Data fetched at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # Fetch all bridges
    print("\nFetching bridge list...")
    data = get_all_bridges()
    if not data or 'bridges' not in data:
        print("ERROR: Unable to fetch bridge data; please check your network connection")
        return None

    bridges = data['bridges']
    print(f"OK: Found {len(bridges)} cross-chain bridge protocols")

    # Filter bridges supporting both Solana and Ethereum
    solana_eth_bridges = []
    for bridge in bridges:
        chains = [c.lower() for c in bridge.get('chains', [])]
        has_solana = any('solana' in c for c in chains)
        has_ethereum = any('ethereum' in c for c in chains)

        if has_solana and has_ethereum:
            solana_eth_bridges.append(bridge)

    print(f"Filtered {len(solana_eth_bridges)} bridges supporting both Solana and Ethereum")

    # Fetch detailed data for each bridge
    print("\nFetching detailed data for each bridge...")
    bridge_details = []

    for i, bridge in enumerate(solana_eth_bridges, 1):
        bridge_id = bridge.get('id')
        bridge_name = bridge.get('displayName', bridge.get('name', 'Unknown'))
        print(f"  [{i}/{len(solana_eth_bridges)}] Fetching {bridge_name}...")

        details = get_bridge_details(bridge_id)
        if not details:
            continue

        # Extract chain-level data
        chain_breakdown = details.get('chainBreakdown', {})
        solana_data = chain_breakdown.get('Solana', {})
        ethereum_data = chain_breakdown.get('Ethereum', {})

        bridge_info = {
            'name': bridge_name,
            'id': bridge_id,
            'chains': bridge.get('chains', []),
            'url': bridge.get('url', ''),

            # Overall data
            'last_24h_volume': details.get('lastDailyVolume') or 0,
            'weekly_volume': details.get('weeklyVolume') or 0,
            'monthly_volume': details.get('monthlyVolume') or 0,

            # Total transaction counts
            'daily_txs': extract_txs(details.get('prevDayTxs', {})),
            'weekly_txs': extract_txs(details.get('weeklyTxs', {})),
            'monthly_txs': extract_txs(details.get('monthlyTxs', {})),

            # Solana-side data
            'solana_24h_volume': solana_data.get('lastDailyVolume') or 0,
            'solana_weekly_volume': solana_data.get('weeklyVolume') or 0,
            'solana_monthly_volume': solana_data.get('monthlyVolume') or 0,
            'solana_weekly_txs': extract_txs(solana_data.get('weeklyTxs', {})),
            'solana_monthly_txs': extract_txs(solana_data.get('monthlyTxs', {})),

            # Ethereum-side data
            'eth_24h_volume': ethereum_data.get('lastDailyVolume') or 0,
            'eth_weekly_volume': ethereum_data.get('weeklyVolume') or 0,
            'eth_monthly_volume': ethereum_data.get('monthlyVolume') or 0,
            'eth_weekly_txs': extract_txs(ethereum_data.get('weeklyTxs', {})),
            'eth_monthly_txs': extract_txs(ethereum_data.get('monthlyTxs', {})),
        }
        bridge_details.append(bridge_info)

    if not bridge_details:
        print("ERROR: Failed to fetch detailed data for any bridge")
        return None

    # Sort by monthly volume
    bridge_details.sort(key=lambda x: x['monthly_volume'], reverse=True)

    # ==================== Print report ====================

    # 1. Overall ranking
    print("\n" + "=" * 100)
    print("Major cross-chain bridge protocol ranking (by monthly volume)")
    print("=" * 100)
    print(f"{'Rank':<4} {'Protocol':<22} {'24h Vol':<14} {'Wk Vol':<14} {'Mo Vol':<14} {'Day Txs':<10} {'Wk Txs':<10} {'Mo Txs':<10}")
    print("-" * 100)

    for i, bridge in enumerate(bridge_details[:15], 1):
        print(f"{i:<4} {bridge['name'][:20]:<22} "
              f"{format_volume(bridge['last_24h_volume']):<14} "
              f"{format_volume(bridge['weekly_volume']):<14} "
              f"{format_volume(bridge['monthly_volume']):<14} "
              f"{format_txs(bridge['daily_txs']):<10} "
              f"{format_txs(bridge['weekly_txs']):<10} "
              f"{format_txs(bridge['monthly_txs']):<10}")

    # 2. Solana-side data
    print("\n" + "=" * 100)
    print("Solana-side transaction data")
    print("=" * 100)
    solana_sorted = sorted(
        bridge_details, key=lambda x: x['solana_monthly_volume'], reverse=True)
    print(f"{'Rank':<4} {'Protocol':<22} {'Solana 24h Vol':<16} {'Solana Wk Vol':<16} {'Solana Mo Vol':<16} {'Wk Txs':<10} {'Mo Txs':<10}")
    print("-" * 100)

    for i, bridge in enumerate(solana_sorted[:15], 1):
        if bridge['solana_monthly_volume'] > 0 or bridge['solana_weekly_txs'] > 0:
            print(f"{i:<4} {bridge['name'][:20]:<22} "
                  f"{format_volume(bridge['solana_24h_volume']):<16} "
                  f"{format_volume(bridge['solana_weekly_volume']):<16} "
                  f"{format_volume(bridge['solana_monthly_volume']):<16} "
                  f"{format_txs(bridge['solana_weekly_txs']):<10} "
                  f"{format_txs(bridge['solana_monthly_txs']):<10}")

    # 3. Ethereum-side data
    print("\n" + "=" * 100)
    print("Ethereum-side transaction data")
    print("=" * 100)
    eth_sorted = sorted(
        bridge_details, key=lambda x: x['eth_monthly_volume'], reverse=True)
    print(f"{'Rank':<4} {'Protocol':<22} {'ETH 24h Vol':<16} {'ETH Wk Vol':<16} {'ETH Mo Vol':<16} {'Wk Txs':<10} {'Mo Txs':<10}")
    print("-" * 100)

    for i, bridge in enumerate(eth_sorted[:15], 1):
        if bridge['eth_monthly_volume'] > 0 or bridge['eth_weekly_txs'] > 0:
            print(f"{i:<4} {bridge['name'][:20]:<22} "
                  f"{format_volume(bridge['eth_24h_volume']):<16} "
                  f"{format_volume(bridge['eth_weekly_volume']):<16} "
                  f"{format_volume(bridge['eth_monthly_volume']):<16} "
                  f"{format_txs(bridge['eth_weekly_txs']):<10} "
                  f"{format_txs(bridge['eth_monthly_txs']):<10}")

    # 4. Summary statistics
    print("\n" + "=" * 100)
    print("Summary statistics")
    print("=" * 100)

    total_monthly_vol = sum(b['monthly_volume'] for b in bridge_details)
    total_weekly_vol = sum(b['weekly_volume'] for b in bridge_details)
    total_monthly_txs = sum(b['monthly_txs'] for b in bridge_details)
    total_weekly_txs = sum(b['weekly_txs'] for b in bridge_details)
    total_solana_monthly_vol = sum(
        b['solana_monthly_volume'] for b in bridge_details)
    total_eth_monthly_vol = sum(b['eth_monthly_volume']
                                for b in bridge_details)
    total_solana_monthly_txs = sum(b['solana_monthly_txs']
                                   for b in bridge_details)
    total_eth_monthly_txs = sum(b['eth_monthly_txs'] for b in bridge_details)

    print(f"  Total bridges (Solana<->ETH): {len(bridge_details)}")
    print(f"  Monthly total volume: {format_volume(total_monthly_vol)}")
    print(f"  Weekly total volume: {format_volume(total_weekly_vol)}")
    print(f"  Monthly total txs: {format_txs(total_monthly_txs)}")
    print(f"  Weekly total txs: {format_txs(total_weekly_txs)}")
    print(f"  Solana monthly volume: {format_volume(total_solana_monthly_vol)}")
    print(f"  Solana monthly txs: {format_txs(total_solana_monthly_txs)}")
    print(f"  Ethereum monthly volume: {format_volume(total_eth_monthly_vol)}")
    print(f"  Ethereum monthly txs: {format_txs(total_eth_monthly_txs)}")

    # 5. Export data
    output_data = {
        'timestamp': datetime.now().isoformat(),
        'summary': {
            'total_bridges': len(bridge_details),
            'total_monthly_volume_usd': total_monthly_vol,
            'total_weekly_volume_usd': total_weekly_vol,
            'total_monthly_txs': total_monthly_txs,
            'total_weekly_txs': total_weekly_txs,
            'solana_monthly_volume_usd': total_solana_monthly_vol,
            'solana_monthly_txs': total_solana_monthly_txs,
            'ethereum_monthly_volume_usd': total_eth_monthly_vol,
            'ethereum_monthly_txs': total_eth_monthly_txs,
        },
        'bridges': bridge_details
    }

    output_file = 'solana_eth_bridge_data.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\nDetailed data saved to: {output_file}")

    # Try generating CSV
    try:
        import pandas as pd
        df = pd.DataFrame(bridge_details)
        csv_file = 'solana_eth_bridge_data.csv'
        df.to_csv(csv_file, index=False)
        print(f"CSV data saved to: {csv_file}")
    except ImportError:
        print("Tip: Install pandas to generate CSV output (pip install pandas)")

    return bridge_details


def get_historical_volume(chain: str = "Solana", days: int = 30):
    """Get historical bridge volume for a given chain."""
    print(f"\nFetching {chain} bridge transaction history for the last {days} days...")

    history = get_bridge_volume_history(chain)
    if not history:
        return None

    # Take the most recent N days
    recent = history[-days:] if len(history) > days else history

    print(
        f"\n{'Date':<12} {'Deposit (USD)':<18} {'Withdraw (USD)':<18} {'Dep Txs':<12} {'Wd Txs':<12}")
    print("-" * 75)

    for day in recent[-10:]:  # only display the last 10 days
        date = datetime.fromtimestamp(int(day['date'])).strftime('%Y-%m-%d')
        deposit_vol = day.get('depositUSD', 0)
        withdraw_vol = day.get('withdrawUSD', 0)
        deposit_txs = day.get('depositTxs', 0)
        withdraw_txs = day.get('withdrawTxs', 0)

        print(f"{date:<12} {format_volume(deposit_vol):<18} {format_volume(withdraw_vol):<18} "
              f"{format_txs(deposit_txs):<12} {format_txs(withdraw_txs):<12}")

    return recent


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════════════╗
║                    DefiLlama Bridge Analysis Tool v1.0                         ║
║                    Solana ↔ Ethereum Bridge Analyzer                          ║
╚═══════════════════════════════════════════════════════════════════════════════╝
    """)

    # Main analysis
    bridges = analyze_solana_eth_bridges()

    # Fetch Solana historical data
    if bridges:
        print("\n" + "=" * 100)
        print("Solana bridge historical transaction data (last 10 days)")
        print("=" * 100)
        get_historical_volume("Solana", 30)

        print("\n" + "=" * 100)
        print("Ethereum bridge historical transaction data (last 10 days)")
        print("=" * 100)
        get_historical_volume("Ethereum", 30)

    print("\nAnalysis complete!")
