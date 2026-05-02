#!/usr/bin/env python3
"""
Kamino 清算数据分析器

功能：
- 提取被清算人、清算人
- 检测是否使用闪电贷
- 计算成本（协议费用、Gas Fee）
- 计算利润

使用方法：
    python analyze_liquidations.py <liquidations_json>
"""

import re
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
import urllib.request

# 已知 Token 信息（常用代币的静态映射，作为动态映射的补充）
# 注意：程序会优先使用从清算指令+日志中动态解析的映射，此列表仅作为后备
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

# 稳定币列表（用于识别债务代币）
STABLECOINS = {"USDC", "USDT"}

# Kamino 协议地址（Lending Market Authority）
KAMINO_AUTHORITY = "9DrvZvyWh1HuAoZxvYWMvkf2XCzryCpGgHqrMjyDWpmo"


@dataclass
class LiquidationAnalysis:
    """清算分析结果"""
    signature: str
    datetime: str
    slot: int
    success: bool

    # 参与方
    liquidator: str                    # 清算人
    obligation_owner: str              # 被清算人

    # 闪电贷信息
    uses_flash_loan: bool              # 是否使用闪电贷
    flash_loan_amount: float           # 闪电贷金额
    flash_loan_token: str              # 闪电贷代币
    flash_loan_fee: float              # 闪电贷费用 (0.001%)

    # 债务信息
    debt_token: str                    # 债务代币
    debt_amount: float                 # 代偿债务金额

    # 抵押品信息
    collateral_token: str              # 抵押品代币
    collateral_received: float         # 清算人收到的抵押品

    # 成本
    gas_fee_sol: float                 # Gas 费用 (SOL)
    gas_fee_usd: float                 # Gas 费用 (USD)
    priority_fee_lamports: int         # Priority Fee (lamports)
    priority_fee_sol: float            # Priority Fee (SOL)
    priority_fee_usd: float            # Priority Fee (USD)
    sol_price_used: float              # 计算 Gas 费用时使用的 SOL 价格
    sol_price_source: str              # SOL 价格来源 (oracle/api)
    protocol_fee: float                # 协议费用 (闪电贷费用)
    protocol_fee_token: str            # 协议费用代币

    # 利润
    gross_profit_usd: float            # 毛利润 (稳定币净收益)
    net_profit_usd: float              # 净利润 (毛利润 - Gas)

    # 清算人所有代币变化
    liquidator_gains: Dict[str, float]

    # 从日志解析的详细信息
    log_data: Dict[str, Any]           # 日志解析数据

    # 原始数据
    raw_token_changes: List[Dict]


def get_token_symbol(mint: str) -> str:
    """获取 Token 符号"""
    if mint in KNOWN_TOKENS:
        return KNOWN_TOKENS[mint]["symbol"]
    return mint[:8] + "..."


