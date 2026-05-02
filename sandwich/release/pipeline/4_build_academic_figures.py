#!/usr/bin/env python3
"""Step 4 — Build academic figures + interpretation text from the strong tier.

Inputs:
  data/sandwiches_recall_wide.jsonl       (full recall set, used to pick strong)
  data/strong_sandwich_enriched.jsonl     (cached, shipped — see note below)

Outputs:
  data/strong_academic_stats.json
  data/strong_academic_interpretation.md
  figures/academic_price_vs_total_profit.png
  figures/academic_fee_tip_costs.png
  figures/academic_victims_capital_profit.png
  figures/academic_research_extensions.png

NOTE on the enriched cache:
  Building strong_sandwich_enriched.jsonl from scratch requires per-tx fee +
  per-bundle Jito tip lookups against the raw 6 GB+ Helius cache
  (data/tx_cache/txs_1000_joined.jsonl), which is NOT shipped here. The
  cache file IS shipped (~17 MB), so this script will hit the cache path
  and rebuild figures + stats from it. To rebuild the cache yourself, you
  need access to the upstream Helius cache.
"""

from __future__ import annotations

import gzip
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
SOL = "So11111111111111111111111111111111111111112"
RECALL = ROOT / "data" / "sandwiches_recall_wide.jsonl"
TXS = ROOT / "data" / "tx_cache" / "txs_1000_joined.jsonl"
OUT = ROOT / "data" / "strong_sandwich_enriched.jsonl"
STATS = ROOT / "data" / "strong_academic_stats.json"
TEXT = ROOT / "data" / "strong_academic_interpretation.md"
FIG = ROOT / "figures"
EXPECTED_N = 13165  # number of strong records after the upstream pipeline


def open_text(path):
    if not path.exists() and path.suffix != ".gz":
        gz_path = Path(str(path) + ".gz")
        if gz_path.exists():
            path = gz_path
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else open(path, encoding="utf-8")


def q(values, p):
    if not values:
        return None
    a = sorted(values)
    return a[int((len(a) - 1) * p)]


def load_strong():
    rows = []
    wanted_sigs = set()
    with open_text(RECALL) as f:
        for line in f:
            r = json.loads(line)
            if r.get("confidence") != "strong":
                continue
            r["_idx"] = len(rows)
            rows.append(r)
            wanted_sigs.add(r["front_sig"])
            wanted_sigs.add(r["back_sig"])
            for v in r.get("victims") or []:
                sig = v.get("sig")
                if sig:
                    wanted_sigs.add(sig)
    return rows, wanted_sigs


def scan_tx_metadata(wanted_sigs):
    cached = {}
    size = TXS.stat().st_size
    read = 0
    n = 0
    t0 = time.time()
    with TXS.open("rb", buffering=1 << 20) as f:
        for line in f:
            read += len(line)
            n += 1
            try:
                r = json.loads(line)
            except Exception:
                continue
            sig = r.get("signature")
            if sig in wanted_sigs:
                b = r.get("_bundle") or {}
                cached[sig] = {
                    "fee_lamports": r.get("fee") or 0,
                    "source": r.get("source"),
                    "type": r.get("type"),
                    "feePayer": r.get("feePayer"),
                    "bundle_id": b.get("bundle_id"),
                    "bundle_pos": b.get("bundle_tx_pos"),
                    "tip_lamports": b.get("tip_lamports") or 0,
                    "landed_cu": b.get("landed_cu") or 0,
                    "validator": b.get("validator"),
                }
                if len(cached) == len(wanted_sigs):
                    break
            if n % 500_000 == 0:
                pct = read / size * 100
                rate = n / max(time.time() - t0, 1e-9)
                print(
                    f"scan txs {n:,} lines {pct:5.1f}% "
                    f"found={len(cached):,}/{len(wanted_sigs):,} rate={rate:,.0f}/s",
                    file=sys.stderr, flush=True,
                )
    return cached


