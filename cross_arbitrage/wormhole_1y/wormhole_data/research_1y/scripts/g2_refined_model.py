#!/usr/bin/env python3
"""
g2_refined_model.py — fit the refined cross-chain risk model.

Refinements relative to §6.2:
  (R1) linear slippage:    S(x) = θ_lin · x
  (R2) plateau-exp φ(Δ) :  φ(Δ) = p∞ + (p0 - p∞) · e^{-λ(Δ - Δmin)}
  (R3) per-token σ:        σ_token = sd(ln(P_eth / P_sol)) within each token

Then compute:
  (P1) predicted net PnL:  Π̂ = x · g_signed · exit_coverage · φ(Δ)
                              - θ_lin · x - C_exec
  (P2) predicted P(Loss) via log-normal with σ_token.

Outputs:
  figs/08_refined_validation.png
  tables/g24_refined_coefficients.csv
  tables/g25_refined_pnl_r2.csv
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.stats import norm

import common_loader as cl

plt.rcParams.update(cl.MPL_STYLE)
P = cl.PALETTE
FIGS = cl.FIGS
TABLES = cl.TABLES
CACHE = Path(__file__).resolve().parents[1] / "cache"


CHAI_OUTLIER_ID = "SOL→ETH|1ePEb73HZdp36NN5|0xf0c49b73833ca7"

_OUTLIER_FILE = Path(__file__).resolve().parent / "excluded_outliers.json"
if _OUTLIER_FILE.exists():
    _o = json.load(open(_OUTLIER_FILE))
    EXCLUDED_IDS = set(_o.get("worst10", [])) | set(_o.get("best10", []))
else:
    EXCLUDED_IDS = {CHAI_OUTLIER_ID}


def load_frame() -> pd.DataFrame:
    df = cl.load_df()
    df = df[df["is_reliable"]].copy()
    df = df[~df["id"].isin(EXCLUDED_IDS)].copy()
    gap = json.load(open(CACHE / "entry_gap_cache.json"))

    def _key(r):
        ss = (r["sol_sig"] or "")[:16]
        eh = (r["eth_hash"] or "")[:16]
        return f"{r['direction']}|{ss}|{eh}"

    df["entry_gap_pct"] = df.apply(lambda r: gap.get(_key(r)), axis=1)

    # also try to recover exit_coverage if present in original JSON
    # already in df via common_loader
    for c in ["sol_liq_usd", "eth_liq_usd", "entry_cost_usd", "exit_value_usd",
              "gross_pnl_usd", "net_pnl_usd", "total_fee_usd", "time_diff_sec",
              "roi_pct", "entry_gap_pct", "exit_coverage", "entry_coverage"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["size_usd"] = df["entry_cost_usd"]
    return df


def fit_linear_slippage(df):
    """
    Per-direction linear slippage: shortfall = α + θ · x.
    """
    out = []
    for d in ["SOL→ETH", "ETH→SOL"]:
        s = df[df["direction"] == d].dropna(
            subset=["size_usd", "entry_gap_pct", "gross_pnl_usd"]
        ).copy()
        s = s[s["size_usd"] > 0]
        s["shortfall"] = s["size_usd"] * s["entry_gap_pct"] / 100.0 - s["gross_pnl_usd"]
        # trim extremes
        q = s["shortfall"].quantile([0.01, 0.99])
        s = s[s["shortfall"].between(q.iloc[0], q.iloc[1])]
        X = s[["size_usd"]].values
        y = s["shortfall"].values
        # OLS with intercept
        A = np.column_stack([np.ones(len(X)), X[:, 0]])
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        alpha, theta = float(coef[0]), float(coef[1])
        yhat = A @ coef
        r2 = 1 - np.sum((y - yhat) ** 2) / np.sum((y - y.mean()) ** 2)
        out.append({"direction": d, "alpha": alpha, "theta_lin": theta,
                    "r2": float(r2), "n": len(s)})
    return pd.DataFrame(out)


def fit_plateau_exp(df):
    """
    φ(Δ) = p∞ + (p0 - p∞) · exp(-λ (Δ - Δmin))
    """
    s = df.dropna(subset=["time_diff_sec", "roi_pct"]).copy()
    s = s[s["time_diff_sec"].between(10, 3600)]
    s["profitable"] = (s["roi_pct"] > 0).astype(int)

    edges = np.logspace(np.log10(10), np.log10(3600), 21)
    s["dbucket"] = pd.cut(s["time_diff_sec"], bins=edges, include_lowest=True)
    agg = (s.groupby("dbucket")
             .agg(delta=("time_diff_sec", "median"),
                  n=("time_diff_sec", "size"),
                  p_profit=("profitable", "mean"))
             .dropna().reset_index(drop=True))
    agg = agg[agg["n"] >= 30]

    dmin = agg["delta"].min()
    def model(d, p_inf, p0, lam):
        return p_inf + (p0 - p_inf) * np.exp(-lam * (d - dmin))

    popt, _ = curve_fit(model, agg["delta"], agg["p_profit"],
                        p0=[0.70, 0.80, 1e-3],
                        bounds=([0, 0, 0], [1, 1, 1]),
                        maxfev=20000)
    p_inf, p0, lam = popt
    agg["p_fit"] = model(agg["delta"], *popt)
    ss_res = np.sum((agg["p_profit"] - agg["p_fit"]) ** 2)
    ss_tot = np.sum((agg["p_profit"] - agg["p_profit"].mean()) ** 2)
    r2 = 1 - ss_res / ss_tot
    return {"p_inf": float(p_inf), "p0": float(p0), "lam": float(lam),
            "dmin": float(dmin), "r2": float(r2), "agg": agg}


def per_token_sigma(df):
    """σ_token = sd(entry_gap_pct/100) within each token_key."""
    s = df.dropna(subset=["entry_gap_pct", "token_key"]).copy()
    s["g"] = s["entry_gap_pct"] / 100.0
    sig = (s.groupby("token_key")
             .agg(sigma_token=("g", "std"),
                  n=("g", "size"))
             .reset_index())
    sig = sig[sig["n"] >= 5]
    return sig


def predict_pnl(df, slip_tbl, phi, sig_map):
    s = df.dropna(subset=["size_usd", "entry_gap_pct", "time_diff_sec",
                          "exit_coverage", "net_pnl_usd", "total_fee_usd",
                          "token_key"]).copy()
    s = s[s["size_usd"] > 0]
    # merge direction slippage
    slip = slip_tbl.set_index("direction")[["alpha", "theta_lin"]].to_dict("index")
    s["alpha"]   = s["direction"].map(lambda d: slip.get(d, {"alpha": 0})["alpha"])
    s["theta"]   = s["direction"].map(lambda d: slip.get(d, {"theta_lin": 0})["theta_lin"])
    # plateau-exp φ
    dmin = phi["dmin"]
    s["phi"] = phi["p_inf"] + (phi["p0"] - phi["p_inf"]) * \
               np.exp(-phi["lam"] * np.clip(s["time_diff_sec"] - dmin, 0, None))
    # refined prediction
    s["pi_hat"] = (s["size_usd"] * s["entry_gap_pct"] / 100.0
                   * s["exit_coverage"] * s["phi"]
                   - s["alpha"]
                   - s["theta"] * s["size_usd"]
                   - s["total_fee_usd"])
    # trim extremes for regression evaluation
    q = s["net_pnl_usd"].quantile([0.01, 0.99])
    s = s[s["net_pnl_usd"].between(q.iloc[0], q.iloc[1])]
    q = s["pi_hat"].quantile([0.01, 0.99])
    s = s[s["pi_hat"].between(q.iloc[0], q.iloc[1])]

    y = s["net_pnl_usd"].values
    yhat = s["pi_hat"].values
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot
    corr = float(np.corrcoef(y, yhat)[0, 1])

    # loss probability with per-token sigma
    st = s.merge(sig_map, on="token_key", how="left")
    st["sigma_token"] = st["sigma_token"].fillna(sig_map["sigma_token"].median())
    st["mu"] = 0.0
    C = st["total_fee_usd"].values
    R = np.abs(st["size_usd"].values * st["entry_gap_pct"].values / 100.0) + 1e-9
    D = st["time_diff_sec"].values
    sig = st["sigma_token"].values
    num = np.log(C / R) - (st["mu"] - sig ** 2 / 2) * D
    den = sig * np.sqrt(D)
    st["p_loss_ref"] = norm.cdf(num / den)
    st["is_loss"] = (st["net_pnl_usd"] < 0).astype(int)

    # calibration
    st = st.dropna(subset=["p_loss_ref"])
    st["dec"] = pd.qcut(st["p_loss_ref"], 10, labels=False, duplicates="drop")
    cal = (st.groupby("dec")
             .agg(n=("p_loss_ref", "size"),
                  pred=("p_loss_ref", "median"),
                  obs=("is_loss", "mean"))
             .reset_index())
    slope, intercept = np.polyfit(cal["pred"], cal["obs"], 1)
    yhat2 = slope * cal["pred"] + intercept
    ss_r = np.sum((cal["obs"] - yhat2) ** 2)
    ss_t = np.sum((cal["obs"] - cal["obs"].mean()) ** 2)
    r2_c = 1 - ss_r / ss_t
    return {
        "pnl_frame": s,
        "cal_frame": cal,
        "r2_pnl":    float(r2),
        "corr_pnl":  corr,
        "slope_cal": float(slope),
        "int_cal":   float(intercept),
        "r2_cal":    float(r2_c),
    }


def make_figure(slip_tbl, phi, pnl):
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.3))

    # ---- A. linear slippage by direction
    ax = axes[0]
    for d, row in slip_tbl.set_index("direction").iterrows():
        ax.bar(d, row["theta_lin"] * 1e4,
               color=P["sol_to_eth"] if d == "SOL→ETH" else P["eth_to_sol"])
        ax.text(d, row["theta_lin"] * 1e4,
                f"  {row['theta_lin']*1e4:.1f} bps\n  n={int(row['n']):,}",
                va="bottom", ha="center", fontsize=8)
    ax.set_ylabel("linear slippage (bps of size)")
    ax.set_title("A.  Per-direction linear slippage\n$S(x)=\\alpha+\\theta\\,x$")

    # ---- B. plateau-exp φ
    ax = axes[1]
    ag = phi["agg"]
    ax.plot(ag["delta"], ag["p_profit"], "o", color=P["sol_to_eth"], ms=5,
            label="observed")
    dg = np.linspace(ag["delta"].min(), ag["delta"].max(), 200)
    yg = phi["p_inf"] + (phi["p0"] - phi["p_inf"]) * \
         np.exp(-phi["lam"] * (dg - phi["dmin"]))
    ax.plot(dg, yg, "-", color=P["negative"], lw=1.5,
            label=(f"fit: p∞={phi['p_inf']:.2f}, p0={phi['p0']:.2f}\n"
                   f"λ={phi['lam']:.2e}, R²={phi['r2']:.3f}"))
    ax.set_xscale("log")
    ax.set_xlabel(r"$\Delta$  (seconds, log scale)")
    ax.set_ylabel(r"$\phi(\Delta)$  (profitable fraction)")
    ax.set_title(r"B.  Plateau-exp: $\phi=p_\infty+(p_0-p_\infty)e^{-\lambda(\Delta-\Delta_{min})}$")
    ax.legend(loc="lower left", frameon=False)

    # ---- C. refined P(Loss) calibration
    ax = axes[2]
    ca = pnl["cal_frame"]
    ax.plot([0, 1], [0, 1], "--", color=P["neutral"], lw=1, label="perfect")
    ax.plot(ca["pred"], ca["obs"], "o-",
            color=P["negative"], lw=1.2, ms=5,
            label=(f"observed\nslope={pnl['slope_cal']:.2f}, "
                   f"int={pnl['int_cal']:.2f}\nR²={pnl['r2_cal']:.3f}"))
    ax.set_xlabel("predicted P(Loss)  (refined, per-token σ)")
    ax.set_ylabel("observed loss rate")
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_title("C.  Refined P(Loss) calibration")
    ax.legend(loc="upper left", frameon=False)

    fig.suptitle(f"Figure 8  —  Refined risk model  (N_pnl={len(pnl['pnl_frame']):,},"
                 f" PnL R²={pnl['r2_pnl']:.3f}, corr={pnl['corr_pnl']:.3f})",
                 fontsize=10, y=1.02)
    fig.tight_layout()
    out = FIGS / "08_refined_validation.png"
    fig.savefig(out, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {out}")


def main():
    df = load_frame()
    print(f"loaded {len(df):,} reliable rows")

    slip = fit_linear_slippage(df)
    print("\nR1. linear slippage per direction:")
    print(slip.to_string(index=False))

    phi = fit_plateau_exp(df)
    print(f"\nR2. plateau-exp φ:  p∞={phi['p_inf']:.3f}, p0={phi['p0']:.3f}, "
          f"λ={phi['lam']:.3e}, R²={phi['r2']:.3f}")

    sig = per_token_sigma(df)
    print(f"\nR3. per-token σ: {len(sig):,} tokens, "
          f"median σ={sig['sigma_token'].median():.4f}")

    pnl = predict_pnl(df, slip, phi, sig)
    print(f"\nPredicted net PnL: R²={pnl['r2_pnl']:.3f}, "
          f"corr={pnl['corr_pnl']:.3f}")
    print(f"Refined P(Loss) calibration: slope={pnl['slope_cal']:.3f}, "
          f"int={pnl['int_cal']:.3f}, R²={pnl['r2_cal']:.3f}")

    # save
    slip.to_csv(TABLES / "g24_refined_coefficients.csv", index=False)
    pd.DataFrame([{
        "r2_pnl":    pnl["r2_pnl"],
        "corr_pnl":  pnl["corr_pnl"],
        "slope_cal": pnl["slope_cal"],
        "int_cal":   pnl["int_cal"],
        "r2_cal":    pnl["r2_cal"],
        "n_pnl":     len(pnl["pnl_frame"]),
        "phi_p_inf": phi["p_inf"],
        "phi_p0":    phi["p0"],
        "phi_lam":   phi["lam"],
        "phi_r2":    phi["r2"],
        "sigma_median_token": float(sig["sigma_token"].median()),
    }]).to_csv(TABLES / "g25_refined_pnl_r2.csv", index=False)

    make_figure(slip, phi, pnl)
    print("\n[done]")


if __name__ == "__main__":
    main()
