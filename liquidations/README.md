# Solana Lending Protocol Liquidation MEV Monitor

Monitor liquidation activity across major Solana lending protocols and analyze liquidation MEV profit.

## Features

- Monitor 4 major lending protocols: Solend, MarginFi, Kamino, Mango
- Use Helius API to fetch enhanced transaction data
- RPC fallback (when Helius is unavailable)
- Automatically calculate MEV profit, ROI, liquidation discount
- Table and JSON format output

## Installation

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Configure API Key:

```bash
# Copy the environment variable template
cp .env.example .env

# Edit the .env file and enter your Helius API key
# Free registration: https://helius.dev
```

## Usage

### Basic Usage

Query liquidation data from the past 1 hour:

```bash
python main.py
```

### Custom Time Range

Query data from the past 3 hours:

```bash
python main.py --hours 3
```

### Use RPC Fallback

Use Solana RPC instead of Helius API:

```bash
python main.py --rpc
```

### No JSON Saving

Display table only, do not save JSON:

```bash
python main.py --no-save
```

## Output Example

```
================================================================================
Solana Lending Protocol Liquidation MEV Monitor Report
================================================================================

Total: 5 liquidation transactions found

Solend: 3 liquidations
--------------------------------------------------------------------------------
Time                 Tx Hash         Liquidator      Debt Repaid      Collateral Received  MEV Profit  ROI      Gas Fee
2024-01-29 14:30:15  abc123...       7xK9...         1000.00 USDC    0.5000 SOL     $15.20    1.52%   $0.0050
2024-01-29 14:45:22  def456...       9mP2...         500.00 USDT     100.0000 BONK  $8.70     1.74%   $0.0048

Solend Statistics:
  Total MEV Profit: $45.30
  Average MEV Profit: $15.10
  Average ROI: 1.63%
```

## File Descriptions

- `main.py` - Main program entry point
- `config.py` - Configuration file (API keys, protocol addresses)
- `helius_client.py` - Helius API client
- `rpc_fallback.py` - Solana RPC fallback
- `liquidation_parser.py` - Liquidation transaction parser
- `mev_calculator.py` - MEV profit calculator

## Monitored Protocols

- **Solend**: So1endDq2YkqhipRh3WViPa8hdiSpxWy6z3Z6tMCpAo
- **MarginFi**: MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA
- **Kamino**: KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD
- **Mango**: 4MangoMjqJ2firMokCjjGgoK8d4MXcrgL7XJaL3w6fVg

## Data Sources

- **Primary**: Helius Enhanced Transactions API (parsed data)
- **Fallback**: Solana RPC (raw data)
- **Prices**: Jupiter Price API v2

## Notes

1. Helius free tier is limited to 100,000 credits/month
2. Public RPC has rate limits; you may need to run your own node
3. Liquidation data accuracy depends on transaction log and instruction parsing
4. MEV profit calculation is based on current token prices, which may differ from actual values

## Extension Features

Features that can be added:

- Real-time monitoring mode (WebSocket)
- Database storage
- Large liquidation alerts
- Support for more protocols
- Liquidation opportunity prediction

## License

MIT
