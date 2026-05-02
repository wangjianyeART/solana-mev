# Arbitrage Clearing on Solana — Reproduction Package

This repository accompanies the section **Arbitrage Clearing** in
[`paper/arbitrage_clearing_en.pdf`](paper/arbitrage_clearing_en.pdf).
It contains the end-to-end pipeline that produces every number, table,
and figure in that section, starting from on-chain Solana RPC and
ending at the three plots used in the paper.

The cleaned dataset behind the section covers:

| Field                    | Value                       |
| ------------------------ | --------------------------- |
| Window                   | 2025-04-04 → 2026-03-03     |
| Data gap                 | 2025-09-09 → 2026-02-14 (159 days) |
| Detected arbitrages      | 561,659 (387,884 wSOL-denominated) |
| Unique searcher wallets  | 4,551                       |
| Settlement channels      | non-bundle / single-tx Jito bundle / multi-tx Jito bundle |

All headline statistics are reproduced from the shipped pipeline; see
[`data/stats.json`](data/stats.json).

---

## Folder layout

```
clearing_release/
├── README.md
├── paper/                              # the section text and rendered PDF
│   └── arbitrage_clearing_en.{tex,pdf}
├── pipeline/                           # the seven-stage processing chain
│   ├── 01_build_cursors.py
│   ├── 02_fetch_arbitrages.py
│   ├── 03_fetch_bundles.py
│   ├── 04_merge_arbs_bundles.py
│   ├── 05_reclean_merged.py
│   ├── 06_calc_profit.py
│   └── 07_make_figures.py
├── figures/                            # the three figures in the paper
│   ├── fig1_settlement_channels.{png,pdf}
│   ├── fig2_dex_venues.{png,pdf}
│   └── fig3_tip_to_gross.{png,pdf}
└── data/
    ├── stats.json                      # all aggregate numbers cited in the paper
    ├── cursors.json                    # 21,900 sampling cursors used in the study
    ├── cursor_stats.json               # per-cursor counts (success / fail / arb / non-arb)
    └── sample_merged_clean.json        # 5-record schema sample of the cleaned dataset
```

The full cleaned dataset (~900 MB across 60 JSON files) is **not**
shipped in this repo. The pipeline regenerates it deterministically
from public RPC; see "Reproducing the dataset" below.

---

## Data origin (what each step does)

The pipeline mirrors the *Data source* and *Arbitrage identification*
paragraphs of the paper.

### 01 · `pipeline/01_build_cursors.py`
Builds **time-uniform sampling cursors**. We do not crawl the chain
continuously — we sample 60 evenly-spaced timestamps per UTC day for
the past 365 days, locate the Solana slot whose block-time matches each
target timestamp (via `getBlockTime` + slot-vs-time linear
interpolation), and record the first signature in that slot.

- RPC: `https://mainnet.helius-rpc.com/?api-key=$HELIUS_API_KEY`
- Output: `cursors/cursors.json` (a copy is in `data/cursors.json`)
- Each entry: `{ts, ts_str, slot, block_time, sig}`

### 02 · `pipeline/02_fetch_arbitrages.py`
For every cursor, retrieves up to `FETCH_PER_CURSOR` Jupiter v6
signatures via `getSignaturesForAddress`, then fetches each
transaction with `getTransaction` (`encoding=jsonParsed`,
`maxSupportedTransactionVersion=0`) on the standard Helius RPC (this
costs ~1 credit/tx instead of 100 for the Enhanced API).

For each transaction the script reconstructs:

- account-level **native SOL deltas** (pre/postBalances)
- **token deltas** per mint (pre/postTokenBalances)
- inner-instruction **SPL transfer pairs** (for cycle reconstruction)
- invoked **DEX programs** (Orca Whirlpool, Meteora DLMM/LB/DAMM,
  Raydium AMM v3/v4 / CAMM, PumpSwap, PancakeSwap, Saber, SolFi v2,
  GoonFi V2, …)
- **Jito tip** (sum of SOL credited to the eight known tip accounts)
- **swap count** (top-level Jupiter inner instructions, deduped)

A transaction is kept as an arbitrage candidate iff:

1. it touches **≥ 2 distinct DEX venues** (excluding Jupiter and
   system / SPL / memo programs); and
