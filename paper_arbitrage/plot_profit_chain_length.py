"""
横坐标：链长度 chain_length，纵坐标：利润 profit。
数据来源：arb_profit_chain_length.json，按 type (SOL/USDC/USDT) 分色。
"""
import matplotlib.pyplot as plt
import json
import os

import matplotlib
matplotlib.use("Agg")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_JSON = os.path.join(SCRIPT_DIR, "arb_profit_chain_length.json")
OUTPUT_PNG = os.path.join(SCRIPT_DIR, "profit_vs_chain_length.png")


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

    fig, ax = plt.subplots(figsize=(9, 6))
    colors = {"SOL": "#4A90D9", "USDC": "#50C878", "USDT": "#E8A838"}
    for t, points in by_type.items():
        if not points:
            continue
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        ax.scatter(
            xs, ys, c=colors[t], label=f"{t} (n={len(points)})", alpha=0.6, s=12)

    ax.set_xlabel("Chain length (transfer count / 2)")
    ax.set_ylabel("Profit (SOL / USDC / USDT)")
    ax.set_title("Arbitrage profit vs chain length")
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    # 纵轴用 symlog 以便同时显示小额 SOL 与大额 USDC/USDT
    ax.set_yscale("symlog", linthresh=0.001)
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"已保存: {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
