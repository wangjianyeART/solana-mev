#!/usr/bin/env python3
"""
步骤 3: Jupiter Lend 清算数据分析器

功能：
- 提取被清算人、清算人
- 计算成本（Gas Fee、Priority Fee）
- 计算利润
- 生成统计报告

使用方法：
    python step3_analyze.py <liquidations_json>
    python step3_analyze.py ./data/jupiter_liquidations_parsed_20260130.json
"""

import re
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import urllib.request


# ============================================================================
# 配置
# ============================================================================

# 已知 Token 信息
KNOWN_TOKENS = {
    "So11111111111111111111111111111111111111112": {"symbol": "SOL", "decimals": 9},
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "decimals": 6},
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {"symbol": "USDT", "decimals": 6},
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {"symbol": "mSOL", "decimals": 9},
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {"symbol": "JitoSOL", "decimals": 9},
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": {"symbol": "bSOL", "decimals": 9},
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": {"symbol": "stSOL", "decimals": 9},
    "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v": {"symbol": "jupSOL", "decimals": 9},
    "JuprjznTrTSp2UFa3ZBUFgwdAmtZCq4MQCwysN55USD": {"symbol": "jupUSD", "decimals": 6},
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {"symbol": "cbBTC", "decimals": 8},
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": {"symbol": "WBTC", "decimals": 8},
}

# 稳定币列表
STABLECOINS = {"USDC", "USDT", "jupUSD"}

# Jupiter Vaults Authority
JUPITER_VAULTS_AUTHORITY = "7s1da8DduuBFqGra5bJBjpnvL5E9mGzCuMk1Qkh4or2Z"


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class LiquidationAnalysis:
    """清算分析结果"""
    signature: str
    datetime: str
    slot: int
    success: bool

    # 参与方
    liquidator: str
    position_owner: str

    # 债务信息
    debt_token: str
    debt_amount: float

    # 抵押品信息
    collateral_token: str
    collateral_received: float

    # 成本
    gas_fee_sol: float
    gas_fee_usd: float
    priority_fee_lamports: int
    priority_fee_sol: float
    priority_fee_usd: float
    sol_price_used: float
    sol_price_source: str

    # 利润
    gross_profit_usd: float
    net_profit_usd: float

    # 清算人所有代币变化
    liquidator_gains: Dict[str, float]

    # 原始数据
    raw_token_changes: List[Dict]


# ============================================================================
# 工具函数
# ============================================================================

def get_token_symbol(mint: str) -> str:
    """获取 Token 符号"""
    if mint in KNOWN_TOKENS:
        return KNOWN_TOKENS[mint]["symbol"]
    return mint[:8] + "..."


def parse_liquidation_logs(raw_logs: List[str]) -> Dict[str, Any]:
    """从原始日志中解析清算详细信息"""
    result = {
        "prices": {},
        "is_jupiter_swap": False,
        "raw_important_logs": []
    }

    for log in raw_logs:
        # 价格信息
        price_match = re.search(r'Token:\s*(\w+)\s*Price:\s*([\d.]+)', log)
        if price_match:
            token = price_match.group(1)
            price = float(price_match.group(2))
            result["prices"][token] = price
            result["raw_important_logs"].append(log)
            continue

        # Jupiter Swap 检测
        if "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4" in log and "Route" in log:
            result["is_jupiter_swap"] = True
            continue

        # 清算指令检测
        if "Instruction: Liquidate" in log:
            result["raw_important_logs"].append(log)

    return result


# ============================================================================
# 价格获取
# ============================================================================

_price_cache: Dict[int, float] = {}


def fetch_sol_price() -> float:
    """获取 SOL 当前价格"""
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode())
            return float(data["price"])
    except:
        return 250.0


def fetch_historical_sol_price(timestamp: int) -> float:
    """根据时间戳获取历史 SOL 价格"""
    cache_key = timestamp // 60 * 60

    if cache_key in _price_cache:
        return _price_cache[cache_key]

    try:
        start_time = cache_key * 1000
        end_time = (cache_key + 60) * 1000

        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol=SOLUSDT&interval=1m&startTime={start_time}&endTime={end_time}&limit=1"
        )

        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            if data and len(data) > 0:
                close_price = float(data[0][4])
                _price_cache[cache_key] = close_price
                return close_price
    except:
        pass

    return 250.0


