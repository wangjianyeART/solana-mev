# Cross-Chain Arbitrage on Wormhole Portal: Open-Source Artifact

This artifact packages the code, key derived data, experiment outputs, and
figure-generation scripts for the Wormhole Portal one-year cross-chain
arbitrage study. The package is organized around:

`wormhole_data/research_1y/`

The goal is to make the origin of the research data clear: how bridge records
were collected, how they were processed into arbitrage candidates, how PnL and
experimental tables were computed, and how the paper figures were generated.

## Contents

```text
.
|-- README.md
|-- requirements.txt
|-- run_wormhole_1y_pipeline.py
|-- data_manifest/
|-- wormhole_data/
|   |-- *_1y.py                         # collection, matching, detection, PnL pipeline
|   |-- parse_*.py, fetch_*.py           # parser/fetcher dependencies used by the pipeline
|   |-- wormhole_portal_1y.json          # compact Wormhole Portal API export
|   |-- wormhole_all_1y_dedup.json       # compact deduplicated Wormhole API export
|   |-- use/portal_full/recent_1y/
|   |   |-- matched/arbitrage/
|   |   |   |-- arbitrage_candidates_10pct.json.gz
|   |   |   `-- prices/
|   |       |-- exact_sol.json
|   |       |-- symbol_map.json
|   |       |-- quote_stats*.json
|   |       `-- token_overview_*.json
|   |   `-- matched_mintfix_v2/arbitrage/
|   |       `-- arbitrage_candidates_10pct.json.gz
|   `-- research_1y/
|       |-- scripts/                     # analysis and figure-generation scripts
|       |-- tables/                      # original experiment tables
|       |-- tables_mintfix_v2/           # corrected final tables
|       |-- figs/                        # generated figures
|       |-- figs_mintfix_v2/             # corrected final regenerated figures
|       |-- cache/ and cache_mintfix_v2/ # ML and entry-gap checkpoints
|       |-- paper/paper.tex
|       `-- report_1y.md
```

## Data Provenance

The study window is 2025-04-15 to 2026-04-14. The pipeline reconstructs
cross-chain arbitrage candidates on Wormhole Portal Token Bridge between
Solana and Ethereum.

### 1. Bridge operation collection

The initial bridge event data comes from Wormhole API exports:

- `wormhole_data/fetch_wormhole_portal_1y.py`
- `wormhole_data/fetch_wormhole_all_1y.py`
- `wormhole_data/dedup_1y.py`

Included compact outputs:

- `wormhole_data/wormhole_portal_1y.json`
- `wormhole_data/wormhole_all_1y_dedup.json`

These files document the source bridge operations used before deeper
on-chain reconstruction.

### 2. On-chain transaction reconstruction

The full pipeline then reconstructs both sides of each bridge operation:

- `wormhole_data/fetch_portal_sigs_1y.py`
- `wormhole_data/fetch_portal_eth_txs_1y.py`
- `wormhole_data/parse_1y.py`
- `wormhole_data/parse_eth_1y.py`
- `wormhole_data/wormhole_lookup_1y.py`
- `wormhole_data/build_matched_1y.py`

The matched records link a Solana signature and Ethereum transaction hash for
each Portal bridge event.

### 3. Local context and arbitrage detection

To determine whether a bridge transfer is part of a cross-chain arbitrage
sequence, the pipeline fetches address-level context and parses surrounding
transactions:

- `wormhole_data/fetch_address_txs_1y.py`
- `wormhole_data/build_matched_context_1y.py`
- `wormhole_data/batch_parse_context_1y.py`
- `wormhole_data/retry_sol_failed_1y.py`
- `wormhole_data/detect_arbitrage_1y.py`
- `wormhole_data/tag_arbitrage_subtypes_1y.py`
- `wormhole_data/apply_greedy_dedup_1y.py`

The default included candidate file is:

`wormhole_data/use/portal_full/recent_1y/matched/arbitrage/arbitrage_candidates_10pct.json.gz`

It contains the final 10% gap-threshold strict candidate set used by the
analysis scripts. Its embedded `meta` section records the candidate counts,
classification statistics, direction statistics, and reliability labels.

The artifact also includes:

`wormhole_data/use/portal_full/recent_1y/matched_mintfix_v2/arbitrage/arbitrage_candidates_10pct.json.gz`

That file is kept for auditability of the corrected mint-fix run, but the
default `matched/` candidate is the more complete snapshot for reproducing the
published figures because it contains token and liquidity enrichment fields.

### 4. Price, liquidity, and PnL enrichment

PnL and price-gap computation is handled by:

- `wormhole_data/fetch_prices_minimal_1y.py`
- `wormhole_data/fetch_prices_precise_1y.py`
- `wormhole_data/shard_prices_1y.py`
- `wormhole_data/compute_pnl_1y.py`
- `wormhole_data/enrich_liquidity_1y_minimal.py`

