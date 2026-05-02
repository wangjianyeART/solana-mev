# Sandwish — Solana Jito Sandwich-Attack Dataset and Analysis Pipeline

A wallet-flow-based identification of Pattern-A sandwich attacks on Solana
(front-run + victim + back-run packed into a single Jito bundle), built on
joined Jito bundle metadata and Helius enhanced-tx data, plus the analysis
and figures supporting the paper section.

> **Headline finding.** Dropping the implicit "front and back legs share the
> same `feePayer`" assumption surfaces **11,591 confirmed Pattern-A
> sandwiches** across 12,916 unique slots and 161 distinct attacker wallets,
> of which 5,060 (38.4%) realise positive net SOL profit. This is roughly
> **two orders of magnitude** more than prior Solana studies of the same
> pattern.

---

## Layout

```
release/
├── README.md                  this file
├── LICENSE                    MIT
├── .env.example               API-key template (only needed to recollect raw data)
├── .gitignore
├── requirements.txt           numpy + matplotlib
│
├── pipeline/                  five-step analysis pipeline
│   ├── 1_detect_pattern_a.py        wallet-flow candidate detection
│   ├── 2_classify_recall.py         tier candidates by quote-inventory symmetry
│   ├── 3_clean_strong.py            R1 self-attack + R2 market-maker filtering
│   ├── 4_build_academic_figures.py  4 academic PNGs + interpretation text
│   └── 5_build_anatomy_figure.py    paper Fig. 1 (4-panel anatomy, PDF)
│
├── data/                      shipped dataset (~90 MB, with recall input gzipped)
│   ├── sandwiches_recall_wide.jsonl.gz  main input (139,700 candidates)
│   ├── sandwiches_strong_clean.jsonl    main output (11,591 strong sandwiches)
│   ├── strong_pool_blacklist.txt         wallets removed by R2
│   ├── strong_tip_fee_cache.jsonl        per-bundle tip + tx fee
│   ├── strong_sandwich_enriched.jsonl    enriched with unit-price / cost / capital
│   ├── strong_academic_stats.json        all stats reported in the paper
│   ├── strong_sandwich_stats.json
│   ├── sandwiches_recall_wide_summary.txt
│   └── paper_table_sandwich_summary.txt
│
├── figures/                   pre-rendered figures
│   ├── sandwich_anatomy_col_en.pdf       paper Fig. 1
│   ├── academic_price_vs_total_profit.png
│   ├── academic_fee_tip_costs.png
│   ├── academic_victims_capital_profit.png
│   └── academic_research_extensions.png
│
└── paper/                     paper section (LaTeX + compiled PDF)
    └── sandwich_section_en.tex / .pdf    English version (ACM sigconf)
```

---

## Quick start

```bash
pip install -r requirements.txt

# Re-run cleaning (recall_wide.gz -> strong_clean), ~30 s
python3 pipeline/3_clean_strong.py

# Rebuild the paper anatomy figure, ~10 s
python3 pipeline/5_build_anatomy_figure.py

# Rebuild the 4 academic figures + stats JSON, ~5 s (cache hit)
python3 pipeline/4_build_academic_figures.py
```

All scripts use paths relative to the repo root, run them from `release/`.
No API key is needed for the shipped pipeline.

---

## Detection method

For each transaction in a Jito bundle, we extract per-wallet, per-asset signed
deltas from `tokenTransfers` and `nativeTransfers`:

$$\Delta_{b,x}(W, m) = \mathrm{inflow} - \mathrm{outflow}$$

### Step 1 — candidate detection (`1_detect_pattern_a.py`)

For every bundle of length ≥ 3, we enumerate ordered triples $(i, k, j)$ and
search for any wallet $W$ and asset pair $(b, q)$ such that:

- $W$ executes opposite-direction swaps over $(b, q)$ in $\mathrm{tx}_i$ and $\mathrm{tx}_j$;
- some other wallet $V \neq W$ in $\mathrm{tx}_k$ moves the quote leg in the
  same direction as $W$'s front leg (the victim).

**Key design choice.** We do **not** require
$\mathrm{feePayer}(\mathrm{tx}_i) = \mathrm{feePayer}(\mathrm{tx}_j)$. In the
sample, 99.7% of real attackers use a separate tipper wallet for the front
and back legs, so a signer-equality prefilter would discard the vast majority
of true positives.

### Step 2 — confidence tiers (`2_classify_recall.py`)

We assign tiers based on quote-inventory symmetry
$\mathrm{sym\_ratio} = |Q_f - Q_b| / \max(|Q_f|, |Q_b|)$ alone — profit sign
is reported but is never used as a validity filter, since losing sandwiches
are a central object of study.

| Tier     | sym_ratio | Meaning                              |
|----------|-----------|--------------------------------------|
| strong   | ≤ 0.10    | inventory almost fully unwound       |
| probable | ≤ 0.25    | tight symmetry                       |
| possible | ≤ 0.50    | loose symmetry                       |
| weak     | ≤ 1.00    | one-sided dump permitted             |

### Step 3 — cleaning (`3_clean_strong.py`)

- **R1 (self-sandwich).** Drop records where the attacker wallet appears as
  a victim signer or aligned wallet in the same bundle.
