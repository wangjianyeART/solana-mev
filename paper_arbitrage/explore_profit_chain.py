"""
Explore the relationship between chain_length and profit:
1. Compute Spearman correlation coefficient by type
2. Bin statistics by chain length (mean/median/sample size)
3. Plot by type: chain length vs average profit (with sample size)
"""
import matplotlib.pyplot as plt
import json
import os

import matplotlib
matplotlib.use("Agg")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_JSON = os.path.join(SCRIPT_DIR, "arb_profit_chain_length.json")
OUTPUT_CORR = os.path.join(SCRIPT_DIR, "profit_chain_correlation.txt")
OUTPUT_BINNED = os.path.join(SCRIPT_DIR, "profit_chain_binned.json")
OUTPUT_PNG = os.path.join(SCRIPT_DIR, "profit_vs_chain_length_explore.png")


def rank_data(xs):
    """Return ranks (1-based), tied values get average rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and xs[order[j]] == xs[order[i]]:
            j += 1
        avg_rank = (i + j + 1) / 2.0
        for k in range(i, j):
            ranks[order[k]] = avg_rank
        i = j
    return ranks


def pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return cov / (sx * sy)


def spearman(xs, ys):
    rx = rank_data(xs)
    ry = rank_data(ys)
    return pearson(rx, ry)


def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        rows = json.load(f)

    if not rows:
        print("无数据")
        return

    by_type = {"SOL": [], "USDC": [], "USDT": []}
    for r in rows:
        t = r.get("type")
        if t in by_type:
            by_type[t].append((r["chain_length"], r["profit"]))

    # ----- 1. 相关系数（Spearman） -----
    lines = ["=== 链长度 vs 利润 相关系数 (Spearman) ===\n"]
    for t in ["SOL", "USDC", "USDT"]:
        points = by_type[t]
        if len(points) < 2:
            lines.append(f"{t}: n={len(points)}, 无法计算相关\n")
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        rho = spearman(xs, ys)
        lines.append(f"{t}: n={len(points)}, Spearman rho = {rho:.4f}\n")
    with open(OUTPUT_CORR, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print("".join(lines))

    # ----- 2. 按链长度分箱（取整为 bin） -----
    def get_binned(points, bin_step=1.0):
        from collections import defaultdict
        bins = defaultdict(list)
        for c, p in points:
            bin_key = int(c // bin_step) * \
                bin_step if bin_step >= 1 else round(c, 1)
            bins[bin_key].append(p)
        out = []
        for k in sorted(bins.keys()):
            vals = bins[k]
            out.append({
                "chain_length_bin": k,
                "count": len(vals),
                "mean_profit": sum(vals) / len(vals),
                "median_profit": sorted(vals)[len(vals) // 2] if vals else 0,
            })
        return out

    binned_by_type = {}
    for t in ["SOL", "USDC", "USDT"]:
        binned_by_type[t] = get_binned(by_type[t])
    with open(OUTPUT_BINNED, "w", encoding="utf-8") as f:
        json.dump(binned_by_type, f, ensure_ascii=False, indent=2)
    print(f"分箱结果已保存: {OUTPUT_BINNED}\n")

    # ----- 3. 分类型图：链长度 bin vs 平均利润 -----
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    colors = {"SOL": "#4A90D9", "USDC": "#50C878", "USDT": "#E8A838"}
    for idx, t in enumerate(["SOL", "USDC", "USDT"]):
        ax = axes[idx]
        binned = binned_by_type[t]
        if not binned:
            ax.set_title(f"{t} (no data)")
            continue
        xs = [b["chain_length_bin"] for b in binned]
        ys = [b["mean_profit"] for b in binned]
        ns = [b["count"] for b in binned]
        ax.bar(xs, ys, color=colors[t], alpha=0.8, width=0.7, edgecolor="none")
        ax.set_xlabel("Chain length (bin)")
        ax.set_ylabel(f"Mean profit ({t})")
        ax.set_title(f"{t} (n={sum(ns)})")
        ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"探索图已保存: {OUTPUT_PNG}")

    print("\n--- 探索建议 ---")
    print("1. 看 Spearman 正/负：链越长利润是否整体更高/更低（单调关系）。")
    print("2. 看分箱图：某些链长度区间是否平均利润明显更高。")
    print("3. 若要做回归：可在各类型内做 profit ~ chain_length（或加二次项）。")
    print("4. 注意：SOL 与 USDC/USDT 单位不同，需分类型分析或统一换算后再合并。")


if __name__ == "__main__":
    main()
