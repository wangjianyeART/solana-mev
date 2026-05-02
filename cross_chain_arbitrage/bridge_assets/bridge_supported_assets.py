#!/usr/bin/env python3
"""
Enumerate cross-chain bridge protocol supported assets for Solana -> Ethereum.
Method 1: Via Wormhole API
Method 2: Via Dune Analytics
Method 3: Via on-chain data queries
"""

import requests
import json
from collections import defaultdict

# ============================================================================
# Method 1: Wormhole API - Get list of supported assets
# ============================================================================

def get_wormhole_supported_tokens():
    """
    Fetch supported assets via the Wormhole Token List API.
    """
    # Wormhole Token List (officially maintained)
    url = "https://raw.githubusercontent.com/wormhole-foundation/wormhole-token-list/main/content/by_source.json"
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # Filter tokens originating from Solana
        solana_tokens = data.get('solana', {})
        
        # Find tokens that can be bridged to Ethereum
        sol_to_eth_tokens = []
        for token_address, token_info in solana_tokens.items():
            destinations = token_info.get('destinations', {})
            if 'ethereum' in destinations:
                sol_to_eth_tokens.append({
                    'solana_address': token_address,
                    'symbol': token_info.get('symbol'),
                    'name': token_info.get('name'),
                    'decimals': token_info.get('decimals'),
                    'ethereum_address': destinations['ethereum'].get('address'),
                })
        
        return sol_to_eth_tokens
    
    except Exception as e:
        print(f"Error fetching Wormhole token list: {e}")
        return None


def get_wormhole_tokens_v2():
    """
    Wormhole Connect Token List (newer format).
    """
    url = "https://raw.githubusercontent.com/wormhole-foundation/wormhole-connect/main/wormhole-connect/src/config/mainnet/tokens.ts"
    # This is a TS file; it needs parsing or use another API
    
    # Alternative: use Wormholescan API
    api_url = "https://api.wormholescan.io/api/v1/tokens"
    
    try:
        response = requests.get(api_url, timeout=30)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    
    return None


# ============================================================================
# Method 2: Count assets actually bridged via on-chain historical transactions
# ============================================================================

def get_dune_query_bridged_assets():
    """
    Return a Dune SQL query to count assets historically bridged from Solana to Ethereum.
    """
    
    query = """
-- Count assets actually bridged from Solana -> Ethereum via Wormhole
-- Run this query on Dune

-- Method A: Count wrapped Solana assets received on the Ethereum side
SELECT 
    t.contract_address AS token_address,
    t.symbol,
    COUNT(*) AS transfer_count,
    COUNT(DISTINCT t."to") AS unique_receivers,
    ROUND(SUM(COALESCE(t.amount_usd, 0)), 2) AS total_volume_usd
FROM tokens_ethereum.transfers t
WHERE 
    -- Wormhole-minted wrapped tokens are typically sent from the Token Bridge contract
    t."from" = 0x3ee18B2214AFF97000D974cf647E7C347E8fa585
    AND t.block_time >= NOW() - INTERVAL '90' DAY
GROUP BY 1, 2
HAVING COUNT(*) > 10  -- Filter out rarely used tokens
ORDER BY total_volume_usd DESC
LIMIT 50;


-- Method B: Check mint records of Wormhole wrapped token contracts
-- Wrapped token names usually contain "Wormhole" or start with "w"
SELECT 
    contract_address,
    symbol,
    name,
    decimals
FROM tokens.erc20
WHERE 
    LOWER(name) LIKE '%wormhole%'
    OR LOWER(name) LIKE 'wrapped%solana%'
    OR LOWER(symbol) LIKE 'w%'  -- e.g. wSOL, wBTC, etc.
ORDER BY symbol;


-- Method C: Extract assets from LogTokensLocked events (if the table exists)
SELECT 
    asset_address,
    asset_chain,
    COUNT(*) AS bridge_count,
    SUM(amount) AS total_amount
FROM wormhole_ethereum.Wormhole_evt_LogTokensLocked
WHERE 
    target_chain = 1  -- Solana
    AND evt_block_time >= NOW() - INTERVAL '180' DAY
GROUP BY 1, 2
ORDER BY bridge_count DESC;
"""
    return query


# ============================================================================
# Method 3: Query the Wormhole Token Bridge contract directly
# ============================================================================

def get_wormhole_registered_tokens_onchain():
    """
    Query registered assets on the Wormhole Token Bridge via RPC.
    Requires the Web3 library.
    """
    
    code = """
# Requires: pip install web3

from web3 import Web3

# Connect to Ethereum RPC
w3 = Web3(Web3.HTTPProvider('https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY'))

# Wormhole Token Bridge contract address
TOKEN_BRIDGE = '0x3ee18B2214AFF97000D974cf647E7C347E8fa585'

# Token Bridge ABI (partial)
TOKEN_BRIDGE_ABI = [
    {
        "name": "wrappedAsset",
        "type": "function",
        "inputs": [
            {"name": "tokenChainId", "type": "uint16"},
            {"name": "tokenAddress", "type": "bytes32"}
        ],
        "outputs": [{"name": "", "type": "address"}]
    },
    {
        "name": "isWrappedAsset",
        "type": "function",
        "inputs": [{"name": "token", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}]
    }
]

contract = w3.eth.contract(address=TOKEN_BRIDGE, abi=TOKEN_BRIDGE_ABI)

# Check whether a token is a wrapped asset
def is_wrapped_asset(token_address):
    return contract.functions.isWrappedAsset(token_address).call()

# Get the wrapped address on Ethereum for an asset on Solana (chain_id=1)
def get_wrapped_address(solana_token_address_bytes32):
    return contract.functions.wrappedAsset(1, solana_token_address_bytes32).call()
"""
    return code