- **R2 (market-maker contamination).** Across all tiers, flag wallets with
  ≥ 200 bundles, two-sided ratio ≥ 0.30, and mean per-bundle profit
  ∈ ±0.01 SOL as AMM pool / MM bots and remove them.

After R1 + R2 we keep **11,591 strong sandwiches** (about 88% of the strong
tier before cleaning).

### Steps 4–5 — enrichment and visualisation

- Augment each record with per-tx `fee` and per-bundle Jito `tip_lamports`
  from the Helius cache, then derive unit-price profit, explicit cost,
  capital, and attacker concentration.
- Emit the four academic figures (`academic_*.png`) and the paper anatomy
  figure (`sandwich_anatomy_col_en.pdf`).

---

## Data fields

### `sandwiches_recall_wide.jsonl.gz` (one candidate per line)

```json
{
  "confidence": "strong",                // strong | probable | possible | weak
  "bundle_id": "<jito bundle id>",
  "slot": 405314000,
  "ts": 1773083308,
  "attacker": "<wallet>",                 // attacker identified by wallet flow
  "front_sig": "...", "back_sig": "...",  // front and back leg signatures
  "front_signer": "...", "back_signer": "...",
  "same_signer": false,                   // false for ~99.7% of records
  "i": 0, "j": 2,                         // positions in the bundle
  "base_mint": "So11...112",              // (SOL is usually the base)
  "quote_mint": "<token mint>",
  "direction": "buy",                     // attacker's front-leg direction
  "front_base": -0.45, "front_quote": +12345.6,
  "back_base":  +0.46, "back_quote": -12345.6,
  "sym_ratio": 0.0023,                    // quote-inventory symmetry
  "profit_base": 0.012,                   // net profit in base (usually SOL)
  "profit_sol": 0.012,
  "old_verdict": "sandwich_confirmed",
  "victims": [
    {"pos": 1, "sig": "...", "signer": "...",
     "aligned_wallet": "...", "victim_quote": +9876, "aligned": true}
  ]
}
```

### `sandwiches_strong_clean.jsonl`

The 11,591 strong sandwiches that survive R1 + R2; same schema.

### `strong_sandwich_enriched.jsonl`

Strong tier with unit-price profit, explicit cost, capital, etc.; full schema
in `pipeline/4_build_academic_figures.py:enrich()`.

---

## Headline statistics

> ⚠️ Two reporting bases. The **academic figures** (step 4) report the
> property decomposition over the **uncleaned strong tier** (n = 13,165),
> because property questions ("does price edge equal realised P&L?") are
> tier-internal and benefit from a larger sample. The **paper anatomy
> figure** (step 5) and the paper text report the **R1+R2 cleaned sample**
> (n = 11,591), because attacker-concentration and bot-side conclusions
> require AMM-pool artifacts to be removed.

**Uncleaned strong tier (n = 13,165)** — from `data/strong_academic_stats.json`:

| Metric                                 | Value                          |
|----------------------------------------|--------------------------------|
| unit-price profitable                  | 77.5% (10,203 / 13,165)        |
| total-profit profitable (SOL > 0)      | 38.4% (5,060 / 13,165)         |
| gross gain / gross loss / net          | +354.81 / −548.46 / −193.65 SOL|
| total explicit cost (fee + tip)        | 0.287 SOL (≪ realised loss)    |
| multi-victim bundles                   | 0.81%                          |
| corr(log capital, profit)              | −0.070                         |
| corr(log capital, log\|profit\|)       | +0.690                         |
| attacker wallets                       | 172                            |

**R1 + R2 cleaned sample (n = 11,591)** — paper headline base:

| Metric                                 | Value                          |
|----------------------------------------|--------------------------------|
| total-profit profitable                | 36.3% (4,207 / 11,591)         |
| attacker wallets                       | 161                            |
| unique slots covered                   | 12,916                         |
| top-10 attackers' attack share         | 56.8%                          |
| relative to prior Solana Pattern-A     | ~2 orders of magnitude more    |

---

## Limitations

1. **Observation window.** Bundles were collected from the Jito history API
   for the period 2026-03-06 ~ 03-25; the structure of the live market may
   differ.
2. **Pattern A only.** We cover the single-bundle [front, victim, back]
   shape. Cross-bundle (Pattern B) and cross-slot variants are out of scope.
3. **Raw tx cache not shipped.** `txs_1000_joined.jsonl` (the 6 GB+ Helius
   cache) is not included in this bundle, so step 1 cannot be re-run from
   scratch as-shipped. Step 4 hits the shipped enriched cache and works
   without the raw cache. To rebuild from raw, configure `.env` (see
   `.env.example`) with a Helius key and run the upstream collector.
4. **MM heuristic is empirical.** R2 thresholds (≥ 200 bundles, ≥ 0.30
   two-sided ratio, ±0.01 SOL mean profit) are tunable; rerun
   `3_clean_strong.py` after editing the constants if you want a different
   cut.

---

## Citing

If you use this dataset or pipeline in academic work, please cite this
repository (replace with the project DOI / Git URL once published). The
paper text and figure live in `paper/sandwich_section_en.{tex,pdf}`.

---

## License

MIT — see [LICENSE](LICENSE).