# ============================================================================
# 分析函数
# ============================================================================

def analyze_single_liquidation(tx: Dict[str, Any], sol_price: float = 250.0) -> LiquidationAnalysis:
    """分析单个清算交易"""

    signature = tx.get("signature", "")
    datetime_str = tx.get("datetime", "")
    slot = tx.get("slot", 0)
    success = tx.get("success", False)

    jupiter_instructions = tx.get("jupiter_instructions", [])
    token_changes = tx.get("token_balance_changes", [])
    liquidation_details = tx.get("liquidation_details", {}) or {}

    # 解析原始日志
    raw_logs = tx.get("raw_logs", [])
    log_data = parse_liquidation_logs(raw_logs)

    # 1. 提取清算人和被清算人
    liquidator = liquidation_details.get("liquidator", "")
    position_owner = ""
    debt_token = ""
    debt_amount = liquidation_details.get("debt_amount", 0.0)
    collateral_token = liquidation_details.get("collateral_token", "")
    collateral_received = liquidation_details.get("collateral_amount", 0.0)

    # 从指令中获取更多信息
    for instr in jupiter_instructions:
        if instr.get("type") == "liquidation":
            accounts = instr.get("accounts", [])
            args = instr.get("args", {})

            # 从账户中获取清算人
            for acc in accounts:
                name = acc.get("name", "")
                if name == "signer":
                    liquidator = acc.get("address", "")
                elif name == "supply_token":
                    addr = acc.get("address", "")
                    collateral_token = get_token_symbol(addr)
                elif name == "borrow_token":
                    addr = acc.get("address", "")
                    debt_token = get_token_symbol(addr)

            # 从参数中获取债务金额
            if "debt_amt" in args and debt_amount == 0:
                # 根据代币类型确定小数位
                if debt_token in ["jupUSD", "USDC", "USDT"]:
                    debt_amount = args["debt_amt"] / 1e6
                else:
                    debt_amount = args["debt_amt"] / 1e9

    # 从 token_balance_changes 中获取更多信息
    # 找被清算人（损失抵押品的人）
    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        amount_change = change.get("change", 0)

        if owner == liquidator or owner == JUPITER_VAULTS_AUTHORITY:
            continue

        # 损失抵押品的人（jupSOL 等）
        if amount_change < -0.001 and symbol in ["jupSOL", "SOL", "mSOL", "stSOL", "JitoSOL", "bSOL"]:
            position_owner = owner
            break

    # 找清算人收到的抵押品
    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        amount_change = change.get("change", 0)

        if owner == liquidator and amount_change > 0:
            if symbol not in STABLECOINS:
                if collateral_received == 0:
                    collateral_received = amount_change
                if not collateral_token or collateral_token.endswith("..."):
                    collateral_token = symbol
                break

    # 2. 计算成本
    gas_fee_lamports = tx.get("fee", 0)
    gas_fee_sol = gas_fee_lamports / 1e9

    block_time = tx.get("blockTime", 0)

    # 优先使用历史价格
    if block_time > 0:
        sol_price_used = fetch_historical_sol_price(block_time)
        sol_price_source = "historical"
    else:
        sol_price_used = sol_price
        sol_price_source = "api_current"

    gas_fee_usd = gas_fee_sol * sol_price_used

    BASE_SIGNATURE_FEE = 5000
    priority_fee_lamports = max(0, gas_fee_lamports - BASE_SIGNATURE_FEE)
    priority_fee_sol = priority_fee_lamports / 1e9
    priority_fee_usd = priority_fee_sol * sol_price_used

    # 3. 计算利润
    liquidator_gains = {}

    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        mint = change.get("mint", "")
        amount_change = change.get("change", 0)

        if owner == liquidator:
            real_symbol = symbol
            if symbol.endswith("...") and mint in KNOWN_TOKENS:
                real_symbol = KNOWN_TOKENS[mint]["symbol"]

            if real_symbol not in liquidator_gains:
                liquidator_gains[real_symbol] = 0.0
            liquidator_gains[real_symbol] += amount_change

    # 计算毛利润
    gross_profit_usd = 0.0

    for symbol, amount in liquidator_gains.items():
        if amount == 0:
            continue

        if symbol in STABLECOINS:
            gross_profit_usd += amount
        elif symbol == "SOL":
            gross_profit_usd += amount * sol_price_used
        elif symbol == "jupSOL":
            # jupSOL 价值约等于 SOL
            gross_profit_usd += amount * sol_price_used
        elif symbol in ["mSOL", "stSOL", "JitoSOL", "bSOL"]:
            # LST 价值约等于 SOL
            gross_profit_usd += amount * sol_price_used

    net_profit_usd = gross_profit_usd - priority_fee_usd

    return LiquidationAnalysis(
        signature=signature,
        datetime=datetime_str,
        slot=slot,
        success=success,
        liquidator=liquidator,
        position_owner=position_owner,
        debt_token=debt_token,
        debt_amount=debt_amount,
        collateral_token=collateral_token,
        collateral_received=collateral_received,
        gas_fee_sol=gas_fee_sol,
        gas_fee_usd=gas_fee_usd,
        priority_fee_lamports=priority_fee_lamports,
        priority_fee_sol=priority_fee_sol,
        priority_fee_usd=priority_fee_usd,
        sol_price_used=sol_price_used,
        sol_price_source=sol_price_source,
        gross_profit_usd=gross_profit_usd,
        net_profit_usd=net_profit_usd,
        liquidator_gains=liquidator_gains,
        raw_token_changes=token_changes
    )