def sol_and_token_legs(r):
    if r.get("base_mint") == SOL:
        front_sol = r.get("front_base"); back_sol = r.get("back_base")
        front_token = r.get("front_quote"); back_token = r.get("back_quote")
        token_mint = r.get("quote_mint")
    elif r.get("quote_mint") == SOL:
        front_sol = r.get("front_quote"); back_sol = r.get("back_quote")
        front_token = r.get("front_base"); back_token = r.get("back_base")
        token_mint = r.get("base_mint")
    else:
        front_sol = back_sol = front_token = back_token = None
        token_mint = None
    return front_sol, back_sol, front_token, back_token, token_mint


def enrich(rows, meta):
    enriched = []
    missing_fee = 0
    for r in rows:
        front_sol, back_sol, front_token, back_token, token_mint = sol_and_token_legs(r)
        if front_sol is None or back_sol is None or not front_token or not back_token:
            continue
        profit_sol = front_sol + back_sol
        capital_sol = abs(front_sol)
        front_unit = abs(front_sol) / abs(front_token) if abs(front_token) > 0 else None
        back_unit = abs(back_sol) / abs(back_token) if abs(back_token) > 0 else None
        if front_unit is None or back_unit is None:
            continue
        if front_sol < 0:
            unit_profit = back_unit - front_unit
            side = "buy_then_sell"
        else:
            unit_profit = front_unit - back_unit
            side = "sell_then_buy"

        fm = meta.get(r["front_sig"]) or {}
        bm = meta.get(r["back_sig"]) or {}
        if not fm or not bm:
            missing_fee += 1
        fee_lamports = (fm.get("fee_lamports") or 0) + (bm.get("fee_lamports") or 0)
        tip_lamports = max(fm.get("tip_lamports") or 0, bm.get("tip_lamports") or 0)
        fee_sol = fee_lamports / 1e9
        tip_sol = tip_lamports / 1e9
        explicit_cost_sol = fee_sol + tip_sol

        victims = r.get("victims") or []
        rec = {
            "bundle_id": r.get("bundle_id"),
            "slot": r.get("slot"),
            "attacker": r.get("attacker"),
            "front_sig": r.get("front_sig"),
            "back_sig": r.get("back_sig"),
            "front_signer": r.get("front_signer"),
            "back_signer": r.get("back_signer"),
            "same_signer": r.get("same_signer"),
            "base_mint": r.get("base_mint"),
            "quote_mint": r.get("quote_mint"),
            "token_mint": token_mint,
            "side": side,
            "front_sol": front_sol,
            "back_sol": back_sol,
            "front_token": front_token,
            "back_token": back_token,
            "front_unit_sol_per_token": front_unit,
            "back_unit_sol_per_token": back_unit,
            "unit_profit_sol_per_token": unit_profit,
            "unit_profitable": unit_profit > 0,
            "profit_sol": profit_sol,
            "total_profitable": profit_sol > 0,
            "capital_sol": capital_sol,
            "fee_sol": fee_sol,
            "tip_sol": tip_sol,
            "explicit_cost_sol": explicit_cost_sol,
            "cost_to_capital": explicit_cost_sol / capital_sol if capital_sol > 0 else None,
            "cost_to_abs_profit": explicit_cost_sol / abs(profit_sol) if profit_sol != 0 else None,
            "victim_count": len(victims),
            "sym_ratio": r.get("sym_ratio"),
            "front_source": fm.get("source"),
            "back_source": bm.get("source"),
            "landed_cu": max(fm.get("landed_cu") or 0, bm.get("landed_cu") or 0),
            "validator": fm.get("validator") or bm.get("validator"),
        }
        enriched.append(rec)
    with OUT.open("w") as f:
        for r in enriched:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"enriched={len(enriched):,} missing_fee_meta={missing_fee:,}")
    return enriched