def parse_liquidation_logs(raw_logs: List[str]) -> Dict[str, Any]:
    """
    从原始日志中解析清算详细信息

    可解析的信息：
    - 代币价格 (Token: XXX Price: YYY)
    - 借款信息 (Borrow: XXX amount: YYY value: ZZZ)
    - 抵押品信息 (Deposit: XXX amount: YYY value: ZZZ)
    - 清算参数 (liquidation_close_factor_pct, liquidation_max_value)
    - LTV 信息 (LTV: XX%/YY%)
    - 清算奖励 (liquidation bonus: XXX bps)
    - 清算结果 (Liquidator repaid XXX and withdrew YYY collateral with fees ZZZ)
    """
    result = {
        "prices": {},              # {token: price}
        "borrow": {},              # {token: {amount, value, value_bf}}
        "deposit": {},             # {token: {amount, value}}
        "liquidation_params": {},  # {close_factor_pct, max_value}
        "ltv": {},                 # {current, threshold, max_allowed}
        "liquidation_bonus_bps": 0,
        "liquidation_result": {},  # {repaid, withdrawn, fees}
        "is_jupiter_swap": False,
        "raw_important_logs": []
    }

    for log in raw_logs:
        # 价格信息: "Token: USDC Price: 0.9997"
        price_match = re.search(r'Token:\s*(\w+)\s*Price:\s*([\d.]+)', log)
        if price_match:
            token = price_match.group(1)
            price = float(price_match.group(2))
            result["prices"][token] = price
            result["raw_important_logs"].append(log)
            continue

        # 借款信息: "Borrow: USDC amount: 350923632.2090 value: 350.8111 value_bf: 350.8111"
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

        # 抵押品信息: "Deposit: cbBTC amount: 520472.0084 value: 438.1339"
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

        # 清算参数: "Liquidating liquidation_close_factor_pct: 10, liquidation_max_value: 2500000"
        params_match = re.search(
            r'liquidation_close_factor_pct:\s*(\d+).*?liquidation_max_value:\s*(\d+)',
            log
        )
        if params_match:
            result["liquidation_params"]["close_factor_pct"] = int(
                params_match.group(1))
            result["liquidation_params"]["max_value"] = int(
                params_match.group(2))
            result["raw_important_logs"].append(log)
            continue

        # LTV 信息: "LTV: 80%/80%, max_allowed_ltv_user 80%"
        ltv_match = re.search(
            r'LTV:\s*(\d+)%/(\d+)%.*?max_allowed_ltv_user\s*(\d+)%', log)
        if ltv_match:
            result["ltv"]["current"] = int(ltv_match.group(1))
            result["ltv"]["threshold"] = int(ltv_match.group(2))
            result["ltv"]["max_allowed"] = int(ltv_match.group(3))
            result["raw_important_logs"].append(log)
            continue

        # 清算奖励: "liquidation bonus: 100bps" 或 "liquidation bonus: 100 bps"
        bonus_match = re.search(r'liquidation bonus:\s*(\d+)\s*bps', log)
        if bonus_match:
            result["liquidation_bonus_bps"] = int(bonus_match.group(1))
            result["raw_important_logs"].append(log)
            continue

        # 清算结果: "Liquidator repaid 35092364 and withdrew 42089 collateral with fees 1"
        pnl_match = re.search(
            r'Liquidator repaid\s*(\d+)\s*and withdrew\s*(\d+)\s*collateral with fees\s*(\d+)',
            log
        )
        if pnl_match:
            result["liquidation_result"]["repaid_raw"] = int(
                pnl_match.group(1))
            result["liquidation_result"]["withdrawn_raw"] = int(
                pnl_match.group(2))
            result["liquidation_result"]["protocol_fees_raw"] = int(
                pnl_match.group(3))
            result["raw_important_logs"].append(log)
            continue

        # Jupiter Swap 检测
        if "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4" in log and "Route" in log:
            result["is_jupiter_swap"] = True
            continue

        # 借款价值信息: "borrowed value (scaled): 350.8111, unhealthy borrow value (scaled): 350.5071"
        borrow_value_match = re.search(
            r'borrowed value.*?:\s*([\d.]+).*?unhealthy borrow value.*?:\s*([\d.]+)',
            log
        )
        if borrow_value_match:
            result["ltv"]["borrowed_value"] = float(
                borrow_value_match.group(1))
            result["ltv"]["unhealthy_threshold"] = float(
                borrow_value_match.group(2))
            continue

    return result


def fetch_sol_price() -> float:
    """获取 SOL 当前价格"""
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT"
        with urllib.request.urlopen(url, timeout=5) as response:
            data = json.loads(response.read().decode())
            return float(data["price"])
    except:
        return 250.0  # 默认价格


# 缓存历史价格，避免重复请求
_price_cache: Dict[int, float] = {}


def fetch_historical_sol_price(timestamp: int) -> float:
    """
    根据时间戳获取历史 SOL 价格

    使用 Binance Klines API 获取指定时间的分钟 K 线收盘价

    Args:
        timestamp: Unix 时间戳（秒）

    Returns:
        当时的 SOL 价格（USD）
    """
    # 四舍五入到分钟，用于缓存
    cache_key = timestamp // 60 * 60

    if cache_key in _price_cache:
        return _price_cache[cache_key]

    try:
        # Binance Klines API 需要毫秒时间戳
        start_time = cache_key * 1000
        end_time = (cache_key + 60) * 1000

        url = (
            f"https://api.binance.com/api/v3/klines"
            f"?symbol=SOLUSDT&interval=1m&startTime={start_time}&endTime={end_time}&limit=1"
        )

        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode())
            if data and len(data) > 0:
                # Kline 格式: [open_time, open, high, low, close, ...]
                close_price = float(data[0][4])
                _price_cache[cache_key] = close_price
                return close_price
    except Exception as e:
        pass

    # 如果获取失败，返回默认价格
    return 250.0


