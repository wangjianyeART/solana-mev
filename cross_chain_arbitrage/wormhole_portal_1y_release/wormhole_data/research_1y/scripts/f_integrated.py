#!/usr/bin/env python3
"""
主题 F · 综合模型 (Integrated SHAP Model) — checkpoint 版.

阶段化 + 断点续跑, 避免整跑 20+ 分钟时死机丢全部结果:
  ① entry_gap_cache.json      — 30k candidates 查 bridge token 价格后的 entry_gap_pct
  ② f_cv_results.npz          — GBM 5-fold CV metrics + predictions
  ③ f_models.pkl              — 在全数据上拟合的 reg + clf
  ④ f_shap.npz                — TreeExplainer 的 shap_values (reg + cls)
  ⑤ 图 + tables               — 最终产物

如果某阶段崩溃, 再跑一次会直接从 disk 读已完成的 checkpoint, 跳过重算.

安全修正:
  * n_jobs=1 避免 macOS fork 爆内存 (串行 CV, 慢一点但不死机)
  * SHAP 对行采样 (≤ SHAP_SAMPLE 行) 而不是全量 15k
  * 每个阶段结束 gc.collect() 释放

输出:
  figs/06_integrated.png
  tables/f_regression_metrics.csv
  tables/f_classifier_metrics.csv
  tables/f_feature_importance.csv
"""
from __future__ import annotations
import gc
import json
import os
import pickle
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib as mpl
from sklearn.ensemble import GradientBoostingRegressor, GradientBoostingClassifier
from sklearn.model_selection import KFold, cross_val_score, cross_val_predict
from sklearn.metrics import log_loss
import shap

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from compute_pnl_1y import PriceLookup

from common_loader import (load_df, load_raw, clear_raw_cache,
                           FIGS, TABLES, PALETTE, MPL_STYLE)

mpl.rcParams.update(MPL_STYLE)