Included lightweight price metadata:

- `symbol_map.json`
- `quote_stats.json`
- `quote_stats_minimal.json`
- `token_overview_sol.json`
- `token_overview_eth.json`
- `exact_sol.json`

The full Ethereum one-minute price cache is intentionally not included because
it is large. In the original run, `series_eth_1m.json` was about 804 MB and was
sharded into `eth_1m_shards/` for lazy loading. The generated outputs that
depend on those prices are included under `research_1y/tables*` and
`research_1y/figs*`.

## Research Scripts

The analysis scripts live in:

`wormhole_data/research_1y/scripts/`

Main scripts:

- `common_loader.py`: loads `arbitrage_candidates_10pct.json` or
  `arbitrage_candidates_10pct.json.gz`, normalizes it
  to a DataFrame, applies the dust-entry filter, and defines figure/table paths.
- `a_macro.py`: macro activity, token distribution, direction split, size
  quantiles. Produces `01_macro.png` and `a*.csv`.
- `b_timing.py`: bridge latency, atomicity, entry/exit delay, speed-tier ROI.
  Produces `02_timing.png` and `b*.csv`.
- `c_economics.py`: ROI distribution, size tiers, fees, net-PnL regressions.
  Produces `03_economics.png` and `c*.csv`.
- `d_liquidity.py`: liquidity tiers and price-gap event study. Produces
  `04_liquidity.png` and `d*.csv`. Rerunning this script requires the full
  price cache.
- `e_risk.py`: actor concentration, reliability tiers, and round-trip cases.
  Produces `05_risk.png` and `e*.csv`.
- `f_integrated.py`: gradient boosting regression/classification, feature
  importance, SHAP summaries. Produces `06_integrated.png` and `f*.csv`.
- `g_risk_model_validation.py`: risk-model validation. Produces
  `07_risk_validation.png` and `g21` to `g23` tables.
- `g2_refined_model.py`: refined validation model. Produces
  `08_refined_validation.png` and `g24` to `g25` tables.
- `h_pipeline_figure.py`: pipeline diagram. Produces `09_pipeline.png`.
- `i_daily_activity.py`: daily activity figures and CSVs.

## Reproducing Figures and Tables

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run selected scripts from the artifact root:

```bash
python wormhole_data/research_1y/scripts/a_macro.py
python wormhole_data/research_1y/scripts/b_timing.py
python wormhole_data/research_1y/scripts/c_economics.py
python wormhole_data/research_1y/scripts/e_risk.py
python wormhole_data/research_1y/scripts/f_integrated.py
python wormhole_data/research_1y/scripts/g_risk_model_validation.py
python wormhole_data/research_1y/scripts/g2_refined_model.py
python wormhole_data/research_1y/scripts/h_pipeline_figure.py
```

Outputs are written to:

- `wormhole_data/research_1y/tables/`
- `wormhole_data/research_1y/figs/`

Notes:

- `f_integrated.py`, `g_risk_model_validation.py`, and `g2_refined_model.py`
  use included checkpoint files from `cache/` in the default run.
- `d_liquidity.py` requires the full Ethereum price shard directory
  `wormhole_data/use/portal_full/recent_1y/matched/arbitrage/prices/eth_1m_shards/`
  to recompute price gaps from scratch. Its generated tables and figure are
  already included.
- Running the full raw-data pipeline requires external API access and local
  API keys for Solana RPC, Etherscan, and price providers.
- To inspect the mint-fix variant, run with
  `WORMHOLE_1Y_RUN_TAG=mintfix_v2`; outputs are written to
  `tables_mintfix_v2/` and `figs_mintfix_v2/`.

## Full Pipeline Entry Point

The top-level runner:

`run_wormhole_1y_pipeline.py`

It writes to an isolated tagged directory:

```bash
python run_wormhole_1y_pipeline.py --tag mintfix_v2 --dry-run
```

Pipeline stages:

1. `build_matched_1y.py`
2. `build_matched_context_1y.py`
3. `detect_arbitrage_1y.py`
4. `tag_arbitrage_subtypes_1y.py`
5. `apply_greedy_dedup_1y.py`
6. `compute_pnl_1y.py`

The included artifact is sufficient to inspect the final derived candidate
data and reproduce most paper tables/figures without rerunning the full
network-dependent collection pipeline.

## Included vs. Omitted Data

Included:

- Core candidate data: `arbitrage_candidates_10pct.json.gz`
- Compact Wormhole API exports
- Final and intermediate experiment tables
- Final figures
- ML/entry-gap checkpoints
- Collection, parsing, matching, arbitrage-detection, PnL, and plotting code

Omitted:

- Full raw Solana and Ethereum transaction dumps
- Full address-level transaction context JSONs
- Full Ethereum one-minute price shards
- Provider API keys and local machine-specific caches

The omitted data can be regenerated using the included collection and pipeline
scripts, subject to API access and provider rate limits.