2. either
   (a) wSOL net delta > 0 with no non-circular token going negative
   (wSOL profit), or
   (b) some non-wSOL mint is both sent and received with positive net
   delta (token-cycle profit), with wSOL net change near zero
   (`|Δ wSOL| ≤ 1000 lamports`) for the token-cycle branch.

This is the operational form of the paper's "circular path with at
least two DEX venues" definition.

- Output: `jup_arb_data_cursor_std/arbs_*.json`,
  `jup_arb_data_cursor_std/cursor_stats.json`
- A copy of `cursor_stats.json` is in `data/cursor_stats.json`.

### 03 · `pipeline/03_fetch_bundles.py`
For every signature in `arbs_*.json`, queries Jito's public bundle API
(`https://bundles.jito.wtf/api/v1/bundles/...`) and records:

- whether the transaction landed inside a Jito bundle (`in_bundle`)
- the bundle id, validator, slot, landed tip, transaction count,
  position of the queried transaction within the bundle, and the full
  list of bundle transaction signatures.

Output: `jito_bundle_results/bundles_*.json`. A `null` `in_bundle`
means the lookup failed; `False` means the transaction is confirmed
not in a bundle (HTTP 404 from Jito).

### 04 · `pipeline/04_merge_arbs_bundles.py`
Joins `arbs_*.json` and `bundles_*.json` by `signature` and writes
`merged_arbs/merged_*.json`. Adds the bundle metadata fields used by
the paper: `in_bundle`, `bundle_tx_count`, `bundle_tip_lamports`,
`bundle_validator`, …

### 05 · `pipeline/05_reclean_merged.py`
Applies the second cleaning pass described in the paper:

- drops records where `arb_io` shows a one-way swap (`input_raw > 0`
  and `output_raw == 0`, or input/output ratio worse than 2×);
- drops records where wSOL is consumed but never re-entered (`Δ wSOL <
  −1000 lamports`);
- drops records where wSOL is only received without being a path leg
  (`input_raw == 0 and Δ wSOL > 1000 lamports`).

Outputs: `merged_arbs_clean/merged_*.json` plus an in-place patch of
`cursor_stats.json` so its `cnt_arb` / `cnt_non_arb` columns match the
post-clean counts.

### 06 · `pipeline/06_calc_profit.py`
Adds the derived columns used by figures and tables:

| New field                     | Definition |
| ----------------------------- | ---------- |
| `arb_cost_lamports`           | `tx_fee_lamports + tip_paid` (tip = `bundle_tip_lamports` for multi-tx bundles, `jito_tip_lamports` for single-tx bundles, 0 otherwise) |
| `arb_gross_profit_lamports`   | `sol_lamports` for wSOL arbitrages (else `null`) |
| `arb_net_profit_lamports`     | `arb_gross_profit_lamports − arb_cost_lamports` (wSOL only) |
| `arb_gross_profit_raw`        | `output_raw − input_raw` for token-cycle arbitrages |
| `arb_gross_profit_rate_pct`   | `gross / input_raw × 100` |
| `arb_net_profit_rate_pct`     | `net   / input_raw × 100` (wSOL only) |

This matches the paper's wSOL net profit formula:

```
π_net_i = ΔSOL_i − fee_i − tip^bundle_i   if i is in a multi-tx bundle
π_net_i = ΔSOL_i − fee_i                  otherwise
```

### 07 · `pipeline/07_make_figures.py`
Reads `merged_arbs_clean/`, regenerates the three paper figures into
`figures/`, and writes the aggregate statistics into `data/stats.json`.

The script independently re-derives `tip_paid` and `corrected_net`
from raw fields (so it does not depend on step 06 having been run on
the same machine) — this is why `data/sample_merged_clean.json`
contains the step-06 columns, but step 07 ignores them.

---

## Reproducing the dataset

### Prerequisites

```bash
pip install aiohttp numpy matplotlib
export HELIUS_API_KEY=<your-helius-api-key>
```

The pipeline uses ~1 credit per `getTransaction` call. The full study
window queries roughly 561k transactions plus the cursor lookups; budget
your key accordingly.

### Run from the release root

All scripts use relative paths, so you must `cd` into this directory
first:

