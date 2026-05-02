# 三张图的学术解释

本文件夹包含三张图及其详细学术解释，供论文撰写参考。

| 文件 | 图名 | 生成脚本 |
|---|---|---|
| `figA_dex_network.png` | DEX 共现网络 | `plot_dex_network.py` |
| `plot_correlation.png` | 因子相关矩阵（Spearman，single-tx / multi-tx 分面） | `plot_correlation.py` |
| `fig8_tip_vs_gross_scatter.png` | Tip vs Gross 散点图 | bundle_economics 项目 |

---

## 图 1 · `figA_dex_network.png`：DEX 共现网络

**图像内容。** 本图是 wSOL 套利子集（387,884 条）中各 DEX 的共现网络，节点为 DEX（不含 Jupiter v6 路由器），边权为"两 DEX 在同一笔循环套利中同时出现"的次数。节点大小按 log₁₀(arb count) 映射到 [250, 1400] 像素，避免高频节点完全盖住低频节点；节点颜色按该 DEX 平均每笔净利（×10⁻⁴ SOL）以 RdYlGn 发散色上色；边宽按 log₁₀(co-occurrence) 映射到 [0.5, 4.5]，保持稀疏长尾可见。布局使用 spring-layout（k = 2.2，400 次迭代），便于把高度互连簇自然推开。节点过滤阈值 arbs ≥ 100、边过滤阈值共现 ≥ 20，并只保留最大连通分量。

**结构解读。** 图中呈现出一个明显的"Raydium AMM v4 + Orca Whirlpool + Meteora DLMM"核心三角——三者两两共现强度最高（边权 ≥ 500 的标注基本集中在此三点），其平均净利颜色偏绿，说明它们不仅是图中流量最大的节点，也是盈利密度最高的节点。外围节点如 pump.fun AMM、Lifinity v2、Phoenix、Raydium CLMM / CPMM 等以中等至细边连入核心，其自身与其他外围 DEX 之间几乎无边——说明套利路径在拓扑上是"轮辐型"而非"网状"，外围 DEX 主要作为核心价差的兑换出口被使用，而非形成独立的套利闭环。

**经济含义。** 网络拓扑直接支持两条结论：一是 Solana 套利流动性在 AMM 层面呈强"主干寡占"结构，主干三角承担绝大部分 arbitrage volume，外围 DEX 依附进入；二是外围 DEX 的颜色分布显示 pump.fun AMM 等新兴场所的平均净利显著高于主干，但共现频次低，说明它们是"机会利润高但容量小"的补充池，并不能替代主干发挥基础作用。对论文叙事而言，此图是"MEV 的 DEX 侧基础设施是少数几家头部 AMM"这一主张的直接可视化证据。

---

## 图 2 · `plot_correlation.png`：因子相关矩阵（Spearman，single-tx / multi-tx 分面）

**图像内容。** 两个 8 × 8 矩阵分别对应 single-tx 与 multi-tx bundle 子集，颜色使用 RdBu_r 发散色带 [-1, 1] 编码 Spearman 秩相关。八个因子为 hops（swap+1）、fee（tx 基础费）、tip（bundle tip）、cost（fee+tip）、gross（毛利）、net（净利）、bsize（bundle 交易数）、cost_rate（cost/gross）。采用秩相关而非 Pearson，是为避免 lamports 尺度下极端重尾对线性相关的主导；限定 gross > 0 且 in_bundle = True，以排除 tip = 0 的非 bundle 交易在矩阵中把 tip 相关行列稀释成近零值。

**结构解读。** 四组稳健规律在两分面均可读出：(1) `tip ↔ cost` 为 0.97 / 1.00，即 fee 在决策与解释层面都不重要，cost 的秩变动几乎由 tip 单独驱动；(2) `gross ↔ net` 为 0.90 / 0.83，毛利的秩主导净利的秩，但两分面 0.07 的差距提示 multi-tx 中 tip 决策引入的额外方差更大；(3) `cost_rate ↔ net` 为 −0.85 / −0.61，说明 net 的亏损主要来自"tip 相对 gross 过高"，而非"gross 本身不足"，这与 bundle_semantics 论文的 R2 结论一致；(4) `hops` 与所有经济变量秩相关均落入 ±0.15，表明路径长度在当前样本内不承载经济信号，是结构字段而非能力 / 难度代理。

**反直觉发现。** `tip ↔ gross` 在 multi-tx 为 0.74、single-tx 仅 0.28，方向与 bundle_semantics 论文 R1 的先验预测相反。可能的机制性解释是：现存的 multi-tx 残存玩家已被筛选为"具备 υ(tᵢ) 估计优势的精细出价者"——其 tip 能紧密跟随 gross；single-tx 子群因策略碎片化（固定 tip 下限、rule-based 出价）在低 gross 段大量平结，Spearman 被拉低。这意味着原 R1 应从"估计难度弱化耦合"改写为一条**条件性命题**：估计难度本身并不机械地削弱 tip--gross 耦合，玩家的估计能力可以反向补偿。

---

## 图 3 · `fig8_tip_vs_gross_scatter.png`：Tip vs Gross 散点图

**图像内容。** 横纵轴均为对数坐标，展示 10,000 条随机采样的 bundle 套利的 tip（付给 Jito）与 gross（毛利）分布，绿色为 single-tx bundle、红色为 multi-tx bundle。红色虚线 τ = g 为净利为零的盈亏临界，黑色点线 τ = 0.5g 为"50% 利润上缴 tip"的参照带。横坐标底部与纵坐标底部的水平带状堆积（τ ≈ 10⁻⁶ SOL）来源于 Jito 的最小 tip 限额 1000 lamports，属于策略下限。

**结构解读。** 绿点（single-tx）呈一条紧贴 τ = 0.5g 参照线下方的窄带，意味着 single-tx 搜索者的出价集中在"毛利的 20%--50%"区间，且随 gross 成比例放大，显示出高度的规则化、有纪律的出价风格；红点（multi-tx）则整体向上漂移，在红色 τ = g 线以上（净利为负区域）有大量散布点，并在 τ = 0.5g 线以上带状加密，提示 multi-tx bundle 的 tip 定价频繁突破"毛利一半"的经验阈值，甚至超过毛利本身。两类点在低 gross 段（g < 10⁻⁵ SOL）均被 tip 下限截断，但红点穿越 τ = g 线的比例显著高于绿点。

**经济含义。** 图 8 是 bundle_semantics 论文 R2（亏损集中）最直观的可视化证据：总样本中 11.7% 的亏损 bundle 里，multi-tx 仅占总 bundle 数的约 25% 却贡献了 70% 以上的亏损——这与图中红点越过 τ = g 线的显著优势完全一致。亏损的机制并非对毛利的逆向选择（若是则红点应分散在全 gross 区间的临界线附近），而是 **tip 定价的系统性偏高**：multi-tx 搜索者必须为整包（含套利主体与不可直接货币化的辅助 tx {tᵢ}）整体出价，由于 Συ(tᵢ) 难以先验精确估计，tip 在分布上被推至"包含保守摊销项"的偏高水平，从而在相当比例的样本中越过 τ = g 的盈亏线。这张图把 R2 从一个计数比例结论升级为一个**可视化的机制证据**。