# ============================================================================
# 主函数
# ============================================================================

def analyze_liquidations(input_file: str, output_file: Optional[str] = None) -> List[LiquidationAnalysis]:
    """分析清算数据文件"""

    print("=" * 70)
    print("Jupiter Lend - 步骤 3: 分析数据")
    print("=" * 70)

    # 加载数据
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 处理不同格式
    if isinstance(data, list):
        transactions = data
    elif isinstance(data, dict):
        transactions = data.get("transactions", [])
    else:
        print("错误: 无法识别的数据格式")
        return []

    print(f"\n输入文件: {input_file}")
    print(f"清算交易数: {len(transactions)}")

    # 获取 SOL 价格
    print("\n获取 SOL 价格...")
    sol_price = fetch_sol_price()
    print(f"SOL 价格: ${sol_price:.2f}")

    # 分析每个清算
    results = []
    total_gas_fee = 0.0

    print("\n分析清算交易...")

    for i, tx in enumerate(transactions):
        analysis = analyze_single_liquidation(tx, sol_price)
        results.append(analysis)
        total_gas_fee += analysis.gas_fee_sol

        if (i + 1) % 10 == 0:
            print(f"   处理: {i + 1}/{len(transactions)}")

    # 统计摘要
    print("\n" + "=" * 70)
    print("统计摘要")
    print("=" * 70)

    # 清算人统计
    liquidators = {}
    for r in results:
        if r.liquidator:
            liquidators[r.liquidator] = liquidators.get(r.liquidator, 0) + 1

    print(f"\n清算人统计:")
    print(f"  唯一清算人数: {len(liquidators)}")
    print(f"  最活跃清算人:")
    for liq, count in sorted(liquidators.items(), key=lambda x: -x[1])[:5]:
        print(f"    {liq[:16]}... : {count} 次")

    # 成本和利润统计
    total_gross_profit = sum(r.gross_profit_usd for r in results)
    total_net_profit = sum(r.net_profit_usd for r in results)
    total_priority_fee = sum(r.priority_fee_sol for r in results)
    total_priority_fee_usd = sum(r.priority_fee_usd for r in results)

    print(f"\n成本统计:")
    print(f"  总 Gas 费用: {total_gas_fee:.6f} SOL (${total_gas_fee * sol_price:.2f})")
    if results:
        print(f"  平均 Gas 费用: {total_gas_fee/len(results):.6f} SOL")
    print(f"  总 Priority Fee: {total_priority_fee:.6f} SOL (${total_priority_fee_usd:.4f})")

    print(f"\n利润统计:")
    print(f"  总毛利润: ${total_gross_profit:.4f}")
    print(f"  总净利润: ${total_net_profit:.4f}")
    if results:
        print(f"  平均净利润: ${total_net_profit/len(results):.4f}")

    # 债务代币统计
    debt_tokens = {}
    for r in results:
        if r.debt_token:
            debt_tokens[r.debt_token] = debt_tokens.get(r.debt_token, 0) + 1

    print(f"\n债务代币分布:")
    for token, count in sorted(debt_tokens.items(), key=lambda x: -x[1]):
        pct = count/len(results)*100 if results else 0
        print(f"  {token}: {count} ({pct:.1f}%)")

    # 抵押品代币统计
    collateral_tokens = {}
    for r in results:
        if r.collateral_token:
            collateral_tokens[r.collateral_token] = collateral_tokens.get(r.collateral_token, 0) + 1

    print(f"\n抵押品代币分布:")
    for token, count in sorted(collateral_tokens.items(), key=lambda x: -x[1]):
        pct = count/len(results)*100 if results else 0
        print(f"  {token}: {count} ({pct:.1f}%)")

    # 详细记录（前10条）
    print("\n" + "=" * 70)
    print("详细清算记录 (前 10 条)")
    print("=" * 70)

    for i, r in enumerate(results[:10]):
        print(f"\n--- 清算 #{i+1} ---")
        print(f"  签名: {r.signature[:32]}...")
        print(f"  时间: {r.datetime}")
        print(f"  清算人: {r.liquidator[:24]}..." if r.liquidator else "  清算人: 未知")
        print(f"  被清算人: {r.position_owner[:24]}..." if r.position_owner else "  被清算人: 未知")
        print(f"  债务: {r.debt_amount:.4f} {r.debt_token}")
        print(f"  抵押品: {r.collateral_received:.6f} {r.collateral_token}")
        print(f"  清算人代币变化:")
        for token, amount in r.liquidator_gains.items():
            direction = "+" if amount > 0 else ""
            print(f"    {direction}{amount:.6f} {token}")
        print(f"  成本:")
        source_map = {"historical": "历史价格", "api_current": "当前API"}
        price_source = source_map.get(r.sol_price_source, r.sol_price_source)
        print(f"    Gas: {r.gas_fee_sol:.6f} SOL (${r.gas_fee_usd:.4f}) [SOL=${r.sol_price_used:.2f} from {price_source}]")
        print(f"    Priority Fee: {r.priority_fee_lamports} lamports ({r.priority_fee_sol:.6f} SOL)")
        print(f"  利润:")
        print(f"    毛利润: ${r.gross_profit_usd:.4f}")
        print(f"    净利润: ${r.net_profit_usd:.4f}")

    # 保存结果
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = Path(input_file).parent / f"jupiter_liquidation_analysis_{timestamp}.json"

    output_data = {
        "metadata": {
            "protocol": "Jupiter Lend",
            "input_file": str(input_file),
            "total_liquidations": len(results),
            "total_gas_fee_sol": total_gas_fee,
            "total_gross_profit_usd": total_gross_profit,
            "total_net_profit_usd": total_net_profit,
            "unique_liquidators": len(liquidators),
            "sol_price_usd": sol_price,
        },
        "liquidators": liquidators,
        "debt_tokens": debt_tokens,
        "collateral_tokens": collateral_tokens,
        "liquidations": [asdict(r) for r in results]
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"\n分析结果已保存: {output_file}")

    print("\n" + "=" * 70)
    print("步骤 3 完成!")
    print("=" * 70)

    return results


def main():
    if len(sys.argv) < 2:
        print("用法: python step3_analyze.py <liquidations_json>")
        print("示例: python step3_analyze.py ./data/jupiter_liquidations_parsed_20260130.json")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    analyze_liquidations(input_file, output_file)


if __name__ == "__main__":
    main()