def compute_stats(rows):
    n = len(rows)
    unit_pos = sum(r["unit_profitable"] for r in rows)
    total_pos = sum(r["total_profitable"] for r in rows)
    both = Counter((r["unit_profitable"], r["total_profitable"]) for r in rows)
    multi_victim = sum(r["victim_count"] > 1 for r in rows)
    fees = [r["fee_sol"] for r in rows]
    tips = [r["tip_sol"] for r in rows]
    costs = [r["explicit_cost_sol"] for r in rows]
    profits = [r["profit_sol"] for r in rows]
    caps = [r["capital_sol"] for r in rows]
    cost_cap = [r["cost_to_capital"] for r in rows if r["cost_to_capital"] is not None]
    cost_abs_profit = [
        min(r["cost_to_abs_profit"], 10)
        for r in rows
        if r["cost_to_abs_profit"] is not None and math.isfinite(r["cost_to_abs_profit"])
    ]
    by_bot = defaultdict(lambda: {"n": 0, "profit": 0.0, "capital": 0.0})
    for r in rows:
        b = by_bot[r["attacker"]]
        b["n"] += 1
        b["profit"] += r["profit_sol"]
        b["capital"] += r["capital_sol"]

    corr_cap_profit = float(np.corrcoef(np.log10(np.array(caps) + 1e-9), profits)[0, 1])
    abs_profit = [abs(p) for p in profits]
    corr_cap_abs_profit = float(
        np.corrcoef(np.log10(np.array(caps) + 1e-9), np.log10(np.array(abs_profit) + 1e-9))[0, 1]
    )
    stats = {
        "n": n,
        "unit_profitable": unit_pos,
        "unit_profitable_share": unit_pos / n,
        "total_profitable": total_pos,
        "total_profitable_share": total_pos / n,
        "unit_total_cross": {f"{k[0]}_{k[1]}": v for k, v in both.items()},
        "multi_victim": multi_victim,
        "multi_victim_share": multi_victim / n,
        "victim_count_distribution": dict(Counter(r["victim_count"] for r in rows)),
        "profit_sol": {
            "gross_gain": sum(p for p in profits if p > 0),
            "gross_loss_abs": -sum(p for p in profits if p <= 0),
            "sum": sum(profits),
            "mean": sum(profits) / n,
            "median": q(profits, 0.5),
            "p10": q(profits, 0.1),
            "p90": q(profits, 0.9),
        },
        "capital_sol": {
            "sum": sum(caps),
            "mean": sum(caps) / n,
            "median": q(caps, 0.5),
            "p90": q(caps, 0.9),
        },
        "fee_tip": {
            "fee_sum_sol": sum(fees),
            "tip_sum_sol": sum(tips),
            "explicit_cost_sum_sol": sum(costs),
            "explicit_cost_mean_sol": sum(costs) / n,
            "cost_to_capital_median": q(cost_cap, 0.5),
            "cost_to_capital_p90": q(cost_cap, 0.9),
            "cost_to_abs_profit_median_capped10": q(cost_abs_profit, 0.5),
        },
        "capital_profit_corr_logcap_profit": corr_cap_profit,
        "capital_absprofit_corr_loglog": corr_cap_abs_profit,
        "bot_count": len(by_bot),
        "top_bot_attack_share": sum(v["n"] for v in sorted(by_bot.values(), key=lambda x: -x["n"])[:10]) / n,
        "top_bot_profit": sum(v["profit"] for v in sorted(by_bot.values(), key=lambda x: -x["n"])[:10]),
    }
    STATS.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


def setup_plot():
    plt.rcParams.update({
        "font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9,
        "legend.fontsize": 8, "figure.dpi": 160, "savefig.dpi": 300,
        "savefig.bbox": "tight", "axes.spines.top": False, "axes.spines.right": False,
    })


