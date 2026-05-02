"""
分类型回归：profit ~ chain_length（线性），并可选 profit ~ chain_length + chain_length^2（二次）。
输出：系数、标准误、t 统计量、p 值、R^2。
"""
import json
import math
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_JSON = os.path.join(SCRIPT_DIR, "arb_profit_chain_length.json")
OUTPUT_TXT = os.path.join(SCRIPT_DIR, "profit_chain_regression.txt")
OUTPUT_JSON = os.path.join(SCRIPT_DIR, "profit_chain_regression.json")


def mat_mult(A, B):
    """A (n×k) @ B (k×m) -> (n×m)"""
    n, k, m = len(A), len(A[0]), len(B[0])
    return [[sum(A[i][s] * B[s][j] for s in range(k)) for j in range(m)] for i in range(n)]


def mat_T(A):
    """转置"""
    if not A:
        return []
    return [[A[i][j] for i in range(len(A))] for j in range(len(A[0]))]


def mat_inv_2x2(A):
    """2x2 逆矩阵"""
    a, b, c, d = A[0][0], A[0][1], A[1][0], A[1][1]
    det = a * d - b * c
    if abs(det) < 1e-20:
        return None
    return [[d / det, -b / det], [-c / det, a / det]]


def mat_inv_3x3(A):
    """3x3 逆矩阵"""
    a, b, c = A[0][0], A[0][1], A[0][2]
    d, e, f = A[1][0], A[1][1], A[1][2]
    g, h, i = A[2][0], A[2][1], A[2][2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-20:
        return None
    inv = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ]
    return [[inv[i][j] / det for j in range(3)] for i in range(3)]


def ols_linear(xs, ys):
    """y = b0 + b1*x. 返回 (b0, b1, se_b0, se_b1, t0, t1, p0, p1, r_squared)。"""
    n = len(xs)
    if n < 3:
        return None
    X = [[1.0, x] for x in xs]
    Xt = mat_T(X)
    XtX = mat_mult(Xt, X)
    Xty = [sum(Xt[i][j] * ys[j] for j in range(n)) for i in range(2)]
    inv = mat_inv_2x2(XtX)
    if not inv:
        return None
    beta = [sum(inv[i][j] * Xty[j] for j in range(2)) for i in range(2)]
    yhat = [beta[0] + beta[1] * x for x in xs]
    resid = [ys[i] - yhat[i] for i in range(n)]
    rss = sum(r ** 2 for r in resid)
    df = n - 2
    if df <= 0:
        return None
    sigma2 = rss / df
    ymean = sum(ys) / n
    tss = sum((y - ymean) ** 2 for y in ys)
    r_sq = 1 - rss / tss if tss > 0 else 0
    var_beta = [sigma2 * inv[i][i] for i in range(2)]
    se = [math.sqrt(max(0, v)) for v in var_beta]
    t = [beta[i] / se[i] if se[i] > 0 else 0 for i in range(2)]
    # two-tailed p-value from t distribution (approximate with normal for large df)

    def pval(t_stat, dof):
        if dof <= 0:
            return float("nan")
        # 用正态近似
        z = abs(t_stat)
        return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    p = [pval(t[i], df) for i in range(2)]
    return {
        "intercept": beta[0], "coef_chain_length": beta[1],
        "se_intercept": se[0], "se_chain_length": se[1],
        "t_intercept": t[0], "t_chain_length": t[1],
        "p_intercept": p[0], "p_chain_length": p[1],
        "r_squared": r_sq, "n": n, "df": df,
    }