RUN_TAG = os.environ.get("WORMHOLE_1Y_RUN_TAG")
CACHE_DIR = Path(__file__).resolve().parents[1] / (f"cache_{RUN_TAG}" if RUN_TAG else "cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
ENTRY_GAP_CACHE = CACHE_DIR / "entry_gap_cache.json"
CV_RESULTS_NPZ  = CACHE_DIR / "f_cv_results.npz"
MODELS_PKL      = CACHE_DIR / "f_models.pkl"
SHAP_NPZ        = CACHE_DIR / "f_shap.npz"

SHAP_SAMPLE = 5000          # SHAP 最多计算多少行 (内存 & 时间安全)
GBM_N_EST   = 200
KF_SPLITS   = 5


FEATURES = [
    "dir_sol_to_eth",
    "hour_utc",
    "log_size",
    "log_latency",
    "atomic_entry_same_block",
    "atomic_exit_same_block",
    "entry_delta_bridge_sec",
    "exit_delta_bridge_sec",
    "entry_coverage",
    "exit_coverage",
    "n_entry_swaps",
    "n_exit_swaps",
    "liq_dead", "liq_thin", "liq_healthy",
    "entry_gap_pct",
    "actor_trade_count",
]


# ---------- Stage 1: entry_gap_pct ----------

def compute_or_load_entry_gap():
    """Return dict: id → signed_gap_pct. Checkpointed to disk."""
    if ENTRY_GAP_CACHE.exists():
        print(f"[1] entry_gap cache hit → {ENTRY_GAP_CACHE.name}", flush=True)
        with open(ENTRY_GAP_CACHE, encoding="utf-8") as f:
            return json.load(f)

    print(f"[1] entry_gap cache miss, computing …", flush=True)
    raw = load_raw()
    pl = PriceLookup()
    entry_gap = {}
    n_cands = len(raw["candidates"])
    t0 = time.time()
    for i, r in enumerate(raw["candidates"]):
        if i and i % 5000 == 0:
            print(f"    entry_gap {i}/{n_cands} got={len(entry_gap)} "
                  f"eth_lru={len(pl._eth_1m_cache)} ({time.time()-t0:.0f}s)",
                  flush=True)
        br = r.get("bridge") or {}
        spl = br.get("spl_mint")
        erc = (br.get("erc20_contract") or "").lower() or None
        if not spl or not erc:
            continue
        dirn = r["direction"]
        sol_ts = r.get("sol_ts")
        eth_ts = r.get("eth_ts")
        if sol_ts is None or eth_ts is None:
            continue
        if dirn == "SOL→ETH":
            ts_ent = sol_ts
        elif dirn == "ETH→SOL":
            ts_ent = eth_ts
        else:
            continue
        sp, _, _ = pl.get(f"solana:{spl}", ts_ent)
        ep, _, _ = pl.get(f"ethereum:{erc}", ts_ent)
        if sp is None or ep is None or sp <= 0 or ep <= 0:
            continue
        if dirn == "SOL→ETH":
            entry_gap[r["id"]] = (ep / sp - 1.0) * 100.0
        else:
            entry_gap[r["id"]] = (sp / ep - 1.0) * 100.0

    with open(ENTRY_GAP_CACHE, "w", encoding="utf-8") as f:
        json.dump(entry_gap, f)
    print(f"    wrote {len(entry_gap)} entries → {ENTRY_GAP_CACHE.name}", flush=True)

    del pl, raw
    clear_raw_cache()
    gc.collect()
    return entry_gap


# ---------- Stage 2: feature frame ----------

def build_feature_frame(entry_gap):
    print("[2] building feature frame …", flush=True)
    df = load_df().copy()
    clear_raw_cache()
    df["entry_gap_pct"] = df["id"].map(entry_gap)

    df["actor"] = df["arb_sol"].fillna("") + "|" + df["arb_eth"].fillna("")
    df["actor_trade_count"] = df.groupby("actor")["id"].transform("count")

    df["dir_sol_to_eth"] = (df["direction"] == "SOL→ETH").astype(int)
    df["liq_dead"]    = (df["liquidity_category"] == "dead_pool_exploit").astype(int)
    df["liq_thin"]    = (df["liquidity_category"] == "thin_pool_arb").astype(int)
    df["liq_healthy"] = (df["liquidity_category"] == "healthy_market_arb").astype(int)
    df["log_size"]    = np.log10(df["entry_cost_usd"].clip(lower=1))
    df["log_latency"] = np.log10(df["time_diff_sec"].clip(lower=1))

    d = df[df.pnl_complete == True].copy()
    d = d.dropna(subset=FEATURES + ["net_pnl_usd"])
    d["net_pnl_clip"] = d["net_pnl_usd"].clip(-100, 500)
    d["profit_bin"]   = (d["net_pnl_usd"] > 0).astype(int)

    X = d[FEATURES].astype(float).values
    y_reg = d["net_pnl_clip"].values
    y_cls = d["profit_bin"].values
    print(f"    X shape = {X.shape}  y_reg={y_reg.shape}  y_cls={y_cls.shape}", flush=True)
    return X, y_reg, y_cls


# ---------- Stage 3: CV + models ----------

def cv_and_fit(X, y_reg, y_cls):
    """Cross-validate and fit both models on full data. Checkpointed."""
    if CV_RESULTS_NPZ.exists() and MODELS_PKL.exists():
        print(f"[3] CV + models cache hit", flush=True)
        dat = np.load(CV_RESULTS_NPZ)
        with open(MODELS_PKL, "rb") as f:
            models = pickle.load(f)
        return {
            "reg": models["reg"], "clf": models["clf"],
            "y_pred_cv": dat["y_pred_cv"],
            "y_proba_cv": dat["y_proba_cv"],
            "r2_cv": dat["r2_cv"],
            "auc_cv": dat["auc_cv"],
        }

    print(f"[3] CV + models cache miss, fitting (n_jobs=1, serial) …", flush=True)
    reg = GradientBoostingRegressor(n_estimators=GBM_N_EST, max_depth=3,
                                    learning_rate=0.05, subsample=0.8,
                                    random_state=42)
    clf = GradientBoostingClassifier(n_estimators=GBM_N_EST, max_depth=3,
                                     learning_rate=0.05, subsample=0.8,
                                     random_state=42)
    kf = KFold(n_splits=KF_SPLITS, shuffle=True, random_state=42)

    t0 = time.time()
    print(f"    CV r2 (reg, n_jobs=1) …", flush=True)
    r2_cv = cross_val_score(reg, X, y_reg, cv=kf, scoring="r2", n_jobs=1)
    print(f"      r2 folds = {r2_cv.round(3).tolist()}  "
          f"({time.time()-t0:.0f}s)", flush=True)

    t0 = time.time()
    print(f"    CV auc (clf, n_jobs=1) …", flush=True)
    auc_cv = cross_val_score(clf, X, y_cls, cv=kf, scoring="roc_auc", n_jobs=1)
    print(f"      auc folds = {auc_cv.round(3).tolist()}  "
          f"({time.time()-t0:.0f}s)", flush=True)

    t0 = time.time()
    print(f"    CV predict (reg) …", flush=True)
    y_pred_cv = cross_val_predict(reg, X, y_reg, cv=kf, n_jobs=1)
    print(f"    CV predict (clf) …", flush=True)
    y_proba_cv = cross_val_predict(clf, X, y_cls, cv=kf,
                                   method="predict_proba", n_jobs=1)[:, 1]
    print(f"      cv-predict done ({time.time()-t0:.0f}s)", flush=True)

    np.savez_compressed(
        CV_RESULTS_NPZ,
        y_pred_cv=y_pred_cv, y_proba_cv=y_proba_cv,
        r2_cv=r2_cv, auc_cv=auc_cv,
    )
    print(f"    cv results → {CV_RESULTS_NPZ.name}", flush=True)

    t0 = time.time()
    print(f"    fit on full data (reg + clf) …", flush=True)
    reg.fit(X, y_reg)
    clf.fit(X, y_cls)
    with open(MODELS_PKL, "wb") as f:
        pickle.dump({"reg": reg, "clf": clf}, f)
    print(f"    models → {MODELS_PKL.name} ({time.time()-t0:.0f}s)", flush=True)

    gc.collect()
    return {
        "reg": reg, "clf": clf,
        "y_pred_cv": y_pred_cv, "y_proba_cv": y_proba_cv,
        "r2_cv": r2_cv, "auc_cv": auc_cv,
    }


# ---------- Stage 4: SHAP ----------

def shap_or_load(reg, clf, X):
    """Compute SHAP on a sample. Checkpointed."""
    if SHAP_NPZ.exists():
        print(f"[4] SHAP cache hit", flush=True)
        dat = np.load(SHAP_NPZ)
        return dat["shap_reg"], dat["shap_cls"], dat["sample_idx"]

    print(f"[4] SHAP cache miss, computing on ≤{SHAP_SAMPLE} sampled rows …",
          flush=True)
    n = X.shape[0]
    if n > SHAP_SAMPLE:
        rng = np.random.RandomState(42)
        sample_idx = rng.choice(n, SHAP_SAMPLE, replace=False)
    else:
        sample_idx = np.arange(n)
    Xs = X[sample_idx]

    t0 = time.time()
    shap_reg = shap.TreeExplainer(reg).shap_values(Xs)
    print(f"    shap_reg done, shape={shap_reg.shape} ({time.time()-t0:.0f}s)",
          flush=True)
    t0 = time.time()
    shap_cls = shap.TreeExplainer(clf).shap_values(Xs)
    if isinstance(shap_cls, list):
        shap_cls = shap_cls[1]
    print(f"    shap_cls done, shape={shap_cls.shape} ({time.time()-t0:.0f}s)",
          flush=True)

    np.savez_compressed(SHAP_NPZ, shap_reg=shap_reg, shap_cls=shap_cls,
                        sample_idx=sample_idx)
    print(f"    shap → {SHAP_NPZ.name}", flush=True)
    gc.collect()
    return shap_reg, shap_cls, sample_idx


# ---------- Stage 5: figure + tables ----------

def make_figure_and_tables(X, y_cls, y_reg, res, shap_reg, shap_cls, sample_idx):
    print("[5] tables + figure …", flush=True)
    reg, clf = res["reg"], res["clf"]
    r2_cv, auc_cv = res["r2_cv"], res["auc_cv"]
    y_proba_cv = res["y_proba_cv"]

    m_reg = pd.DataFrame({
        "metric": ["r2_cv_mean", "r2_cv_std", "n"],
        "value":  [float(r2_cv.mean()), float(r2_cv.std()), len(X)],
    })
    m_reg.to_csv(TABLES / "f_regression_metrics.csv", index=False)

    m_cls = pd.DataFrame({
        "metric": ["auc_cv_mean", "auc_cv_std", "logloss_full", "baseline_rate", "n"],
        "value": [
            float(auc_cv.mean()),
            float(auc_cv.std()),
            float(log_loss(y_cls, y_proba_cv.clip(1e-4, 1-1e-4))),
            float(y_cls.mean()),
            len(X),
        ],
    })
    m_cls.to_csv(TABLES / "f_classifier_metrics.csv", index=False)

    imp_reg = pd.Series(reg.feature_importances_, index=FEATURES).sort_values(ascending=False)
    imp_cls = pd.Series(clf.feature_importances_, index=FEATURES).sort_values(ascending=False)
    fi_tbl = pd.concat({"reg": imp_reg, "cls": imp_cls}, axis=1).round(4)
    fi_tbl.to_csv(TABLES / "f_feature_importance.csv")

    mean_abs_shap_reg = np.abs(shap_reg).mean(axis=0)
    mean_abs_shap_cls = np.abs(shap_cls).mean(axis=0)
    order_cls = np.argsort(mean_abs_shap_cls)[::-1]
    order_reg = np.argsort(mean_abs_shap_reg)[::-1]

    fig = plt.figure(figsize=(14, 9))
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.32)

    ax_a = fig.add_subplot(gs[0, 0])
    ax_a.barh(imp_reg.index[::-1], imp_reg.values[::-1],
              color=PALETTE["sol_to_eth"], edgecolor="black", linewidth=0.3)
    ax_a.set_xlabel("GBM gain importance")
    ax_a.set_title(f"(a) Regressor feature importance  (target = net_pnl, clipped)\n"
                   f"CV $R^2$ = {r2_cv.mean():.3f} ± {r2_cv.std():.3f}")

    ax_b = fig.add_subplot(gs[0, 1])
    names_cls = [FEATURES[i] for i in order_cls]
    vals_cls  = [mean_abs_shap_cls[i] for i in order_cls]
    ax_b.barh(names_cls[::-1], vals_cls[::-1],
              color=PALETTE["eth_to_sol"], edgecolor="black", linewidth=0.3)
    ax_b.set_xlabel("mean |SHAP value|")
    ax_b.set_title(f"(b) Classifier SHAP importance  (target = P(net_pnl > 0))\n"
                   f"CV AUC = {auc_cv.mean():.3f} ± {auc_cv.std():.3f}  "
                   f"(base rate = {y_cls.mean():.0%})")

    ax_c = fig.add_subplot(gs[1, 0])
    top_n = 8
    top_idx = order_reg[:top_n]
    y_positions = np.arange(top_n)[::-1]
    Xs = X[sample_idx]
    for yi, fi in zip(y_positions, top_idx):
        fv = Xs[:, fi]
        sv = shap_reg[:, fi]
        q = pd.Series(fv).rank(pct=True).values
        jitter = (np.random.RandomState(fi).rand(len(sv)) - 0.5) * 0.6
        sc = ax_c.scatter(np.clip(sv, -40, 60), yi + jitter, c=q, cmap="coolwarm",
                          s=4, alpha=0.45, edgecolors="none", vmin=0, vmax=1)
    ax_c.set_yticks(y_positions)
    ax_c.set_yticklabels([FEATURES[i] for i in top_idx], fontsize=8)
    ax_c.axvline(0, color="grey", lw=0.5, ls="--")
    ax_c.set_xlabel("SHAP value (impact on net_pnl prediction $)")
    ax_c.set_xlim(-40, 60)
    ax_c.set_title(f"(c) SHAP beeswarm  top-{top_n} regressor features  "
                   f"(sample n={len(sample_idx)})")
    cbar = plt.colorbar(sc, ax=ax_c, shrink=0.7, pad=0.02)
    cbar.set_label("feature value (quantile)", fontsize=7)
    cbar.ax.tick_params(labelsize=6)

    ax_d = fig.add_subplot(gs[1, 1])
    q_bins = pd.qcut(pd.Series(y_proba_cv).rank(method="first"), 10,
                     labels=[f"D{i+1}" for i in range(10)])
    cal = pd.DataFrame({"proba": y_proba_cv, "actual": y_cls, "bin": q_bins})
    cal_stats = cal.groupby("bin", observed=True).agg(
        pred_mean=("proba", "mean"), actual_rate=("actual", "mean"), n=("actual", "count"),
    ).round(3)
    ax_d.plot([0, 1], [0, 1], color="grey", ls="--", lw=0.8, label="perfect calibration")
    ax_d.scatter(cal_stats["pred_mean"], cal_stats["actual_rate"],
                 s=40, color=PALETTE["positive"], edgecolor="black", linewidth=0.5,
                 label=f"CV bins (n = {int(cal_stats['n'].sum())})")
    for _, row in cal_stats.iterrows():
        ax_d.annotate(f"{int(row['n'])}", (row["pred_mean"], row["actual_rate"]),
                      fontsize=6.5, xytext=(3, 3), textcoords="offset points")
    ax_d.set_xlabel("predicted P(net_pnl > 0)")
    ax_d.set_ylabel("observed net-positive rate")
    ax_d.set_title(f"(d) Calibration  (base rate = {y_cls.mean():.0%})")
    ax_d.set_xlim(0, 1); ax_d.set_ylim(0, 1)
    ax_d.legend(frameon=False, loc="upper left", fontsize=8)

    fig.suptitle("Figure 6 — Integrated Predictive Model (GBM + SHAP)",
                 fontsize=12, y=0.995, fontweight="bold")
    out = FIGS / "06_integrated.png"
    fig.savefig(out)
    plt.close(fig)

    print(f"n used: {len(X)}")
    print(f"Regressor  CV R² = {r2_cv.mean():.4f} ± {r2_cv.std():.4f}")
    print(f"Classifier CV AUC = {auc_cv.mean():.4f} ± {auc_cv.std():.4f}")
    print(f"Classifier base rate P(net+) = {y_cls.mean():.4f}")
    print()
    print("Regressor feature importance (top 10):")
    print(imp_reg.head(10).to_string())
    print()
    print("Classifier feature importance (top 10):")
    print(imp_cls.head(10).to_string())
    print()
    print("Mean |SHAP| ranking (classifier top 10):")
    for i in order_cls[:10]:
        print(f"  {FEATURES[i]:<30s}  {mean_abs_shap_cls[i]:.4f}")
    print(f"saved: {out}")


def main():
    # Stage 1
    entry_gap = compute_or_load_entry_gap()
    entry_gap = {int(k) if isinstance(k, str) and k.isdigit() else k: v
                 for k, v in entry_gap.items()}
    gc.collect()

    # Stage 2
    X, y_reg, y_cls = build_feature_frame(entry_gap)
    del entry_gap
    gc.collect()

    # Stage 3
    res = cv_and_fit(X, y_reg, y_cls)

    # Stage 4
    shap_reg, shap_cls, sample_idx = shap_or_load(res["reg"], res["clf"], X)

    # Stage 5
    make_figure_and_tables(X, y_cls, y_reg, res, shap_reg, shap_cls, sample_idx)


if __name__ == "__main__":
    main()
