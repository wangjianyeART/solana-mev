"""
统计：1）链长度 chain_length 的分布；2）2 签名者套利的分布。
数据来源：arb_profit_chain_length.json
"""
import json
import os
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_JSON = os.path.join(SCRIPT_DIR, "arb_profit_chain_length.json")
OUTPUT_JSON = os.path.join(SCRIPT_DIR, "stats_chain_length_signers.json")


def stats(nums):
    if not nums:
        return {"n": 0, "min": None, "max": None, "mean": None, "median": None, "std": None}
    n = len(nums)
    s = sorted(nums)
    min_v = min(nums)
    max_v = max(nums)
    mean_v = sum(nums) / n
    median_v = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    var = sum((x - mean_v) ** 2 for x in nums) / n
    std_v = var ** 0.5
    return {"n": n, "min": min_v, "max": max_v, "mean": mean_v, "median": median_v, "std": std_v}


def freq_table(values):
    """值 -> 出现次数，按键排序"""
    d = defaultdict(int)
    for v in values:
        d[v] += 1
    return dict(sorted(d.items()))


def freq_to_pct(freq, n):
    """频数表 -> 百分比表（保留 2 位小数）"""
    if not n:
        return {}
    return {k: round(100 * v / n, 2) for k, v in freq.items()}


def main():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        rows = json.load(f)

    if not rows:
        print("无数据")
        return

    # 按类型、按 num_signers 分组
    by_type = defaultdict(list)
    one_signer = []
    two_signer = []
    all_chain = []
    all_num_signers = []

    for r in rows:
        t = r.get("type")
        ns = r.get("num_signers", 1)
        c = r.get("chain_length", 0)
        by_type[t].append(r)
        all_chain.append(c)
        all_num_signers.append(ns)
        if ns == 2:
            two_signer.append(r)
        else:
            one_signer.append(r)

    # ----- 1. 链长度分布 -----
    n_all = len(all_chain)
    chain_stats_all = stats(all_chain)
    chain_freq_all = freq_table(all_chain)
    chain_pct_all = freq_to_pct(chain_freq_all, n_all)

    chain_by_type = {}
    for t in ["SOL", "USDC", "USDT"]:
        pts = by_type.get(t, [])
        cs = [r["chain_length"] for r in pts]
        fr = freq_table(cs)
        chain_by_type[t] = {
            "stats": stats(cs),
            "freq": fr,
            "freq_pct": freq_to_pct(fr, len(cs)),
        }

    # ----- 2. 2 签名者套利分布 -----
    n_two = len(two_signer)
    n_one = len(one_signer)
    n_total = len(rows)
    two_signer_chain = [r["chain_length"] for r in two_signer]
    two_signer_profit = [r["profit"] for r in two_signer]
    two_signer_by_type = defaultdict(list)
    for r in two_signer:
        two_signer_by_type[r["type"]].append(r)

    two_freq = freq_table(two_signer_chain)
    two_dist = {
        "count": n_two,
        "count_1_signer": n_one,
        "total": n_total,
        "pct_2_signer": round(100 * n_two / n_total, 2) if n_total else 0,
        "chain_length": {
            "stats": stats(two_signer_chain),
            "freq": two_freq,
            "freq_pct": freq_to_pct(two_freq, n_two),
        },
        "profit": {
            "stats": stats(two_signer_profit),
        },
        "by_type": {},
    }
    for t in ["SOL", "USDC", "USDT"]:
        pts = two_signer_by_type.get(t, [])
        if not pts:
            two_dist["by_type"][t] = {"count": 0, "chain_length": {
                "stats": stats([]), "freq": {}, "freq_pct": {}}, "profit": {"stats": stats([])}}
            continue
        cs = [r["chain_length"] for r in pts]
        ps = [r["profit"] for r in pts]
        fr = freq_table(cs)
        two_dist["by_type"][t] = {
            "count": len(pts),
            "chain_length": {"stats": stats(cs), "freq": fr, "freq_pct": freq_to_pct(fr, len(pts))},
            "profit": {"stats": stats(ps)},
        }

    # ----- 控制台输出 -----
    def round_dict(d):
        if d is None:
            return None
        if isinstance(d, dict):
            return {k: round_dict(v) for k, v in d.items()}
        if isinstance(d, float):
            return round(d, 6) if d == d else d
        return d

    print("=" * 60)
    print("一、链长度 (chain_length) 分布")
    print("=" * 60)
    print(f"全体套利: n={chain_stats_all['n']}, min={chain_stats_all['min']}, max={chain_stats_all['max']}, mean={chain_stats_all['mean']:.4f}, median={chain_stats_all['median']}, std={chain_stats_all['std']:.4f}")
    print("全体 chain_length 笔数(%):", json.dumps(
        chain_pct_all, ensure_ascii=False))
    print()
    for t in ["SOL", "USDC", "USDT"]:
        st = chain_by_type[t]["stats"]
        pct = chain_by_type[t]["freq_pct"]
        mean_str = f"{st['mean']:.4f}" if st["mean"] is not None else "N/A"
        print(
            f"  {t}: n={st['n']}, min={st['min']}, max={st['max']}, mean={mean_str}, median={st['median']}, 笔数(%): {pct}")
    print()

    print("=" * 60)
    print("二、2 签名者套利分布")
    print("=" * 60)
    print(
        f"2 签名者套利: {n_two} 笔, 1 签名者: {n_one} 笔, 合计: {n_total} 笔, 2 签占比: {two_dist['pct_2_signer']}%")
    if n_two:
        st_c = two_dist["chain_length"]["stats"]
        st_p = two_dist["profit"]["stats"]
        print(
            f"2 签 chain_length: min={st_c['min']}, max={st_c['max']}, mean={st_c['mean']:.4f}, median={st_c['median']}, std={st_c['std']:.4f}")
        print("2 签 chain_length 笔数(%):", json.dumps(
            two_dist["chain_length"]["freq_pct"], ensure_ascii=False))
        print(
            f"2 签 profit: min={st_p['min']}, max={st_p['max']}, mean={st_p['mean']:.6f}, median={st_p['median']:.6f}")
        print("2 签按类型:")
        for t in ["SOL", "USDC", "USDT"]:
            bt = two_dist["by_type"][t]
            print(
                f"  {t}: count={bt['count']}, chain_length 笔数(%): {bt['chain_length']['freq_pct']}")
    print()

    # ----- 保存 JSON（数值保留小数） -----
    out = {
        "chain_length_distribution": {
            "all": {**chain_stats_all, "freq": chain_freq_all, "freq_pct": chain_pct_all},
            "by_type": {t: {"stats": chain_by_type[t]["stats"], "freq": chain_by_type[t]["freq"], "freq_pct": chain_by_type[t]["freq_pct"]} for t in ["SOL", "USDC", "USDT"]},
        },
        "two_signer_distribution": two_dist,
    }
    # 浮点保留 6 位

    def to_serializable(obj):
        if isinstance(obj, dict):
            return {k: to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [to_serializable(x) for x in obj]
        if isinstance(obj, float):
            return round(obj, 6) if obj == obj else None
        return obj

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(to_serializable(out), f, ensure_ascii=False, indent=2)
    print(f"已保存: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