def ols_quadratic(xs, ys):
    """y = b0 + b1*x + b2*x^2."""
    n = len(xs)
    if n < 4:
        return None
    X = [[1.0, x, x * x] for x in xs]
    Xt = mat_T(X)
    XtX = mat_mult(Xt, X)
    Xty = [sum(Xt[i][j] * ys[j] for j in range(n)) for i in range(3)]
    inv = mat_inv_3x3(XtX)
    if not inv:
        return None
    beta = [sum(inv[i][j] * Xty[j] for j in range(3)) for i in range(3)]
    yhat = [beta[0] + beta[1] * xs[i] + beta[2] * xs[i] ** 2 for i in range(n)]
    resid = [ys[i] - yhat[i] for i in range(n)]
    rss = sum(r ** 2 for r in resid)
    df = n - 3
    if df <= 0:
        return None
    sigma2 = rss / df
    ymean = sum(ys) / n
    tss = sum((y - ymean) ** 2 for y in ys)
    r_sq = 1 - rss / tss if tss > 0 else 0
    var_beta = [sigma2 * inv[i][i] for i in range(3)]
    se = [math.sqrt(max(0, v)) for v in var_beta]
    t = [beta[i] / se[i] if se[i] > 0 else 0 for i in range(3)]

    def pval(t_stat, dof):
        if dof <= 0:
            return float("nan")
        z = abs(t_stat)
        return 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))

    p = [pval(t[i], df) for i in range(3)]
    return {
        "intercept": beta[0], "coef_chain_length": beta[1], "coef_chain_length_sq": beta[2],
        "se_intercept": se[0], "se_chain_length": se[1], "se_chain_length_sq": se[2],
        "t_intercept": t[0], "t_chain_length": t[1], "t_chain_length_sq": t[2],
        "p_intercept": p[0], "p_chain_length": p[1], "p_chain_length_sq": p[2],
        "r_squared": r_sq, "n": n, "df": df,
    }


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

    results_linear = {}
    results_quad = {}
    lines = [
        "=== 分类型回归：profit ~ chain_length ===\n",
        "数据来源: arb_profit_chain_length.json\n\n",
    ]

    for t in ["SOL", "USDC", "USDT"]:
        points = by_type[t]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        n = len(points)
        lines.append(f"-------- {t} (n={n}) --------\n")

        # 线性
        res_lin = ols_linear(xs, ys)
        if res_lin:
            results_linear[t] = res_lin
            lines.append(
                "【线性】 profit = intercept + coef_chain_length * chain_length\n")
            lines.append(
                f"  intercept         = {res_lin['intercept']:.6e}   se = {res_lin['se_intercept']:.6e}   t = {res_lin['t_intercept']:.4f}   p = {res_lin['p_intercept']:.4f}\n")
            lines.append(
                f"  coef_chain_length = {res_lin['coef_chain_length']:.6e}   se = {res_lin['se_chain_length']:.6e}   t = {res_lin['t_chain_length']:.4f}   p = {res_lin['p_chain_length']:.4f}\n")
            lines.append(f"  R^2 = {res_lin['r_squared']:.4f}\n\n")
        else:
            lines.append("【线性】 样本不足或矩阵奇异，未估计。\n\n")

        # 二次
        res_quad = ols_quadratic(xs, ys)
        if res_quad:
            results_quad[t] = res_quad
            lines.append(
                "【二次】 profit = intercept + b1*chain_length + b2*chain_length^2\n")
            lines.append(
                f"  intercept           = {res_quad['intercept']:.6e}   se = {res_quad['se_intercept']:.6e}   t = {res_quad['t_intercept']:.4f}   p = {res_quad['p_intercept']:.4f}\n")
            lines.append(
                f"  coef_chain_length   = {res_quad['coef_chain_length']:.6e}   se = {res_quad['se_chain_length']:.6e}   t = {res_quad['t_chain_length']:.4f}   p = {res_quad['p_chain_length']:.4f}\n")
            lines.append(
                f"  coef_chain_length^2 = {res_quad['coef_chain_length_sq']:.6e}   se = {res_quad['se_chain_length_sq']:.6e}   t = {res_quad['t_chain_length_sq']:.4f}   p = {res_quad['p_chain_length_sq']:.4f}\n")
            lines.append(f"  R^2 = {res_quad['r_squared']:.4f}\n\n")
        else:
            lines.append("【二次】 样本不足或矩阵奇异，未估计。\n\n")

    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print("".join(lines))

    out = {
        "linear": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in results_linear.items()},
        "quadratic": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in results_quad.items()},
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"系数与 p 值已保存: {OUTPUT_TXT}\n{OUTPUT_JSON}")


if __name__ == "__main__":
    main()