```bash
cd clearing_release/

python pipeline/01_build_cursors.py        # ~hours, writes cursors/cursors.json
python pipeline/02_fetch_arbitrages.py     # the big step (long-running, resumable)
python pipeline/03_fetch_bundles.py        # queries Jito bundle API
python pipeline/04_merge_arbs_bundles.py   # merges arbs + bundles
python pipeline/05_reclean_merged.py       # second cleaning pass
python pipeline/06_calc_profit.py          # adds profit columns
python pipeline/07_make_figures.py         # produces figures/ + data/stats.json
```

Each step is **resumable**: re-running picks up from the last completed
file. Output directories (`cursors/`, `jup_arb_data_cursor_std/`,
`jito_bundle_results/`, `merged_arbs/`, `merged_arbs_clean/`) are
created at the release root.

### Skip the heavy steps

If you only want to regenerate the figures and `stats.json` from a
pre-computed cleaned dataset, point the figure script at the data
directory directly:

```bash
ARB_DATA_DIR=/path/to/merged_arbs_clean python pipeline/07_make_figures.py
```

The script also auto-discovers `./merged_arbs_clean`,
`<repo>/merged_arbs_clean`, and `<repo>/data/merged_arbs_clean` in
that order, so dropping the 60 `merged_*.json` files into any of those
directories also works.

A 5-record schema sample is in `data/sample_merged_clean.json`.

---

## Output schema

Each record in `merged_arbs_clean/merged_*.json` (after step 06) has
the structure shown in `data/sample_merged_clean.json`. Key fields:

| Field                       | Source                         | Meaning |
| --------------------------- | ------------------------------ | ------- |
| `signature`                 | `getTransaction`               | base58 transaction signature |
| `time`, `slot`              | `getTransaction.blockTime`     | local datetime + Solana slot |
| `wallet`                    | `accountKeys[0]`               | searcher / fee-payer |
| `dexes`                     | program-id table               | DEX venues invoked |
| `swap_count`                | inner instructions             | DEX hops in the route |
| `circular_mints`            | derived                        | mints both sent and received |
| `arb_token_mints`           | derived                        | profitable mints (wSOL or otherwise) |
| `arb_io[mint]`              | inner SPL transfers            | `{input_raw, output_raw, decimals}` per arb mint |
| `tx_fee_lamports`           | `meta.fee`                     | Solana priority + base fee |
| `jito_tip_lamports`         | sum over Jito tip accounts     | tip paid in this single tx |
| `sol_lamports`              | wSOL token delta of `wallet`   | wSOL net change |
| `native_lamports`           | native SOL delta of `wallet`   | native-SOL net change |
| `in_bundle`                 | Jito bundle API                | `True` / `False` / `None` (lookup failed) |
| `bundle_tx_count`           | Jito bundle API                | number of tx in the bundle |
| `bundle_tip_lamports`       | Jito bundle API                | landed tip for the whole bundle |
| `bundle_validator`          | Jito bundle API                | validator that landed it |
| `arb_cost_lamports`         | step 06                        | `fee + tip_paid` |
| `arb_gross_profit_lamports` | step 06                        | wSOL gross profit |
| `arb_net_profit_lamports`   | step 06                        | wSOL net after fee + tip |

---

## Mapping figures to scripts and numbers

| Figure | Script section in `07_make_figures.py` | Source rows | Numbers also recorded in `data/stats.json` |
| ------ | --------------------------------------- | ----------- | ------------------------------------------ |
| Fig. 1 (settlement channels) | "Figure 1" block | all records | `regime_counts`, `total_records` |
| Fig. 2 (DEX venues) | "Figure 2" block | all records | `top_venues` |
| Fig. 3 (tip / gross) | "Figure 3" block | wSOL bundle records with `gross > 0` | `bundle_tip_to_gross_pct`, `wsol_total_gross_sol`, `wsol_total_net_sol` |

The shaded gap shown in Fig. 1 (Sep 2025 – Feb 2026) is the 159-day
break in our cursor coverage and is hard-coded in
`07_make_figures.py`.

---

## License & notes

- Bundle cosignatures and tip tables embedded in
  `pipeline/02_fetch_arbitrages.py` (Jito tip accounts, DEX program
  IDs) are public mainnet identifiers.
- We deliberately exclude Jupiter v6 from the DEX venue tally because
  it is the routing aggregator, not a settlement venue. PumpSwap,
  GoonFi V2, SolFi v2, etc. are kept because they are settlement venues
  even when invoked through Jupiter.
- `cursor_stats.json` is overwritten in place by step 05; re-running
  the pipeline against new data will modify it.
