#!/usr/bin/env python3
"""
步骤 3: Kamino 清算数据分析器

功能：
- 提取被清算人、清算人
- 检测是否使用闪电贷
- 计算成本（协议费用、Gas Fee）
- 计算利润
- 生成统计报告

使用方法：
    python step3_analyze.py <liquidations_json>
    python step3_analyze.py ./data/liquidations_only_20260130.json
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
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {"symbol": "cbBTC", "decimals": 8},
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": {"symbol": "WBTC", "decimals": 8},
}

# 稳定币列表
STABLECOINS = {"USDC", "USDT"}

# Kamino 协议地址
KAMINO_AUTHORITY = "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo"


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
    obligation_owner: str

    # 闪电贷信息
    uses_flash_loan: bool
    flash_loan_amount: float
    flash_loan_token: str
    flash_loan_fee: float

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
    protocol_fee: float
    protocol_fee_token: str

    # 利润
    gross_profit_usd: float
    net_profit_usd: float

    # 清算人所有代币变化
    liquidator_gains: Dict[str, float]

    # 从日志解析的详细信息
    log_data: Dict[str, Any]

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
        "borrow": {},
        "deposit": {},
        "liquidation_params": {},
        "ltv": {},
        "liquidation_bonus_bps": 0,
        "liquidation_result": {},
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

        # 借款信息
        borrow_match = re.search(
            r'Borrow:\s*(\w+)\s*amount:\s*([\d.]+)\s*value:\s*([\d.]+)(?:\s*value_bf:\s*([\d.]+))?',
            log
        )
        if borrow_match:
            token = borrow_match.group(1)
            result["borrow"][token] = {
                "amount_raw": float(borrow_match.group(2)),
                "value_usd": float(borrow_match.group(3)),
                "value_bf": float(borrow_match.group(4)) if borrow_match.group(4) else None
            }
            result["raw_important_logs"].append(log)
            continue

        # 抵押品信息
        deposit_match = re.search(
            r'Deposit:\s*(\w+)\s*amount:\s*([\d.]+)\s*value:\s*([\d.]+)',
            log
        )
        if deposit_match:
            token = deposit_match.group(1)
            result["deposit"][token] = {
                "amount_raw": float(deposit_match.group(2)),
                "value_usd": float(deposit_match.group(3))
            }
            result["raw_important_logs"].append(log)
            continue

        # 清算参数
        params_match = re.search(
            r'liquidation_close_factor_pct:\s*(\d+).*?liquidation_max_value:\s*(\d+)',
            log
        )
        if params_match:
            result["liquidation_params"]["close_factor_pct"] = int(params_match.group(1))
            result["liquidation_params"]["max_value"] = int(params_match.group(2))
            result["raw_important_logs"].append(log)
            continue

        # LTV 信息
        ltv_match = re.search(
            r'LTV:\s*(\d+)%/(\d+)%.*?max_allowed_ltv_user\s*(\d+)%', log)
        if ltv_match:
            result["ltv"]["current"] = int(ltv_match.group(1))
            result["ltv"]["threshold"] = int(ltv_match.group(2))
            result["ltv"]["max_allowed"] = int(ltv_match.group(3))
            result["raw_important_logs"].append(log)
            continue

        # 清算奖励
        bonus_match = re.search(r'liquidation bonus:\s*(\d+)\s*bps', log)
        if bonus_match:
            result["liquidation_bonus_bps"] = int(bonus_match.group(1))
            result["raw_important_logs"].append(log)
            continue

        # 清算结果
        pnl_match = re.search(
            r'Liquidator repaid\s*(\d+)\s*and withdrew\s*(\d+)\s*collateral with fees\s*(\d+)',
            log
        )
        if pnl_match:
            result["liquidation_result"]["repaid_raw"] = int(pnl_match.group(1))
            result["liquidation_result"]["withdrawn_raw"] = int(pnl_match.group(2))
            result["liquidation_result"]["protocol_fees_raw"] = int(pnl_match.group(3))
            result["raw_important_logs"].append(log)
            continue

        # Jupiter Swap 检测
        if "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4" in log and "Route" in log:
            result["is_jupiter_swap"] = True
            continue

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

    kamino_instructions = tx.get("kamino_instructions", [])
    token_changes = tx.get("token_balance_changes", [])

    # 解析原始日志
    raw_logs = tx.get("raw_logs", [])
    log_data = parse_liquidation_logs(raw_logs)

    # 0. 建立 mint 到符号的动态映射
    mint_to_symbol = {}
    collateral_mints = []
    debt_mints = []

    for instr in kamino_instructions:
        if 'liquidate' in instr.get('name', '').lower():
            for acc in instr.get('accounts', []):
                acc_name = acc.get('name', '')
                mint_addr = acc.get('address', '')

                if 'withdrawReserveLiquidityMint' in acc_name:
                    collateral_mints.append(mint_addr)
                elif 'repayReserveLiquidityMint' in acc_name:
                    debt_mints.append(mint_addr)

    collateral_symbols = list(log_data.get('deposit', {}).keys())
    debt_symbols = list(log_data.get('borrow', {}).keys())

    if len(collateral_mints) == 1 and len(collateral_symbols) == 1:
        mint_to_symbol[collateral_mints[0]] = collateral_symbols[0]
    elif len(collateral_mints) == 1 and len(collateral_symbols) > 1:
        for symbol in collateral_symbols:
            if symbol not in debt_symbols:
                mint_to_symbol[collateral_mints[0]] = symbol
                break

    if len(debt_mints) == 1 and len(debt_symbols) == 1:
        mint_to_symbol[debt_mints[0]] = debt_symbols[0]

    # 1. 检测闪电贷
    uses_flash_loan = False
    flash_loan_amount = 0.0
    flash_loan_token = ""
    flash_loan_fee = 0.0

    for instr in kamino_instructions:
        if instr.get("name") == "flashBorrowReserveLiquidity":
            uses_flash_loan = True
            args = instr.get("args", {})
            flash_loan_amount_raw = args.get("liquidityAmount", 0)

            for acc in instr.get("accounts", []):
                if acc.get("name") == "reserveLiquidityMint":
                    mint = acc.get("address", "")
                    flash_loan_token = get_token_symbol(mint)
                    decimals = KNOWN_TOKENS.get(mint, {}).get("decimals", 6)
                    flash_loan_amount = flash_loan_amount_raw / (10 ** decimals)
                    break
            break

    if uses_flash_loan:
        for instr in kamino_instructions:
            if instr.get("name") == "flashRepayReserveLiquidity":
                flash_loan_fee = flash_loan_amount * 0.00001
                break

    # 2. 提取清算人和被清算人
    liquidator = ""
    obligation_owner = ""
    debt_token = ""
    debt_amount = 0.0
    collateral_token = ""
    collateral_received = 0.0

    for instr in kamino_instructions:
        if "liquidate" in instr.get("name", "").lower():
            for acc in instr.get("accounts", []):
                acc_name = acc.get("name", "")
                if "liquidator" in acc_name.lower() and acc.get("is_signer"):
                    liquidator = acc.get("address", "")
                    break

            args = instr.get("args", {})
            liquidity_amount_raw = args.get("liquidityAmount", 0)

            for acc in instr.get("accounts", []):
                if "repayReserveLiquidityMint" in acc.get("name", ""):
                    mint = acc.get("address", "")
                    debt_token = get_token_symbol(mint)
                    decimals = KNOWN_TOKENS.get(mint, {}).get("decimals", 6)
                    debt_amount = liquidity_amount_raw / (10 ** decimals)
                    break

            for acc in instr.get("accounts", []):
                if "withdrawReserveLiquidityMint" in acc.get("name", ""):
                    mint = acc.get("address", "")
                    collateral_token = get_token_symbol(mint)
                    break
            break

    # 从 token_balance_changes 找被清算人
    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        amount_change = change.get("change", 0)

        if owner == liquidator or owner == KAMINO_AUTHORITY:
            continue

        if symbol in STABLECOINS and amount_change < -1:
            obligation_owner = owner
            break

    if not obligation_owner:
        for change in token_changes:
            owner = change.get("owner", "")
            amount_change = change.get("change", 0)

            if owner == liquidator or owner == KAMINO_AUTHORITY:
                continue

            if amount_change < -0.001:
                obligation_owner = owner
                break

    # 清算人收到的抵押品
    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        amount_change = change.get("change", 0)

        if owner == liquidator and amount_change > 0:
            if symbol not in STABLECOINS:
                collateral_received = amount_change
                if not collateral_token or collateral_token.endswith("..."):
                    collateral_token = symbol
                break

    if collateral_received == 0:
        for change in token_changes:
            owner = change.get("owner", "")
            amount_change = change.get("change", 0)

            if owner == liquidator and amount_change > 0:
                collateral_received = amount_change
                break

    # 3. 计算成本
    gas_fee_lamports = tx.get("fee", 0)
    gas_fee_sol = gas_fee_lamports / 1e9

    block_time = tx.get("blockTime", 0)

    oracle_sol_price = log_data.get("prices", {}).get("SOL")
    if oracle_sol_price:
        sol_price_used = oracle_sol_price
        sol_price_source = "oracle"
    elif block_time > 0:
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

    protocol_fee = flash_loan_fee if uses_flash_loan else 0.0
    protocol_fee_token = flash_loan_token if uses_flash_loan else ""

    # 4. 计算利润
    liquidator_gains = {}

    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        mint = change.get("mint", "")
        amount_change = change.get("change", 0)

        if owner == liquidator:
            real_symbol = symbol
            if symbol.endswith("..."):
                if mint in mint_to_symbol:
                    real_symbol = mint_to_symbol[mint]
                elif mint in KNOWN_TOKENS:
                    real_symbol = KNOWN_TOKENS[mint]["symbol"]

            if symbol not in liquidator_gains:
                liquidator_gains[symbol] = {
                    "amount": 0.0,
                    "mint": mint,
                    "real_symbol": real_symbol
                }
            liquidator_gains[symbol]["amount"] += amount_change

    token_prices = log_data.get("prices", {})

    gross_profit_usd = 0.0

    for symbol, token_info in liquidator_gains.items():
        amount = token_info["amount"]
        real_symbol = token_info["real_symbol"]

        if amount == 0:
            continue

        if real_symbol in token_prices:
            price = token_prices[real_symbol]
            gross_profit_usd += amount * price
        elif real_symbol in STABLECOINS:
            gross_profit_usd += amount
        elif real_symbol == "SOL":
            gross_profit_usd += amount * sol_price_used

    net_profit_usd = gross_profit_usd - priority_fee_usd

    # 更新抵押品
    for symbol, token_info in liquidator_gains.items():
        amount = token_info["amount"]
        real_symbol = token_info["real_symbol"]
        if real_symbol not in STABLECOINS and amount > 0:
            collateral_received = amount
            collateral_token = real_symbol
            break

    liquidator_gains_display = {}
    for symbol, token_info in liquidator_gains.items():
        real_symbol = token_info["real_symbol"]
        amount = token_info["amount"]
        liquidator_gains_display[real_symbol] = amount

    return LiquidationAnalysis(
        signature=signature,
        datetime=datetime_str,
        slot=slot,
        success=success,
        liquidator=liquidator,
        obligation_owner=obligation_owner,
        uses_flash_loan=uses_flash_loan,
        flash_loan_amount=flash_loan_amount,
        flash_loan_token=flash_loan_token,
        flash_loan_fee=flash_loan_fee,
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
        protocol_fee=protocol_fee,
        protocol_fee_token=protocol_fee_token,
        gross_profit_usd=gross_profit_usd,
        net_profit_usd=net_profit_usd,
        liquidator_gains=liquidator_gains_display,
        log_data=log_data,
        raw_token_changes=token_changes
    )


# ============================================================================
# 主函数
# ============================================================================

def analyze_liquidations(input_file: str, output_file: Optional[str] = None) -> List[LiquidationAnalysis]:
    """分析清算数据文件"""

    print("=" * 70)
    print("Kamino 清算分析 - 步骤 3: 分析数据")
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
    flash_loan_count = 0
    total_gas_fee = 0.0

    print("\n分析清算交易...")

    for i, tx in enumerate(transactions):
        analysis = analyze_single_liquidation(tx, sol_price)
        results.append(analysis)

        if analysis.uses_flash_loan:
            flash_loan_count += 1
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

    # 闪电贷统计
    if results:
        print(f"\n闪电贷使用:")
        print(f"  使用闪电贷: {flash_loan_count} ({flash_loan_count/len(results)*100:.1f}%)")
        print(f"  不使用闪电贷: {len(results) - flash_loan_count}")

    # 成本和利润统计
    total_gross_profit = sum(r.gross_profit_usd for r in results)
    total_net_profit = sum(r.net_profit_usd for r in results)
    total_flash_fee = sum(r.flash_loan_fee for r in results if r.uses_flash_loan)
    total_priority_fee = sum(r.priority_fee_sol for r in results)
    total_priority_fee_usd = sum(r.priority_fee_usd for r in results)

    print(f"\n成本统计:")
    print(f"  总 Gas 费用: {total_gas_fee:.6f} SOL (${total_gas_fee * sol_price:.2f})")
    if results:
        print(f"  平均 Gas 费用: {total_gas_fee/len(results):.6f} SOL")
    print(f"  总 Priority Fee: {total_priority_fee:.6f} SOL (${total_priority_fee_usd:.4f})")
    print(f"  总闪电贷费用: ${total_flash_fee:.4f}")

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
        print(f"  {token}: {count} ({count/len(results)*100:.1f}%)" if results else f"  {token}: {count}")

    # 抵押品代币统计
    collateral_tokens = {}
    for r in results:
        if r.collateral_token:
            collateral_tokens[r.collateral_token] = collateral_tokens.get(r.collateral_token, 0) + 1

    print(f"\n抵押品代币分布:")
    for token, count in sorted(collateral_tokens.items(), key=lambda x: -x[1]):
        print(f"  {token}: {count} ({count/len(results)*100:.1f}%)" if results else f"  {token}: {count}")

    # 详细记录（前10条）
    print("\n" + "=" * 70)
    print("详细清算记录 (前 10 条)")
    print("=" * 70)

    for i, r in enumerate(results[:10]):
        print(f"\n--- 清算 #{i+1} ---")
        print(f"  签名: {r.signature[:32]}...")
        print(f"  时间: {r.datetime}")
        print(f"  清算人: {r.liquidator[:24]}..." if r.liquidator else "  清算人: 未知")
        print(f"  被清算人: {r.obligation_owner[:24]}..." if r.obligation_owner else "  被清算人: 未知")
        print(f"  闪电贷: {'是' if r.uses_flash_loan else '否'}", end="")
        if r.uses_flash_loan:
            print(f" ({r.flash_loan_amount:.2f} {r.flash_loan_token}, 费用: {r.flash_loan_fee:.4f})")
        else:
            print()

        log = r.log_data
        if log.get("prices"):
            print(f"  价格信息:")
            for token, price in log["prices"].items():
                print(f"    {token}: ${price:.4f}")

        if log.get("liquidation_bonus_bps"):
            print(f"  清算奖励: {log['liquidation_bonus_bps']} bps ({log['liquidation_bonus_bps']/100:.2f}%)")

        print(f"  清算人代币变化:")
        for token, amount in r.liquidator_gains.items():
            direction = "+" if amount > 0 else ""
            print(f"    {direction}{amount:.6f} {token}")
        print(f"  成本:")
        source_map = {"oracle": "预言机", "historical": "历史价格", "api_current": "当前API"}
        price_source = source_map.get(r.sol_price_source, r.sol_price_source)
        print(f"    Gas: {r.gas_fee_sol:.6f} SOL (${r.gas_fee_usd:.4f}) [SOL=${r.sol_price_used:.2f} from {price_source}]")
        print(f"    Priority Fee: {r.priority_fee_lamports} lamports ({r.priority_fee_sol:.6f} SOL)")
        if r.uses_flash_loan:
            print(f"    闪电贷费用: {r.flash_loan_fee:.4f} {r.flash_loan_token}")
        print(f"  利润:")
        print(f"    毛利润: ${r.gross_profit_usd:.4f}")
        print(f"    净利润: ${r.net_profit_usd:.4f}")

    # 保存结果
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = Path(input_file).parent / f"liquidation_analysis_{timestamp}.json"

    output_data = {
        "metadata": {
            "input_file": str(input_file),
            "total_liquidations": len(results),
            "flash_loan_count": flash_loan_count,
            "flash_loan_percentage": flash_loan_count / len(results) * 100 if results else 0,
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
        print("示例: python step3_analyze.py ./data/liquidations_only_20260130.json")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    analyze_liquidations(input_file, output_file)


if __name__ == "__main__":
    main()