def analyze_single_liquidation(tx: Dict[str, Any], sol_price: float = 250.0) -> LiquidationAnalysis:
    """分析单个清算交易"""

    signature = tx.get("signature", "")
    datetime_str = tx.get("datetime", "")
    slot = tx.get("slot", 0)
    success = tx.get("success", False)

    kamino_instructions = tx.get("kamino_instructions", [])
    token_changes = tx.get("token_balance_changes", [])
    sol_changes = tx.get("sol_balance_changes", [])

    # 解析原始日志
    raw_logs = tx.get("raw_logs", [])
    log_data = parse_liquidation_logs(raw_logs)

    # ========================================
    # 0. 从清算指令和日志建立 mint 到符号的动态映射
    # ========================================
    mint_to_symbol = {}  # {mint_address: symbol}

    # 从清算指令中提取 mint 地址
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

    # 从日志中找到抵押品代币符号
    collateral_symbols = []
    debt_symbols = []

    for token, info in log_data.get('deposit', {}).items():
        collateral_symbols.append(token)

    for token, info in log_data.get('borrow', {}).items():
        debt_symbols.append(token)

    # 建立映射：如果只有一个抵押品/债务，直接匹配
    if len(collateral_mints) == 1 and len(collateral_symbols) == 1:
        mint_to_symbol[collateral_mints[0]] = collateral_symbols[0]
    elif len(collateral_mints) == 1 and len(collateral_symbols) > 1:
        # 多个抵押品时，使用第一个非债务代币
        for symbol in collateral_symbols:
            if symbol not in debt_symbols:
                mint_to_symbol[collateral_mints[0]] = symbol
                break

    if len(debt_mints) == 1 and len(debt_symbols) == 1:
        mint_to_symbol[debt_mints[0]] = debt_symbols[0]

    # ========================================
    # 1. 检测闪电贷
    # ========================================
    uses_flash_loan = False
    flash_loan_amount = 0.0
    flash_loan_token = ""
    flash_loan_fee = 0.0

    for instr in kamino_instructions:
        if instr.get("name") == "flashBorrowReserveLiquidity":
            uses_flash_loan = True
            args = instr.get("args", {})
            flash_loan_amount_raw = args.get("liquidityAmount", 0)

            # 找到闪电贷的代币
            for acc in instr.get("accounts", []):
                if acc.get("name") == "reserveLiquidityMint":
                    mint = acc.get("address", "")
                    flash_loan_token = get_token_symbol(mint)
                    # 根据代币获取 decimals
                    decimals = KNOWN_TOKENS.get(mint, {}).get("decimals", 6)
                    flash_loan_amount = flash_loan_amount_raw / \
                        (10 ** decimals)
                    break
            break

    # 计算闪电贷费用（从 flashRepay 指令中）
    if uses_flash_loan:
        for instr in kamino_instructions:
            if instr.get("name") == "flashRepayReserveLiquidity":
                # 闪电贷费用通常是 0.001%
                flash_loan_fee = flash_loan_amount * 0.00001
                break

    # ========================================
    # 2. 提取清算人和被清算人
    # ========================================
    liquidator = ""
    obligation_owner = ""
    debt_token = ""
    debt_amount = 0.0
    collateral_token = ""
    collateral_received = 0.0

    # 从清算指令中找清算人
    for instr in kamino_instructions:
        if "liquidate" in instr.get("name", "").lower():
            for acc in instr.get("accounts", []):
                acc_name = acc.get("name", "")
                if "liquidator" in acc_name.lower() and acc.get("is_signer"):
                    liquidator = acc.get("address", "")
                    break

            # 获取清算金额
            args = instr.get("args", {})
            liquidity_amount_raw = args.get("liquidityAmount", 0)

            # 找到债务代币
            for acc in instr.get("accounts", []):
                if "repayReserveLiquidityMint" in acc.get("name", ""):
                    mint = acc.get("address", "")
                    debt_token = get_token_symbol(mint)
                    decimals = KNOWN_TOKENS.get(mint, {}).get("decimals", 6)
                    debt_amount = liquidity_amount_raw / (10 ** decimals)
                    break

            # 找到抵押品代币
            for acc in instr.get("accounts", []):
                if "withdrawReserveLiquidityMint" in acc.get("name", ""):
                    mint = acc.get("address", "")
                    collateral_token = get_token_symbol(mint)
                    break
            break

    # 从 token_balance_changes 分析
    # 被清算人：稳定币大幅减少的人（不是清算人，不是 Kamino 协议）
    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        amount_change = change.get("change", 0)

        # 排除清算人和协议
        if owner == liquidator or owner == KAMINO_AUTHORITY:
            continue

        # 找稳定币大幅减少的人
        if symbol in STABLECOINS and amount_change < -1:
            obligation_owner = owner
            break

    # 如果没找到，用其他代币减少的人
    if not obligation_owner:
        for change in token_changes:
            owner = change.get("owner", "")
            amount_change = change.get("change", 0)

            if owner == liquidator or owner == KAMINO_AUTHORITY:
                continue

            if amount_change < -0.001:  # 有代币减少
                obligation_owner = owner
                break

    # 清算人收到的抵押品
    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        amount_change = change.get("change", 0)

        if owner == liquidator and amount_change > 0:
            # 优先找非稳定币（抵押品通常是 SOL 系列）
            if symbol not in STABLECOINS:
                collateral_received = amount_change
                if not collateral_token or collateral_token.endswith("..."):
                    collateral_token = symbol
                break

    # 如果抵押品是稳定币
    if collateral_received == 0:
        for change in token_changes:
            owner = change.get("owner", "")
            amount_change = change.get("change", 0)

            if owner == liquidator and amount_change > 0:
                collateral_received = amount_change
                break

    # ========================================
    # 3. 计算成本
    # ========================================
    # Gas 费用 - 优先使用日志中的预言机 SOL 价格，其次使用历史价格
    gas_fee_lamports = tx.get("fee", 0)
    gas_fee_sol = gas_fee_lamports / 1e9

    # 获取交易时间戳
    block_time = tx.get("blockTime", 0)

    # 优先级：1. 日志中的预言机价格  2. 根据时间戳获取的历史价格
    oracle_sol_price = log_data.get("prices", {}).get("SOL")
    if oracle_sol_price:
        sol_price_used = oracle_sol_price
        sol_price_source = "oracle"
    elif block_time > 0:
        # 使用 Binance 历史价格
        sol_price_used = fetch_historical_sol_price(block_time)
        sol_price_source = "historical"
    else:
        sol_price_used = sol_price
        sol_price_source = "api_current"

    gas_fee_usd = gas_fee_sol * sol_price_used

    # Priority Fee（优先费用 = 总费用 - 基础签名费用 5000 lamports）
    BASE_SIGNATURE_FEE = 5000
    priority_fee_lamports = max(0, gas_fee_lamports - BASE_SIGNATURE_FEE)
    priority_fee_sol = priority_fee_lamports / 1e9
    priority_fee_usd = priority_fee_sol * sol_price_used

    # 协议费用（闪电贷费用，通常是 0.001%）
    protocol_fee = flash_loan_fee if uses_flash_loan else 0.0
    protocol_fee_token = flash_loan_token if uses_flash_loan else ""

    # ========================================
    # 4. 计算利润（基于清算人的所有 token 变化）
    # ========================================

    # 清算人的所有 token 净变化（包含 mint 地址映射）
    liquidator_gains = {}  # {symbol: {"amount": float, "mint": str, "real_symbol": str}}

    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        mint = change.get("mint", "")
        amount_change = change.get("change", 0)

        if owner == liquidator:
            # 获取真实符号的优先级：
            # 1. 动态映射（从清算指令+日志）
            # 2. KNOWN_TOKENS（静态映射）
            # 3. 原始符号
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

    # 从日志中获取价格信息
    token_prices = log_data.get("prices", {})

    # 计算所有代币的 USD 价值变化
    gross_profit_usd = 0.0
    token_profits_breakdown = {}  # {symbol: usd_value}

    for symbol, token_info in liquidator_gains.items():
        amount = token_info["amount"]
        real_symbol = token_info["real_symbol"]

        if amount == 0:
            continue

        # 优先使用真实符号在日志中查找价格
        if real_symbol in token_prices:
            price = token_prices[real_symbol]
            usd_value = amount * price
            gross_profit_usd += usd_value
            token_profits_breakdown[symbol] = usd_value
        # 稳定币按 1:1 计算
        elif real_symbol in STABLECOINS:
            gross_profit_usd += amount
            token_profits_breakdown[symbol] = amount
        # SOL 使用当前价格
        elif real_symbol == "SOL":
            usd_value = amount * sol_price_used
            gross_profit_usd += usd_value
            token_profits_breakdown[symbol] = usd_value
        # 其他代币如果没有价格信息，记录但不计入利润
        else:
            token_profits_breakdown[symbol] = 0.0

    # 净利润 = 毛利润 - Priority Fee
    # 注意：
    # 1. 闪电贷费用已包含在 USDC 变化中
    # 2. 基础签名费用是网络固定成本，不计入
    # 3. 只有 Priority Fee 是清算人的主动成本
    net_profit_usd = gross_profit_usd - priority_fee_usd

    # 更新抵押品收到的数量（从非稳定币中获取）
    for symbol, token_info in liquidator_gains.items():
        amount = token_info["amount"]
        real_symbol = token_info["real_symbol"]
        if real_symbol not in STABLECOINS and amount > 0:
            collateral_received = amount
            collateral_token = real_symbol
            break

    # 转换 liquidator_gains 为简单格式用于输出（使用真实符号）
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