def fig_price_vs_total(rows):
    counts = Counter((r["unit_profitable"], r["total_profitable"]) for r in rows)
    mat = np.array([
        [counts[(True, True)], counts[(True, False)]],
        [counts[(False, True)], counts[(False, False)]],
    ])
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 2.65), gridspec_kw={"width_ratios": [1, 1.2]})
    ax = axes[0]
    ax.imshow(mat, cmap="Blues")
    ax.set_xticks([0, 1], ["total +", "total <=0"])
    ax.set_yticks([0, 1], ["unit +", "unit <=0"])
    for i in range(2):
        for j in range(2):
            ax.text(j, i, f"{mat[i,j]:,}\n{mat[i,j]/len(rows)*100:.1f}%",
                    ha="center", va="center")
    ax.set_title("Unit-price vs total-profit outcomes")

    profits = np.array([r["profit_sol"] for r in rows])
    unit_profit = np.array([r["unit_profit_sol_per_token"] for r in rows])
    colors = np.where(profits > 0, "#4267AC", "#B63E2B")
    ax = axes[1]
    idx = np.random.default_rng(42).choice(len(rows), min(len(rows), 5000), replace=False)
    ax.scatter(unit_profit[idx], profits[idx], s=5, alpha=0.35, c=colors[idx], edgecolors="none")
    ax.axhline(0, color="#333", lw=0.8, ls="--")
    ax.axvline(0, color="#333", lw=0.8, ls="--")
    ax.set_xscale("symlog", linthresh=1e-8)
    ax.set_ylim(np.percentile(profits, 1), np.percentile(profits, 99))
    ax.set_xlabel("unit price edge (SOL/token, signed)")
    ax.set_ylabel("total profit (SOL)")
    ax.set_title("Price edge is not identical to realised P&L")
    fig.tight_layout()
    fig.savefig(FIG / "academic_price_vs_total_profit.png")
    plt.close(fig)


def fig_fee_tip_cost(rows):
    fees = sum(r["fee_sol"] for r in rows)
    tips = sum(r["tip_sol"] for r in rows)
    gross_gain = sum(r["profit_sol"] for r in rows if r["profit_sol"] > 0)
    gross_loss = -sum(r["profit_sol"] for r in rows if r["profit_sol"] <= 0)
    cost_cap = np.array([r["cost_to_capital"] for r in rows if r["cost_to_capital"] is not None])
    cost_abs = np.array([
        r["cost_to_abs_profit"] for r in rows
        if r["cost_to_abs_profit"] is not None and math.isfinite(r["cost_to_abs_profit"])
    ])
    cost_abs = np.clip(cost_abs, 0, 1)

    fig, axes = plt.subplots(1, 3, figsize=(7.3, 2.55))
    ax = axes[0]
    vals = [gross_gain, gross_loss, fees, tips]
    labels = ["gross gains", "gross losses", "tx fees", "Jito tips"]
    colors = ["#4267AC", "#B63E2B", "#D08A2D", "#777777"]
    ax.bar(range(4), vals, color=colors)
    ax.set_xticks(range(4), labels, rotation=35, ha="right")
    ax.set_ylabel("SOL")
    ax.set_yscale("log")
    ax.set_title("Explicit fees are orders smaller than P&L")
    for i, v in enumerate(vals):
        ax.text(i, v * 1.12, f"{v:.3g}", ha="center", va="bottom", fontsize=8)

    ax = axes[1]
    cc_bp = cost_cap * 1e4
    cc_bp = cc_bp[cc_bp > 0]
    ax.hist(np.clip(cc_bp, 1e-3, np.percentile(cc_bp, 99.5)), bins=60, color="#4267AC", alpha=0.86)
    ax.set_xscale("log")
    ax.set_xlabel("explicit cost / capital (bp)")
    ax.set_ylabel("attacks")
    ax.set_title("Cost intensity per capital")
    ax.axvline(np.median(cc_bp), color="#B63E2B", lw=1.2)

    ax = axes[2]
    ca_pct = np.clip(cost_abs * 100, 1e-4, 100)
    ax.hist(ca_pct, bins=np.logspace(-4, 2, 70), color="#D08A2D", alpha=0.86)
    ax.set_xscale("log")
    ax.set_xlabel("explicit cost / |profit| (%) capped at 100")
    ax.set_title("Small costs matter only near zero")
    ax.axvline(np.median(ca_pct), color="#B63E2B", lw=1.2)
    fig.tight_layout()
    fig.savefig(FIG / "academic_fee_tip_costs.png")
    plt.close(fig)


