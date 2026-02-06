# Fast MarginFi - MarginFi 清算 MEV 分析工具

快速获取、解析和分析 MarginFi 协议的清算交易。

## 功能

- **获取数据**: 并发获取 MarginFi 清算交易，严格遵守 API 速率限制
- **解析数据**: 使用 IDL 解析 MarginFi 指令，提取余额变化和清算详情
- **分析数据**: 计算清算利润、生成统计报告

## MarginFi 程序 ID

| 程序 | Program ID |
|------|------------|
| MarginFi | `2jGhuVUuy3umdzByFx8sNWUAaf5vaeuDm78RDPEnhrMr` |

## 项目结构

```
marginfi/
├── main.py                           # 主入口，整合三步流程
├── step1_fetch_liquidations_fast.py  # 步骤1: 获取清算交易
├── step2_batch_parse.py              # 步骤2: 批量解析交易
├── step3_analyze.py                  # 步骤3: 分析清算数据
├── marginfi_decoder.py               # MarginFi IDL 解码器
├── idl (3).json                      # MarginFi IDL 定义
├── .env                              # 环境变量配置
├── README.md                         # 本文档
└── data/                             # 输出数据目录
```

## 安装

1. 配置 Helius API Key:

```bash
# 编辑 .env 文件
HELIUS_API_KEY=your_api_key_here
```

2. 安装依赖（如需要）:

```bash
pip install aiohttp
```

## 使用方法

### 完整流程

```bash
# 默认扫描 5000 条签名
python main.py

# 指定扫描数量
python main.py --limit 10000

# 指定输出目录
python main.py --limit 5000 --output ./my_data

# 调整并发线程数
python main.py --limit 5000 --workers 8
```

### 分步执行

```bash
# 只执行步骤 1（获取数据）
python main.py --step 1

# 只执行步骤 2（解析数据）
python main.py --step 2 --input ./data/marginfi_liquidations_raw_xxx.json

# 只执行步骤 3（分析数据）
python main.py --step 3 --input ./data/marginfi_liquidations_parsed_xxx.json
```

### 从指定步骤继续

```bash
# 从步骤 2 开始（需要步骤 1 的输出）
python main.py --from 2 --input ./data/marginfi_liquidations_raw_xxx.json

# 从步骤 3 开始（需要步骤 2 的输出）
python main.py --from 3 --input ./data/marginfi_liquidations_parsed_xxx.json
```

### 单独运行各步骤

```bash
# 步骤 1: 获取清算交易
python step1_fetch_liquidations_fast.py --limit 5000

# 步骤 2: 批量解析
python step2_batch_parse.py ./data/marginfi_liquidations_raw_xxx.json

# 步骤 3: 分析数据
python step3_analyze.py ./data/marginfi_liquidations_parsed_xxx.json
```

### 使用解码器

```bash
# 显示所有指令信息
python marginfi_decoder.py --mode info

# 解码单条指令
python marginfi_decoder.py --mode decode --data "<base58_data>"

# 批量处理交易文件
python marginfi_decoder.py --mode batch --file transactions.json
```

## 输出文件

| 步骤 | 文件格式 | 说明 |
|------|----------|------|
| 步骤 1 | `marginfi_liquidations_raw_{timestamp}.json` | 原始交易数据 |
| 步骤 2 | `marginfi_liquidations_parsed_{timestamp}.json` | 解析后的清算交易 |
| 步骤 3 | `marginfi_liquidation_analysis_{timestamp}.json` | 分析报告 |

## 分析报告内容

步骤 3 生成的分析报告包含：

- **统计摘要**
  - 清算人统计（数量、最活跃清算人）
  - 成本统计（Gas 费、Priority Fee）
  - 利润统计（总利润、平均利润）
  - 债务/抵押品代币分布

- **每笔清算详情**
  - 签名、时间、Slot
  - 清算人、被清算人
  - 债务代币和金额
  - 抵押品代币和金额
  - 代币变化
  - 成本明细
  - 利润计算

## API 速率限制

- 默认速率: 8 req/sec（Helius 免费版限制为 10 req/sec）
- 使用令牌桶算法控制请求速率
- 自动重试 429 错误

## 价格数据

- 优先使用 Binance 历史 K 线价格
- 备选使用当前 API 价格

## 支持的代币

| 代币 | Mint 地址 |
|------|-----------|
| SOL | `So11111111111111111111111111111111111111112` |
| USDC | `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` |
| USDT | `Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB` |
| mSOL | `mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So` |
| JitoSOL | `J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn` |
| bSOL | `bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1` |
| stSOL | `7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj` |
| jupSOL | `jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v` |

## 示例输出

```
======================================================================
                    FAST MARGINFI - 清算 MEV 分析
======================================================================

开始时间: 2026-01-30 15:00:00
扫描目标: 5000 条签名
输出目录: ./data

----------------------------------------------------------------------
【步骤 1/3】获取清算交易
----------------------------------------------------------------------
...

----------------------------------------------------------------------
【步骤 2/3】解析清算交易
----------------------------------------------------------------------
...

----------------------------------------------------------------------
【步骤 3/3】分析清算数据
----------------------------------------------------------------------
...

======================================================================
                         流程完成
======================================================================

统计摘要:
  扫描签名数: 5000
  成功交易数: 4500
  清算交易数: 15

利润统计:
  总净利润: $12.3456
  平均净利润: $0.8230

======================================================================
```

## 清算指令结构

MarginFi 清算相关指令:

| 指令 | Discriminator | 说明 |
|------|---------------|------|
| `lending_account_liquidate` | `d6a997d5fba756db` | 执行清算 |
| `start_liquidation` | `f45d5ad6c0a6bf15` | 开始清算流程 |
| `end_liquidation` | `6e0bf436e5b516b8` | 结束清算流程 |

`lending_account_liquidate` 指令参数:

| 参数 | 类型 | 说明 |
|------|------|------|
| asset_amount | u64 | 清算资产金额 |
| liquidatee_accounts | u8 | 被清算人账户数 |
| liquidator_accounts | u8 | 清算人账户数 |

## 与其他协议的区别

| 特性 | MarginFi | Kamino | Jupiter Lend |
|------|----------|--------|--------------|
| 程序 ID | `2jGhuVUuy...` | `KLend2g3c...` | `jupr81Yts...` |
| IDL 格式 | 直接提供 discriminator | 需要计算 discriminator | 直接提供 discriminator |
| 清算模式 | 三阶段 (start/liquidate/end) | 单指令 | 单指令 |
| 账户属性 | writable/signer | isMut/isSigner | writable/signer |