def analyze_liquidations(input_file: str, output_file: Optional[str] = None):
    """分析清算数据文件"""

    print("=" * 70)
    print("Kamino 清算数据分析器")
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
        print("❌ 无法识别的数据格式")
        return

    print(f"\n📂 输入文件: {input_file}")
    print(f"📊 清算交易数: {len(transactions)}")

    # 获取 SOL 价格
    print("\n🔄 获取 SOL 价格...")
    sol_price = fetch_sol_price()
    print(f"   SOL 价格: ${sol_price:.2f}")

    # 分析每个清算
    results = []
    flash_loan_count = 0
    total_gas_fee = 0.0
    total_protocol_fee = 0.0

    print("\n🔍 分析清算交易...")

    for i, tx in enumerate(transactions):
        analysis = analyze_single_liquidation(tx, sol_price)
        results.append(analysis)

        if analysis.uses_flash_loan:
            flash_loan_count += 1
        total_gas_fee += analysis.gas_fee_sol
        total_protocol_fee += analysis.protocol_fee

        if (i + 1) % 10 == 0:
            print(f"   处理: {i + 1}/{len(transactions)}")

    # ========================================
    # 统计摘要
    # ========================================
    print("\n" + "=" * 70)
    print("📊 统计摘要")
    print("=" * 70)

    # 清算人统计
    liquidators = {}
    for r in results:
        if r.liquidator:
            liquidators[r.liquidator] = liquidators.get(r.liquidator, 0) + 1

    print(f"\n🔹 清算人统计:")
    print(f"   唯一清算人数: {len(liquidators)}")
    print(f"   最活跃清算人:")
    for liq, count in sorted(liquidators.items(), key=lambda x: -x[1])[:5]:
        print(f"     {liq[:16]}... : {count} 次")

    # 闪电贷统计
    print(f"\n🔹 闪电贷使用:")
    print(
        f"   使用闪电贷: {flash_loan_count} ({flash_loan_count/len(results)*100:.1f}%)")
    print(
        f"   不使用闪电贷: {len(results) - flash_loan_count} ({(len(results)-flash_loan_count)/len(results)*100:.1f}%)")

    # 成本和利润统计
    total_gross_profit = sum(r.gross_profit_usd for r in results)
    total_net_profit = sum(r.net_profit_usd for r in results)
    total_flash_fee = sum(
        r.flash_loan_fee for r in results if r.uses_flash_loan)
    total_priority_fee = sum(r.priority_fee_sol for r in results)
    total_priority_fee_usd = sum(r.priority_fee_usd for r in results)

    print(f"\n🔹 成本统计:")
    print(
        f"   总 Gas 费用: {total_gas_fee:.6f} SOL (${total_gas_fee * sol_price:.2f})")
    print(f"   平均 Gas 费用: {total_gas_fee/len(results):.6f} SOL")
    print(
        f"   总 Priority Fee: {total_priority_fee:.6f} SOL (${total_priority_fee_usd:.4f})")
    print(f"   平均 Priority Fee: {total_priority_fee/len(results):.6f} SOL")
    print(f"   总闪电贷费用: ${total_flash_fee:.4f}")

    print(f"\n🔹 利润统计:")
    print(f"   总毛利润: ${total_gross_profit:.4f}")
    print(f"   总净利润: ${total_net_profit:.4f}")
    print(f"   平均净利润: ${total_net_profit/len(results):.4f}")

    # 债务代币统计
    debt_tokens = {}
    for r in results:
        if r.debt_token:
            debt_tokens[r.debt_token] = debt_tokens.get(r.debt_token, 0) + 1

    print(f"\n🔹 债务代币分布:")
    for token, count in sorted(debt_tokens.items(), key=lambda x: -x[1]):
        print(f"   {token}: {count} ({count/len(results)*100:.1f}%)")

    # 抵押品代币统计
    collateral_tokens = {}
    for r in results:
        if r.collateral_token:
            collateral_tokens[r.collateral_token] = collateral_tokens.get(
                r.collateral_token, 0) + 1

    print(f"\n🔹 抵押品代币分布:")
    for token, count in sorted(collateral_tokens.items(), key=lambda x: -x[1]):
        print(f"   {token}: {count} ({count/len(results)*100:.1f}%)")

    # ========================================
    # 输出详细结果
    # ========================================
    print("\n" + "=" * 70)
    print("📋 详细清算记录 (前 10 条)")
    print("=" * 70)

    for i, r in enumerate(results[:10]):
        print(f"\n--- 清算 #{i+1} ---")
        print(f"  签名: {r.signature[:32]}...")
        print(f"  时间: {r.datetime}")
        print(
            f"  清算人: {r.liquidator[:24]}..." if r.liquidator else "  清算人: 未知")
        print(
            f"  被清算人: {r.obligation_owner[:24]}..." if r.obligation_owner else "  被清算人: 未知")
        print(f"  闪电贷: {'是' if r.uses_flash_loan else '否'}", end="")
        if r.uses_flash_loan:
            print(
                f" ({r.flash_loan_amount:.2f} {r.flash_loan_token}, 费用: {r.flash_loan_fee:.4f})")
        else:
            print()

        # 从日志解析的详细信息
        log = r.log_data
        if log.get("prices"):
            print(f"  📈 价格信息:")
            for token, price in log["prices"].items():
                print(f"    {token}: ${price:.4f}")

        if log.get("borrow"):
            print(f"  📉 借款信息:")
            for token, info in log["borrow"].items():
                print(f"    {token}: 价值 ${info['value_usd']:.2f}")

        if log.get("deposit"):
            print(f"  🏦 抵押品信息:")
            for token, info in log["deposit"].items():
                print(f"    {token}: 价值 ${info['value_usd']:.2f}")

        if log.get("ltv"):
            ltv = log["ltv"]
            print(
                f"  📊 LTV: {ltv.get('current', '?')}% / {ltv.get('threshold', '?')}%")

        if log.get("liquidation_bonus_bps"):
            print(
                f"  🎁 清算奖励: {log['liquidation_bonus_bps']} bps ({log['liquidation_bonus_bps']/100:.2f}%)")

        if log.get("liquidation_result"):
            res = log["liquidation_result"]
            print(f"  💰 清算结果 (从日志):")
            print(f"    代偿债务: {res.get('repaid_raw', 0)} (原始单位)")
            print(f"    获得抵押品: {res.get('withdrawn_raw', 0)} (原始单位)")
            print(f"    协议费: {res.get('protocol_fees_raw', 0)} (原始单位)")

        if log.get("is_jupiter_swap"):
            print(f"  🔄 使用 Jupiter Swap: 是")

        print(f"  清算人代币变化:")
        for token, amount in r.liquidator_gains.items():
            direction = "+" if amount > 0 else ""
            print(f"    {direction}{amount:.6f} {token}")
        print(f"  成本:")
        source_map = {"oracle": "预言机",
                      "historical": "历史价格", "api_current": "当前API"}
        price_source = source_map.get(r.sol_price_source, r.sol_price_source)
        print(
            f"    Gas: {r.gas_fee_sol:.6f} SOL (${r.gas_fee_usd:.4f}) [SOL=${r.sol_price_used:.2f} from {price_source}]")
        print(
            f"    Priority Fee: {r.priority_fee_lamports} lamports ({r.priority_fee_sol:.6f} SOL, ${r.priority_fee_usd:.4f})")
        if r.uses_flash_loan:
            print(f"    闪电贷费用: {r.flash_loan_fee:.4f} {r.flash_loan_token}")
        print(f"  利润:")
        print(f"    毛利润: ${r.gross_profit_usd:.4f}")
        print(f"    净利润: ${r.net_profit_usd:.4f}")

    # ========================================
    # 保存结果
    # ========================================
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = Path(input_file).parent / \
            f"liquidation_analysis_{timestamp}.json"

    output_data = {
        "metadata": {
            "input_file": str(input_file),
            "total_liquidations": len(results),
            "flash_loan_count": flash_loan_count,
            "flash_loan_percentage": flash_loan_count / len(results) * 100,
            "total_gas_fee_sol": total_gas_fee,
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

    print(f"\n✅ 分析结果已保存: {output_file}")

    return results


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_liquidations.py <liquidations_json>")
        print("示例: python analyze_liquidations.py ./data/liquidations_only_20260130_005256.json")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    analyze_liquidations(input_file, output_file)


if __name__ == "__main__":
    main()