def fig_victims_capital(rows):
    vc = Counter(r["victim_count"] for r in rows)
    caps = np.array([r["capital_sol"] for r in rows])
    profits = np.array([r["profit_sol"] for r in rows])

    fig, axes = plt.subplots(1, 3, figsize=(7.3, 2.55))
    ax = axes[0]
    labels = [str(k) for k in sorted(vc)]
    vals = [vc[k] for k in sorted(vc)]
    ax.bar(labels, vals, color="#4267AC")
    ax.set_xlabel("victims per bundle")
    ax.set_ylabel("attacks")
    ax.set_title("Most bundles have one victim")
    for i, v in enumerate(vals):
        ax.text(i, v, f"{v/len(rows)*100:.1f}%", ha="center", va="bottom", fontsize=8)

    ax = axes[1]
    idx = np.random.default_rng(7).choice(len(rows), min(len(rows), 6000), replace=False)
    ax.scatter(caps[idx], profits[idx], s=5, alpha=0.3, color="#4267AC", edgecolors="none")
    ax.axhline(0, color="#333", lw=0.8, ls="--")
    ax.set_xscale("log")
    ax.set_ylim(np.percentile(profits, 1), np.percentile(profits, 99))
    ax.set_xlabel("capital (SOL, log)")
    ax.set_ylabel("profit (SOL)")
    ax.set_title("Capital scales dispersion")

    ax = axes[2]
    deciles = np.array_split(sorted(rows, key=lambda r: r["capital_sol"]), 10)
    x = np.arange(1, 11)
    mean_abs_profit = [np.mean([abs(r["profit_sol"]) for r in d]) for d in deciles]
    mean_cost = [np.mean([r["explicit_cost_sol"] for r in d]) for d in deciles]
    ax.plot(x, mean_abs_profit, marker="o", label="|profit|", color="#4267AC")
    ax.plot(x, mean_cost, marker="s", label="fee+tip", color="#D08A2D")
    ax.set_yscale("log")
    ax.set_xlabel("capital decile")
    ax.set_ylabel("SOL, log")
    ax.set_title("Explicit costs do not scale like risk")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / "academic_victims_capital_profit.png")
    plt.close(fig)


def fig_research_extensions(rows):
    by_bot = defaultdict(lambda: {"n": 0, "profit": 0.0, "unit_pos": 0})
    by_slot = Counter()
    by_token = defaultdict(lambda: {"n": 0, "profit": 0.0})
    for r in rows:
        b = by_bot[r["attacker"]]
        b["n"] += 1
        b["profit"] += r["profit_sol"]
        b["unit_pos"] += r["unit_profitable"]
        by_slot[r["slot"]] += 1
        t = by_token[r["token_mint"]]
        t["n"] += 1
        t["profit"] += r["profit_sol"]

    bot_vals = list(by_bot.values())
    token_vals = sorted(by_token.values(), key=lambda x: -x["n"])[:15]
    fig, axes = plt.subplots(1, 3, figsize=(7.3, 2.55))

    ax = axes[0]
    ax.scatter([v["n"] for v in bot_vals], [v["profit"] for v in bot_vals],
               s=14, alpha=0.65, color="#4267AC", edgecolors="none")
    ax.axhline(0, color="#333", lw=0.8, ls="--")
    ax.set_xscale("log")
    ax.set_xlabel("attacks per bot (log)")
    ax.set_ylabel("bot aggregate profit")
    ax.set_title("Does skill survive repetition?")

    ax = axes[1]
    slot_counts = Counter(by_slot.values())
    labels = [str(k) for k in sorted(slot_counts)]
    vals = [slot_counts[k] for k in sorted(slot_counts)]
    ax.bar(labels, vals, color="#777777")
    ax.set_yscale("log")
    ax.set_xlabel("strong attacks in a slot")
    ax.set_ylabel("slots (log)")
    ax.set_title("Is sandwiching episodic or continuous?")

    ax = axes[2]
    ax.scatter([v["n"] for v in token_vals], [v["profit"] for v in token_vals],
               s=30, color="#B63E2B", alpha=0.75)
    ax.axhline(0, color="#333", lw=0.8, ls="--")
    ax.set_xscale("log")
    ax.set_xlabel("attacks per token (log)")
    ax.set_ylabel("token aggregate profit")
    ax.set_title("Are losses token-specific?")
    fig.tight_layout()
    fig.savefig(FIG / "academic_research_extensions.png")
    plt.close(fig)


