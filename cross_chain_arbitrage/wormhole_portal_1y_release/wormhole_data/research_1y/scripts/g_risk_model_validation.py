#!/usr/bin/env python3
"""
g_risk_model_validation.py — empirical validation of the §6.2 theoretical risk
model against 1-year Wormhole Portal data.

Three tests:
  A. Slippage quadratic form:   S(x) = θ · x² / L · P_mid
     Regress |gross_shortfall|  on x²/L and compare with linear x model.
  B. Temporal decay:            φ(Δ) = e^(−λΔ)
     Survival of profitable trades over latency buckets; extract λ.
  C. Loss probability:          P(Loss) = Φ([ln(C/R) − (μ − σ²/2)Δ] / σ√Δ)
     Per-event predicted loss probability vs observed loss frequency.

Outputs:
  figs/07_risk_validation.png
  tables/g21_slippage_fit.csv
  tables/g22_phi_fit.csv
  tables/g23_ploss_calibration.csv
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.stats import norm
from scipy.optimize import curve_fit

import common_loader as cl

plt.rcParams.update(cl.MPL_STYLE)

FIGS = cl.FIGS
TABLES = cl.TABLES
CACHE = Path(__file__).resolve().parents[1] / "cache"

FIGS.mkdir(parents=True, exist_ok=True)
TABLES.mkdir(parents=True, exist_ok=True)

P = cl.PALETTE


# ------------------------------- load ------------------------------------
def load_frame() -> pd.DataFrame:
    df = cl.load_df()
    df = df[df["is_reliable"]].copy()

    # entry gap cache: key = "{direction}|{sol_sig[:16]}|{eth_hash[:14]}" → pct
    gap_path = CACHE / "entry_gap_cache.json"
    if gap_path.exists():
        gap = json.load(open(gap_path))
        def _key(r):
            ss = (r["sol_sig"] or "")[:16]
            eh = (r["eth_hash"] or "")[:16]
            return f"{r['direction']}|{ss}|{eh}"
        df["entry_gap_pct"] = df.apply(lambda r: gap.get(_key(r)), axis=1)
    else:
        df["entry_gap_pct"] = np.nan

    # numeric coercion
    for c in ["sol_liq_usd", "eth_liq_usd", "entry_cost_usd", "exit_value_usd",
              "gross_pnl_usd", "net_pnl_usd", "total_fee_usd", "time_diff_sec",
              "roi_pct", "entry_gap_pct"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["size_usd"] = df["entry_cost_usd"]
    df["min_liq"] = df[["sol_liq_usd", "eth_liq_usd"]].min(axis=1)
    return df


# -------- TEST A: slippage quadratic ------------------------------------
def test_slippage(df: pd.DataFrame):
    """
    gross_shortfall = (ideal gross from entry_gap) − observed gross_pnl.
    ideal = size * entry_gap_pct/100   (dollar alpha carried in at entry).
    Slippage should be positive (observed < ideal).
    H_quad: shortfall ∝ x² / L     (Eq. 6)
    H_lin : shortfall ∝ x
    """
    s = df.dropna(subset=["size_usd", "entry_gap_pct", "gross_pnl_usd",
                          "min_liq"]).copy()
    s = s[(s["size_usd"] > 0) & (s["min_liq"] > 0)]
    s["ideal_gross"] = s["size_usd"] * s["entry_gap_pct"] / 100.0
    s["shortfall"] = s["ideal_gross"] - s["gross_pnl_usd"]
    s = s[s["shortfall"].between(s["shortfall"].quantile(0.01),
                                 s["shortfall"].quantile(0.99))]
    s = s[s["size_usd"].between(s["size_usd"].quantile(0.01),
                                s["size_usd"].quantile(0.99))]
    s = s[s["min_liq"].between(s["min_liq"].quantile(0.01),
                               s["min_liq"].quantile(0.99))]

    x = s["size_usd"].values
    L = s["min_liq"].values
    y = s["shortfall"].values

    # Quadratic design x² / L
    Xq = (x ** 2) / L
    # Linear design x
    Xl = x

    def fit(X, y):
        X = X.reshape(-1, 1)
        # OLS slope (no intercept ⇒ structural zero-size zero-slippage)
        theta = float(np.linalg.lstsq(X, y, rcond=None)[0][0])
        yhat = X[:, 0] * theta
        ss_res = float(np.sum((y - yhat) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        return theta, r2

    theta_q, r2_q = fit(Xq, y)
    theta_l, r2_l = fit(Xl, y)

    # also report AIC-equivalent RSS ratio
    rss_q = float(np.sum((y - Xq * theta_q) ** 2))
    rss_l = float(np.sum((y - Xl * theta_l) ** 2))

    tbl = pd.DataFrame([
        {"model": "quadratic  shortfall = θ · x²/L",
         "theta": theta_q, "r2_structural": r2_q, "rss": rss_q,
         "implied_slippage_bps_at_x=1000,L=50k":
             1e4 * (theta_q * 1000 ** 2 / 50_000) / 1000},
        {"model": "linear     shortfall = θ · x",
         "theta": theta_l, "r2_structural": r2_l, "rss": rss_l,
         "implied_slippage_bps_at_x=1000,L=50k":
             1e4 * (theta_l * 1000) / 1000},
    ])
    tbl.to_csv(TABLES / "g21_slippage_fit.csv", index=False)

    # data for plot
    return {
        "scatter_x_quad":  Xq,
        "scatter_x_lin":   Xl,
        "y":               y,
        "theta_q":         theta_q,
        "theta_l":         theta_l,
        "r2_q":            r2_q,
        "r2_l":            r2_l,
        "table":           tbl,
    }


# -------- TEST B: φ(Δ) = e^(−λΔ) ----------------------------------------
def test_phi(df: pd.DataFrame):
    s = df.dropna(subset=["time_diff_sec", "roi_pct"]).copy()
    s = s[s["time_diff_sec"].between(10, 3600)]   # 10s ~ 1h window
    s["profitable"] = (s["roi_pct"] > 0).astype(int)

    # 20 log-spaced buckets
    edges = np.logspace(np.log10(s["time_diff_sec"].min()),
                        np.log10(s["time_diff_sec"].max()),
                        21)
    s["dbucket"] = pd.cut(s["time_diff_sec"], bins=edges, include_lowest=True)
    agg = (s.groupby("dbucket")
             .agg(delta=("time_diff_sec", "median"),
                  n=("time_diff_sec", "size"),
                  p_profit=("profitable", "mean"))
             .dropna()
             .reset_index(drop=True))
    agg = agg[agg["n"] >= 30]

    # Fit p_profit = A * exp(-λ Δ)
    def expmodel(d, A, lam):
        return A * np.exp(-lam * d)

    popt, _ = curve_fit(expmodel, agg["delta"], agg["p_profit"],
                        p0=[agg["p_profit"].iloc[0], 1e-4],
                        maxfev=10000)
    A_hat, lam_hat = popt
    agg["p_fit"] = expmodel(agg["delta"], A_hat, lam_hat)

    # R² of fit
    ss_res = float(np.sum((agg["p_profit"] - agg["p_fit"]) ** 2))
    ss_tot = float(np.sum((agg["p_profit"] - agg["p_profit"].mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    agg.to_csv(TABLES / "g22_phi_fit.csv", index=False)
    return {
        "agg":    agg,
        "A":      A_hat,
        "lam":    lam_hat,
        "r2":     r2,
        "half_life_sec": math.log(2) / lam_hat if lam_hat > 0 else float("nan"),
    }


# -------- TEST C: P(Loss) calibration -----------------------------------
def test_ploss(df: pd.DataFrame):
    """
    P(Loss) = Φ([ln(C/R) − (μ − σ²/2)Δ] / σ√Δ)

    Assumptions for estimation:
      • C = total_fee_usd  (execution friction; slippage already inside gross)
      • R = |gross_pnl_usd|  (realised gross)
      • μ ≈ 0  (arbitrage alpha is mean-zero modulo gap)
      • σ  = cross-sectional sd of per-token ln(P_eth / P_sol) proxied as
             sd of entry_gap_pct/100 across the reliable sample
      • Δ  = time_diff_sec

    Compute per-row predicted P(Loss), bin into deciles, compare to observed
    loss-frequency within each decile.
    """
    s = df.dropna(subset=["total_fee_usd", "gross_pnl_usd",
                          "time_diff_sec", "entry_gap_pct"]).copy()
    s = s[(s["total_fee_usd"] > 0) & (s["time_diff_sec"] > 0)]
    s = s[np.abs(s["gross_pnl_usd"]) > 1e-6]

    sigma = float(s["entry_gap_pct"].std() / 100.0)   # per-event gap vol
    mu = 0.0

    # numerator: ln(C / |R|) − (μ − σ²/2) Δ
    ratio = s["total_fee_usd"] / np.abs(s["gross_pnl_usd"])
    num = np.log(ratio) - (mu - sigma ** 2 / 2) * s["time_diff_sec"]
    den = sigma * np.sqrt(s["time_diff_sec"])
    s["p_loss_pred"] = norm.cdf(num / den)
    s["is_loss"] = (s["net_pnl_usd"] < 0).astype(int)

    # decile calibration
    s = s.dropna(subset=["p_loss_pred"])
    s["pred_decile"] = pd.qcut(s["p_loss_pred"], 10,
                               labels=False, duplicates="drop")
    cal = (s.groupby("pred_decile")
             .agg(n=("p_loss_pred", "size"),
                  pred_mid=("p_loss_pred", "median"),
                  observed_loss_rate=("is_loss", "mean"))
             .reset_index())

    # simple calibration quality: linear fit of observed ~ predicted
    X = cal["pred_mid"].values
    Y = cal["observed_loss_rate"].values
    slope, intercept = np.polyfit(X, Y, 1)
    # R²
    yhat = slope * X + intercept
    ss_res = float(np.sum((Y - yhat) ** 2))
    ss_tot = float(np.sum((Y - Y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    cal["sigma_used"] = sigma
    cal["slope_obs_vs_pred"] = slope
    cal["intercept"] = intercept
    cal["r2"] = r2
    cal.to_csv(TABLES / "g23_ploss_calibration.csv", index=False)
    return {
        "cal":       cal,
        "sigma":     sigma,
        "slope":     slope,
        "intercept": intercept,
        "r2":        r2,
    }


# -------- plot -----------------------------------------------------------
def make_figure(A, B, C):
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.2))

    # ----- Panel A: slippage quadratic scatter -----
    ax = axes[0]
    # Bin by x²/L deciles and plot median shortfall for readability
    dfp = pd.DataFrame({"xqL": A["scatter_x_quad"], "y": A["y"]})
    dfp["dec"] = pd.qcut(dfp["xqL"], 20, labels=False, duplicates="drop")
    bp = dfp.groupby("dec").agg(xqL_med=("xqL", "median"),
                                y_med=("y", "median"),
                                y_p25=("y", lambda v: v.quantile(0.25)),
                                y_p75=("y", lambda v: v.quantile(0.75))).reset_index()
    ax.fill_between(bp["xqL_med"], bp["y_p25"], bp["y_p75"],
                    alpha=0.18, color=P["sol_to_eth"], label="IQR (observed)")
    ax.plot(bp["xqL_med"], bp["y_med"], "o-",
            color=P["sol_to_eth"], lw=1.3, ms=4, label="median (observed)")
    # fitted line
    xg = np.linspace(bp["xqL_med"].min(), bp["xqL_med"].max(), 200)
    ax.plot(xg, A["theta_q"] * xg, "--", color=P["negative"], lw=1.5,
            label=f"fit: S = {A['theta_q']:.2e} · x²/L\n(R²_struct={A['r2_q']:.3f})")
    ax.set_xlabel(r"$x^{2}/L$  (USD)")
    ax.set_ylabel(r"shortfall $= S_{\mathrm{ideal}}-S_{\mathrm{gross}}$  (USD)")
    ax.set_title("A.  Slippage quadratic: $S(x)=\\theta\\,x^{2}/L\\,P$")
    ax.legend(loc="upper left", frameon=False)

    # ----- Panel B: φ(Δ) exponential -----
    ax = axes[1]
    ag = B["agg"]
    ax.plot(ag["delta"], ag["p_profit"], "o", color=P["sol_to_eth"], ms=5,
            label="P(profitable | Δ)  observed")
    dg = np.linspace(ag["delta"].min(), ag["delta"].max(), 200)
    ax.plot(dg, B["A"] * np.exp(-B["lam"] * dg), "-", color=P["negative"],
            lw=1.5,
            label=f"fit: $A e^{{-\\lambda\\Delta}}$\nA={B['A']:.2f}, λ={B['lam']:.2e}\n(t½≈{B['half_life_sec']:.0f}s, R²={B['r2']:.3f})")
    ax.set_xscale("log")
    ax.set_xlabel(r"$\Delta$  (seconds, log scale)")
    ax.set_ylabel(r"$\phi(\Delta)$  (profitable fraction)")
    ax.set_title(r"B.  Temporal decay: $\phi(\Delta)=e^{-\lambda\Delta}$")
    ax.legend(loc="lower left", frameon=False)

    # ----- Panel C: P(Loss) calibration -----
    ax = axes[2]
    ca = C["cal"]
    ax.plot([0, 1], [0, 1], "--", color=P["neutral"], lw=1, label="perfect calibration")
    ax.plot(ca["pred_mid"], ca["observed_loss_rate"], "o-",
            color=P["negative"], lw=1.2, ms=5,
            label=f"observed\nslope={C['slope']:.2f}, intercept={C['intercept']:.2f}\nR²={C['r2']:.3f}")
    ax.set_xlabel("predicted P(Loss)")
    ax.set_ylabel("observed loss rate")
    ax.set_title(f"C.  P(Loss) calibration  (σ={C['sigma']:.3f})")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper left", frameon=False)

    fig.suptitle("Figure 7  —  Empirical validation of the §6.2 cross-chain "
                 "MEV risk model  (1y Wormhole Portal, N={:,} reliable)".format(
                     len(A["y"])),
                 fontsize=10, y=1.02)
    fig.tight_layout()
    out = FIGS / "07_risk_validation.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out}")


# ------------------------------- main ------------------------------------
def main():
    df = load_frame()
    print(f"loaded {len(df):,} reliable rows, "
          f"{df['entry_gap_pct'].notna().sum():,} with entry_gap_pct")

    A = test_slippage(df)
    print("\nA. SLIPPAGE")
    print(A["table"].to_string(index=False))

    B = test_phi(df)
    print("\nB. TEMPORAL DECAY")
    print(f"  fit: p_profit = {B['A']:.3f} · exp(-{B['lam']:.3e} · Δ)")
    print(f"  half-life      = {B['half_life_sec']:.1f} s")
    print(f"  R²             = {B['r2']:.3f}")

    C = test_ploss(df)
    print("\nC. P(LOSS) CALIBRATION")
    print(C["cal"].to_string(index=False))
    print(f"  sigma used  = {C['sigma']:.4f}")
    print(f"  calib slope = {C['slope']:.3f}, intercept = {C['intercept']:.3f}")
    print(f"  R²          = {C['r2']:.3f}")

    make_figure(A, B, C)
    print("\n[done]")


if __name__ == "__main__":
    main()