# ============================================================================
# Method 4: Use the Wormholescan API
# ============================================================================

def get_assets_from_wormholescan():
    """
    Wormholescan provides an API for cross-chain assets.
    """
    
    # Fetch all Solana-related token transfers
    base_url = "https://api.wormholescan.io"
    
    endpoints = {
        'tokens': '/api/v1/tokens',
        'token_by_chain': '/api/v1/tokens?sourceChain=1',  # 1 = Solana
        'transfers': '/api/v1/transactions?sourceChain=1&targetChain=2',  # Sol->Eth
    }
    
    results = {}
    
    for name, endpoint in endpoints.items():
        try:
            url = base_url + endpoint
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                results[name] = response.json()
                print(f"✅ {name}: {len(results[name])} items")
            else:
                print(f"❌ {name}: HTTP {response.status_code}")
        except Exception as e:
            print(f"❌ {name}: {e}")
    
    return results


# ============================================================================
# Method 5: deBridge API
# ============================================================================

def get_debridge_supported_tokens():
    """
    Fetch supported assets via the deBridge API.
    """
    
    # deBridge Token List API
    url = "https://api.dln.trade/v1.0/supported-chains-tokens"
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # Filter tokens supported on both Solana and Ethereum
        solana_tokens = {}
        ethereum_tokens = {}
        
        for chain_id, chain_data in data.get('chains', {}).items():
            if chain_data.get('chainName') == 'Solana':
                solana_tokens = chain_data.get('tokens', {})
            elif chain_data.get('chainName') == 'Ethereum':
                ethereum_tokens = chain_data.get('tokens', {})
        
        # Find tokens present on both sides (matched by symbol)
        sol_symbols = {t.get('symbol'): t for t in solana_tokens.values()}
        eth_symbols = {t.get('symbol'): t for t in ethereum_tokens.values()}
        
        common_tokens = []
        for symbol in sol_symbols:
            if symbol in eth_symbols:
                common_tokens.append({
                    'symbol': symbol,
                    'solana': sol_symbols[symbol],
                    'ethereum': eth_symbols[symbol]
                })
        
        return common_tokens
        
    except Exception as e:
        print(f"Error: {e}")
        return None


# ============================================================================
# Method 6: Allbridge API
# ============================================================================

def get_allbridge_supported_tokens():
    """
    Allbridge Core API
    """
    url = "https://core.api.allbridgecoreapi.net/token-info"
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        sol_to_eth = []
        
        # Iterate over all tokens
        for token_key, token_info in data.items():
            chains = token_info.get('chains', {})
            
            # Check if both Solana and Ethereum are supported
            if 'SOL' in chains and 'ETH' in chains:
                sol_to_eth.append({
                    'symbol': token_info.get('symbol'),
                    'solana_address': chains['SOL'].get('tokenAddress'),
                    'ethereum_address': chains['ETH'].get('tokenAddress'),
                    'solana_pool': chains['SOL'].get('poolAddress'),
                    'ethereum_pool': chains['ETH'].get('poolAddress'),
                })
        
        return sol_to_eth
        
    except Exception as e:
        print(f"Error: {e}")
        return None


# ============================================================================
# Main program
# ============================================================================

def main():
    print("=" * 70)
    print("Cross-chain bridge supported assets analysis tool")
    print("=" * 70)
    
    # 1. Wormhole
    print("\n[Wormhole] Supported Solana -> Ethereum assets:")
    print("-" * 50)
    tokens = get_wormhole_supported_tokens()
    if tokens:
        for i, t in enumerate(tokens[:20], 1):
            print(f"{i:3}. {t['symbol']:<10} | SOL: {t['solana_address'][:20]}...")
        print(f"... Total: {len(tokens)} assets")
    else:
        print("Failed to fetch; please check your network connection")
    
    # 2. deBridge
    print("\n[deBridge] Supported Solana <-> Ethereum assets:")
    print("-" * 50)
    debridge_tokens = get_debridge_supported_tokens()
    if debridge_tokens:
        for i, t in enumerate(debridge_tokens[:20], 1):
            print(f"{i:3}. {t['symbol']}")
        print(f"... Total: {len(debridge_tokens)} assets")
    
    # 3. Allbridge
    print("\n[Allbridge] Supported Solana <-> Ethereum assets:")
    print("-" * 50)
    allbridge_tokens = get_allbridge_supported_tokens()
    if allbridge_tokens:
        for i, t in enumerate(allbridge_tokens, 1):
            print(f"{i:3}. {t['symbol']}")
    
    # 4. Output Dune query
    print("\n[Dune] SQL query (count assets actually bridged):")
    print("-" * 50)
    print("Copy the following SQL and run it on Dune:")
    print(get_dune_query_bridged_assets())
    
    # Save results
    output = {
        'wormhole': tokens,
        'debridge': debridge_tokens,
        'allbridge': allbridge_tokens,
    }
    
    with open('bridge_supported_assets.json', 'w') as f:
        json.dump(output, f, indent=2, default=str)
    
    print("\nResults saved to bridge_supported_assets.json")


if __name__ == "__main__":
    main()