def write_text(stats):
    lines = []
    lines.append("# Strong-tier sandwich attacks: figure interpretation\n")
    lines.append("## Direct answers\n")
    lines.append(
        f"1. **Unit-price profitable**: {stats['unit_profitable']:,}/{stats['n']:,} "
        f"({stats['unit_profitable_share']:.1%}). Unit-price profit is the per-token "
        f"execution-price improvement between the front leg and the back leg "
        f"(SOL/token)."
    )
    lines.append(
        f"2. **Total-profit profitable**: {stats['total_profitable']:,}/{stats['n']:,} "
        f"({stats['total_profitable_share']:.1%}). Gross gains "
        f"{stats['profit_sol']['gross_gain']:.2f} SOL, gross losses "
        f"-{stats['profit_sol']['gross_loss_abs']:.2f} SOL, net "
        f"{stats['profit_sol']['sum']:.2f} SOL."
    )
    lines.append(
        f"3. **Tip and fee cost share**: total tx fees "
        f"{stats['fee_tip']['fee_sum_sol']:.6f} SOL, total Jito tips "
        f"{stats['fee_tip']['tip_sum_sol']:.6f} SOL, total explicit cost "
        f"{stats['fee_tip']['explicit_cost_sum_sol']:.6f} SOL; median "
        f"explicit-cost / capital is "
        f"{stats['fee_tip']['cost_to_capital_median']*1e4:.3f} bp."
    )
    lines.append(
        f"4. **Multi-victim bundles**: yes, {stats['multi_victim']:,} "
        f"({stats['multi_victim_share']:.2%}) strong sandwiches have more than one "
        f"victim; the vast majority are single-victim."
    )
    lines.append(
        f"5. **Cost / profit / capital relationships**: corr(log(capital), profit) = "
        f"{stats['capital_profit_corr_logcap_profit']:.3f}; "
        f"corr(log(capital), log|profit|) = "
        f"{stats['capital_absprofit_corr_loglog']:.3f}. Capital looks more like "
        f"risk exposure than a guarantee of steady return."
    )
    lines.append("\n## Extended research questions\n")
    lines.append("- **Why do most attacks have unit-price improvement but only a minority realise SOL profit?** See `academic_price_vs_total_profit.png`. There is a gap between price edge and realised P&L driven by inventory size, refill mismatch, and execution error.")
    lines.append("- **Do explicit fees and tips explain the losses?** See `academic_fee_tip_costs.png`. No: explicit costs are orders of magnitude smaller than realised losses.")
    lines.append("- **Are multi-victim bundles the dominant structure?** See `academic_victims_capital_profit.png`. No: the vast majority of bundles target a single victim.")
    lines.append("- **Does capital deliver return or just risk?** Same figure: larger capital widens P&L dispersion, while explicit cost stays roughly flat in size.")
    lines.append("- **Do bots exhibit persistent skill? Do token or slot effects explain losses?** See `academic_research_extensions.png`.")
    TEXT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    setup_plot()
    rows, wanted = load_strong()

    enriched = None
    if OUT.exists():
        try:
            enriched = [json.loads(line) for line in OUT.open()]
            if len(enriched) != EXPECTED_N:
                print(f"cached enriched has {len(enriched)} != {EXPECTED_N}, rebuilding",
                      file=sys.stderr)
                enriched = None
        except Exception:
            enriched = None

    if enriched is None:
        if not TXS.exists():
            print(f"ERROR: enriched cache missing and {TXS} not present.", file=sys.stderr)
            print("The raw 6 GB+ Helius cache is required to rebuild from scratch.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"strong rows={len(rows):,}; target sigs={len(wanted):,}")
        meta = scan_tx_metadata(wanted)
        enriched = enrich(rows, meta)

    stats = compute_stats(enriched)
    fig_price_vs_total(enriched)
    fig_fee_tip_cost(enriched)
    fig_victims_capital(enriched)
    fig_research_extensions(enriched)
    write_text(stats)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
