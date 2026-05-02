# Data Manifest

This manifest describes the main files included in the open-source artifact.

## Core Data

- `wormhole_data/wormhole_portal_1y.json`
  - Compact Wormhole Portal API export for the one-year Solana-Ethereum study.

- `wormhole_data/wormhole_all_1y_dedup.json`
  - Deduplicated compact Wormhole API export used for bridge-operation
    provenance checks.

- `wormhole_data/use/portal_full/recent_1y/matched/arbitrage/arbitrage_candidates_10pct.json.gz`
  - Default strict candidate dataset at the 10% gap threshold.
  - This is the primary data file consumed by `research_1y/scripts/common_loader.py`.

- `wormhole_data/use/portal_full/recent_1y/matched_mintfix_v2/arbitrage/arbitrage_candidates_10pct.json.gz`
  - Additional corrected mint-fix candidate snapshot retained for auditability.

## Price Metadata

- `wormhole_data/use/portal_full/recent_1y/matched/arbitrage/prices/symbol_map.json`
  - Token symbol to chain address mapping.

- `wormhole_data/use/portal_full/recent_1y/matched/arbitrage/prices/exact_sol.json`
  - Included Solana-side exact timestamp price cache.

- `wormhole_data/use/portal_full/recent_1y/matched/arbitrage/prices/quote_stats*.json`
  - Price-query summary statistics.

- `wormhole_data/use/portal_full/recent_1y/matched/arbitrage/prices/token_overview_*.json`
  - Token-level liquidity and metadata summaries.

## Experiment Outputs

- `wormhole_data/research_1y/tables/`
  - Original CSV tables for macro, timing, economics, liquidity, risk, and ML experiments.

- `wormhole_data/research_1y/tables_mintfix_v2/`
  - Corrected final CSV tables after the mint-fix run.

- `wormhole_data/research_1y/figs/`
  - Original generated figures.

- `wormhole_data/research_1y/figs_mintfix_v2/`
  - Corrected regenerated figures available for the mint-fix run.

- `wormhole_data/research_1y/cache/` and `cache_mintfix_v2/`
  - Checkpoints used by ML and entry-gap scripts.

## Omitted Large Data

The following large intermediate data is not bundled:

- Full raw Solana RPC transaction dumps.
- Full raw Ethereum transaction dumps.
- Address-level context JSON files.
- Full `series_eth_1m.json` and `eth_1m_shards/` price cache.

These can be regenerated with the included fetch, parse, price, and pipeline
scripts if API credentials and rate limits are available.
