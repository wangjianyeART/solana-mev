# Solana MEV Empirical Analysis Toolkit

This repository contains the data collection, detection, and analysis code accompanying our research paper on Maximal Extractable Value (MEV) on the Solana blockchain. The toolkit covers three major MEV categories — **sandwich attacks**, **arbitrage**, and **liquidations** — and examines their relationship with market volatility and network conditions.

## Repository Structure

```
opensource/
├── datasource/          # Data collection: volatility sampling & transaction fetching
├── sandwich/            # Sandwich attack detection
├── paper_arbitrage/     # On-chain arbitrage detection & profit analysis
├── cross_arbitrage/     # Cross-chain bridge analysis (Solana ↔ Ethereum)
├── Liquidations/        # Lending protocol liquidation analysis (Kamino, MarginFi, Jupiter)
├── liquidation_paper/   # Streamlined Kamino liquidation pipeline (paper version)
└── analyzer/            # Aggregated statistics & visualization
```

## Prerequisites

- Python 3.9+
- A [Helius](https://helius.dev/) RPC API key (for fetching Solana transaction data)

### Python Dependencies

```bash
pip install requests pandas numpy matplotlib tabulate python-dotenv aiohttp
```

### Environment Setup

Create a `.env` file in each module directory that requires API access:

```
HELIUS_API_KEY=your_helius_api_key
```

---

## 1. datasource/ — Data Collection Pipeline

This module samples time periods by market volatility and fetches raw Solana transaction data for analysis.

### Workflow

```
Step 1: Sample volatile minutes     →  CSV files
Step 2: Map UTC timestamps to slots →  CSV with slot ranges
Step 3: Fetch transactions by slot  →  JSON transaction data
```

### Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `getmins.py` | Select top volatile minutes (BTC & SOL) from Binance over 2 years | `python getmins.py` |
| `getmins_low.py` | Select lowest volatility minutes as control group | `python getmins_low.py` |
| `random_minutes.py` | Select random minutes as baseline control | `python random_minutes.py` |
| `dedup_csv.py` | Deduplicate high-volatility CSV (keep highest per minute) | `python dedup_csv.py` |
| `utc_slot.py` | Map UTC timestamps to Solana slot ranges via binary search | `python utc_slot.py` |
| `batch_utc_slot.py` | Batch version for multiple CSV files | `python batch_utc_slot.py` |
| `minutes_to_slots.py` | Alternative slot mapping with two-sided binary search | `python minutes_to_slots.py` |
| `batch_gettx.py` | Fetch transactions for all slot ranges in CSV | `python batch_gettx.py` |
| `gettxfromslot.py` | Fetch transactions for a specific slot range | `python gettxfromslot.py` |
| `arbitrage.py` | Quick arbitrage scan with protocol identification | `python arbitrage.py` |

### Data Files

| File | Description |
|------|-------------|
| `high_volatility_minutes.csv` | Top volatile minutes (BTC/SOL) from Binance |
| `low_volatility_minutes.csv` | Lowest volatility minutes (control) |
| `random_volatility_minutes.csv` | Random minutes (baseline) |
| `*_with_slots.csv` | Above CSVs enriched with Solana slot ranges |
| `mev_full_analysis_*.json` | Sample transaction data (full metadata) |

### Transaction JSON Schema

Each fetched transaction contains:

```json
{
  "sig": "transaction_signature",
  "slot": 324115988,
  "accounts": ["signer_address", "..."],
  "fee": 5000,
  "has_err": false,
  "pre_token_balances": [{"accountIndex": 0, "mint": "...", "owner": "...", "uiTokenAmount": {"uiAmount": 1.5}}],
  "post_token_balances": ["...same structure..."],
  "pre_balances": [1000000000],
  "post_balances": [999995000],
  "logs": ["Program log: ..."],
  "inner_instructions": []
}
```

---

## 2. sandwich/ — Sandwich Attack Detection

Detects sandwich attacks (front-running + back-running) within Solana slots by analyzing token balance changes.

### Detection Algorithm

1. Extract per-transaction token balance changes (signer vs pool)
2. Identify **front-run** transactions: same signer buys token (positive change)
3. Identify **back-run** transactions: same signer sells token (negative change) on the same pool
4. Locate **victim** transactions between front and back with positive changes on the same mint/pool

### Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `batch_sandwich.py` | Batch detection across all data files, outputs summary stats | `python batch_sandwich.py` |
| `new.py` | Two-phase detection: extract tx info → detect attacks with profit calculation | See below |
| `try.py` | Experimental detection variants (iterative algorithm development) | `python try.py` |

### Usage Example (new.py)

```python
# Phase 1: Extract transaction info
python -c "from new import *; extract_tx_info('mev_full_analysis_324115990_324115990.json', 'tx_info_debug.json')"

# Phase 2: Detect sandwich attacks
python -c "from new import *; detect_sandwich_attacks('tx_info_debug.json')"
```

### Output Structure

Each detected sandwich attack:

```json
{
  "slot": "324115990",
  "attacker": "attacker_wallet_address",
  "mint": "token_mint_address",
  "pool": "pool_address",
  "front_sig": "front_run_tx_signature",
  "back_sig": "back_run_tx_signature",
  "front_amount": 1234.56,
  "back_amount": 1240.12,
  "profit": 5.56,
  "victims": [{"sig": "...", "signer": "...", "amount": 100.0}]
}
```

---

## 3. paper_arbitrage/ — On-Chain Arbitrage Detection

Detects and analyzes atomic arbitrage transactions for SOL, USDC, and USDT.

### Workflow

```
Step 1: Fetch transactions      →  gettx.py
Step 2: Batch detect arbitrage  →  batch_detect.py
Step 3: Compute profit & chain  →  compute_profit_chain.py
Step 4: Statistical analysis    →  regress_profit_chain.py, stats_chain_length_signers.py
Step 5: Visualization           →  plot_profit_chain_length.py, explore_profit_chain.py
```

### Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `detect_arbitrage.py` | Core detection engine (SOL/USDC/USDT) | `from detect_arbitrage import analyze_file` |
| `gettx.py` | Fetch transactions from Helius RPC | `python gettx.py` |
| `batch_detect.py` | Batch scan all data files for arbitrage | `python batch_detect.py` |
| `batch_liquidation.py` | Scan logs for liquidation events | `python batch_liquidation.py` |
| `compute_profit_chain.py` | Extract profit and chain length per arbitrage tx | `python compute_profit_chain.py` |
| `measure_chain_length.py` | Measure chain length for all transactions (population baseline) | `python measure_chain_length.py` |
| `explore_profit_chain.py` | Spearman correlation & binned analysis | `python explore_profit_chain.py` |
| `regress_profit_chain.py` | Linear & quadratic regression (profit vs chain length) | `python regress_profit_chain.py` |
| `stats_chain_length_signers.py` | Distribution statistics by chain length & signer count | `python stats_chain_length_signers.py` |
| `plot_profit_chain_length.py` | Scatter plot visualization | `python plot_profit_chain_length.py` |
| `sol.py` / `usdc.py` / `usdt.py` | Token-specific detection with strict criteria | `python sol.py` |

### Key Concepts

- **Chain Length**: Number of transfer events in a transaction / 2, representing swap complexity
- **Two-Signer Transactions**: Transactions with 2 signers (main + Jito tip), indicating MEV bundle usage
- **Leverage**: `signer_profit / max_pool_increase`, measuring capital efficiency

### Data Files

| File | Description |
|------|-------------|
| `arb_profit_chain_length.csv` | Per-arbitrage profit and chain length data |
| `profit_chain_regression.json` | Regression coefficients |
| `stats_chain_length_signers.json` | Distribution statistics |
| `profit_vs_chain_length.png` | Scatter plot visualization |

---

## 4. cross_arbitrage/ — Cross-Chain Bridge Analysis

Analyzes cross-chain bridge activity between Solana and Ethereum to study cross-chain arbitrage opportunities.

### Scripts (in `bridge/` subdirectory)

| Script | Purpose | Usage |
|--------|---------|-------|
| `getvol.py` | Fetch bridge volume/transaction data from DefiLlama | `python getvol.py` |
| `bridge_supported_assets.py` | Query supported assets across Wormhole, deBridge, Allbridge | `python bridge_supported_assets.py` |
| `getwormhole.py` | Measure Wormhole bridge latency (Solana → Ethereum) | `python getwormhole.py` |
| `find_first_usdc_to_eth.py` | Track USDC transfers via Wormhole on a given date | `python find_first_usdc_to_eth.py 2025-08-12` |
| `mayan.py` | Fetch Mayan Finance supported tokens on Solana | `python mayan.py` |
| `count_mayan_tokens_by_mint.py` | Deduplicate and count unique Mayan tokens | `python count_mayan_tokens_by_mint.py` |

### Data Files

| File | Description |
|------|-------------|
| `solana_eth_bridge_data.json/csv` | Bridge volume & transaction stats (12 bridges) |
| `wormhole_solana_to_eth_all_*.json` | All Wormhole Solana→ETH operations on a date |
| `layerzero_tokens.json` | LayerZero supported token list |
| `wormhole_native_token_transfer_token_list.json` | Wormhole NTT token list |
| `mayan_solana_unique_mints.json` | Unique Solana token mints on Mayan |

---

## 5. Liquidations/ — Lending Protocol Liquidation Analysis

Multi-protocol liquidation MEV analysis supporting **Kamino**, **MarginFi**, and **Jupiter Lend**.

### Architecture

```
Liquidations/
├── main.py                      # Unified multi-protocol monitor
├── config.py                    # Protocol addresses & configuration
├── liquidation_analyzer/
│   ├── 01_fetch_transaction.py  # Fetch single transaction
│   ├── 02_parse_transaction.py  # Parse with IDL decoder
│   ├── 03_calculate_profit.py   # Calculate liquidation profit
│   ├── analyze_liquidations.py  # Comprehensive analysis engine
│   ├── kamino_decoder.py        # Kamino IDL instruction decoder
│   ├── fast_kamino/             # High-performance Kamino pipeline
│   │   ├── main.py              # 3-step orchestrator
│   │   ├── step1_fetch_liquidations_fast.py
│   │   ├── step2_batch_parse.py
│   │   └── step3_analyze.py
│   ├── marginfi/                # MarginFi protocol pipeline
│   │   ├── main.py
│   │   ├── marginfi_decoder.py
│   │   ├── step1_fetch_liquidations_fast.py
│   │   ├── step2_batch_parse.py
│   │   └── step3_analyze.py
│   └── jupiter/                 # Jupiter Lend pipeline
│       ├── main.py
│       ├── jupiter_decoder.py
│       ├── step1_fetch_liquidations_fast.py
│       ├── step2_batch_parse.py
│       └── step3_analyze.py
```

### 3-Step Pipeline (per protocol)

```
Step 1: Fetch    — Concurrent fetching with rate limiting, filter by liquidation discriminator
Step 2: Parse    — IDL-based instruction decoding, extract balance changes & participants
Step 3: Analyze  — Calculate costs (gas, priority fee, flash loan fee) and net profit in USD
```

### Usage

```bash
# Full Kamino pipeline (scan 5000 recent signatures)
cd Liquidations/liquidation_analyzer/fast_kamino
python main.py --limit 5000

# Run individual steps
python main.py --step 1 --limit 10000
python main.py --step 2 --input ./data/liquidations_raw_xxx.json
python main.py --step 3 --input ./data/liquidations_parsed_xxx.json

# MarginFi pipeline
cd ../marginfi
python main.py --limit 5000

# Jupiter Lend pipeline
cd ../jupiter
python main.py --limit 5000

# Analyze a single transaction
cd ..
python 01_fetch_transaction.py <tx_signature>
python 02_parse_transaction.py ./data/<sig>_raw.json
python 03_calculate_profit.py ./data/<sig>_raw_parsed.json
```

### Analysis Output

Each liquidation analysis includes:

```json
{
  "signature": "...",
  "liquidator": "wallet_address",
  "obligation_owner": "borrower_address",
  "uses_flash_loan": true,
  "flash_loan_amount": 1000.0,
  "debt_token": "USDC",
  "debt_amount": 500.0,
  "collateral_token": "SOL",
  "collateral_received": 5.2,
  "gas_fee_usd": 0.001,
  "priority_fee_sol": 0.005,
  "gross_profit_usd": 12.50,
  "net_profit_usd": 11.80
}
```

### Supported Protocols

| Protocol | Program ID |
|----------|-----------|
| Kamino | `KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD` |
| MarginFi | `2jGhuVUuy3umdzByFx8sNWUAaf5vaeuDm78RDPEnhrMr` |
| Jupiter Lend | `jupr81YtYssSyPt8jbnGuiWon5f6x9TcDEFxYe3Bdzi` |

---

## 6. liquidation_paper/ — Paper-Version Kamino Pipeline

A self-contained copy of the Kamino liquidation pipeline used for the paper's analysis. Same 3-step architecture as `Liquidations/liquidation_analyzer/fast_kamino/`.

```bash
cd liquidation_paper/fast_kamino
python main.py --limit 5000
```

---

## 7. analyzer/ — Aggregated Analysis & Visualization

Produces the final statistics and figures used in the paper, reading from pre-computed summary data.

### Statistical Scripts

| Script | Purpose | Output |
|--------|---------|--------|
| `calc_volatility_avg.py` | Average volatility by category (High/Random/Low) | Console |
| `calc_fail_rate_stats.py` | Transaction failure rates by volatility | Console |
| `calc_empty_slot_stats.py` | Empty slot rates by volatility | Console |
| `calc_tx_volume_stats.py` | Transaction volume statistics | Console |
| `calc_eth_tx_total.py` | Total Ethereum transactions (for comparison) | Console |
| `calc_eth_tx_avg_2y.py` | Average daily ETH transactions over 2 years | Console |
| `debug_sandwich_data.py` | Sandwich attack rate analysis | Console |

### Visualization Scripts

| Script | Output Figure | Description |
|--------|--------------|-------------|
| `plot_mev_complete.py` | `mev_complete.png` | 2-panel overview: network stress + MEV density |
| `plot_mev_boxplot.py` | `mev_rates_boxplot.png` | Box plots for 3 MEV types by volatility |
| `plot_mev_density.py` | `mev_density_by_type.png` | MEV density with dual Y-axes |
| `plot_network_congestion.py` | `network_congestion.png` | Empty block & failure rates |
| `plot_sandwich_distribution.py` | `sandwich_count_boxplot.png` | Sandwich count distribution |
| `plot_sandwich_distribution_sampled.py` | `sandwich_rate_distribution_sampled_90.png` | Sampled CDF of sandwich rates |
| `plot_volatility_sandwich.py` | `volatility_vs_sandwich.png` | Volatility vs sandwich count scatter |
| `plot_volatility_sandwich_rate.py` | `volatility_vs_sandwich_count_all.png` | Sandwich rate scatter by category |
| `plot_volatility_sandwich_random.py` | `volatility_sandwich_rate_random.png` | Random category only |
| `plot_volatility_liquidation.py` | `volatility_vs_liquidation.png` | Liquidation events vs volatility |
| `plot_volatility_liquidation_rate.py` | `volatility_vs_liquidation_rate.png` | Liquidation rate scatter |
| `plot_volatility_liquidation_total_rate.py` | `volatility_vs_liquidation_total_rate.png` | Total liquidation rate |
| `plot_volatility_liquidation_random.py` | `volatility_vs_liquidation_random.png` | Random category only |
| `plot_volatility_liquidation_high_low.py` | `volatility_vs_liquidation_high_low.png` | High vs Low comparison |

### Core Data File

`merged_mev_volatility.json` — the central dataset joining MEV activity with volatility data:

```json
{
  "slot_range_key": {
    "success_tx": 1200,
    "fail_tx": 800,
    "total_arb": 15,
    "sandwich_success": 3,
    "sandwich_failed": 1,
    "liquidation_success": 2,
    "liquidation_fail": 0,
    "category": "high",
    "volatility": 0.0368,
    "empty_slot_rate": 0.032,
    "datetime_utc": "2024-03-05 19:57 UTC"
  }
}
```

---

## End-to-End Workflow

```
1. datasource/getmins.py          ──→  Sample volatile time periods
2. datasource/utc_slot.py         ──→  Map to Solana slots
3. datasource/batch_gettx.py      ──→  Fetch raw transaction data
         │
         ├──→ sandwich/batch_sandwich.py        ──→  Detect sandwich attacks
         ├──→ paper_arbitrage/batch_detect.py   ──→  Detect arbitrage
         ├──→ paper_arbitrage/batch_liquidation.py ──→ Detect liquidations (from logs)
         │
         └──→ analyzer/                         ──→  Merge, compute stats, visualize
              ├── calc_*.py                     ──→  Statistical summaries
              └── plot_*.py                     ──→  Publication figures

Liquidations/ (independent pipeline)
         └──→ step1 → step2 → step3            ──→  Protocol-level liquidation profit analysis
```

## License

This code is released for academic and research purposes.

## Citation

If you use this toolkit in your research, please cite our paper.
