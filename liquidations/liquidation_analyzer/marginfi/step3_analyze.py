#!/usr/bin/env python3
"""
步骤 3: MarginFi 清算数据分析器

功能：
- 提取被清算人、清算人
- 检测是否使用闪电贷
- 计算成本（协议费用、Gas Fee）
- 计算利润
- 生成统计报告

使用方法：
    python step3_analyze.py <liquidations_json>
    python step3_analyze.py ./data/marginfi_liquidations_parsed_20260130.json
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
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {"symbol": "cbBTC", "decimals": 8},
    "3NZ9JMVBmGAqocybic2c7LQCJScmgsAZ6vQqTDzcqmJh": {"symbol": "WBTC", "decimals": 8},
    "HzwqbKZw9HxBN6Mrrx3VEhbwhQCYKoytumVuoGWpS1hn": {"symbol": "hSOL", "decimals": 9},
    # Euro Coin (稳定币)
    "HzwqbKZw8HxMN6bF2yFZNrht3c2iXXzpKcFu7uBEDKtr": {"symbol": "EURC", "decimals": 6},
    "he1iusmfkpAdwvxLNGV8Y1iSbj4rUy6yMhEA3fotn9A": {"symbol": "hSOL", "decimals": 9},
    "5oVNBeEEQvYi1cX3ir8Dx5n1P7pdxydbGF2X4TxVusJm": {"symbol": "INF", "decimals": 9},
    "LSTxxxnJzKDFSLr4dUkPcmCf5VyryEqzPLz5j4bpxFp": {"symbol": "LST", "decimals": 9},
    "vSoLxydx6akxyMD9XEcPvGYNGq6Nn66oqVb3UkGkei7": {"symbol": "vSOL", "decimals": 9},
    "7Q2afV64in6N6SeZsAAB81TJzwDoD6zpqmHkzi9Dcavn": {"symbol": "JSOL", "decimals": 9},
    "BonK1YhkXEGLZzwtcvRTip3gAL9nCeQD7ppZBLXhtTs": {"symbol": "BONK", "decimals": 5},
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": {"symbol": "BONK", "decimals": 5},
    "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof": {"symbol": "RENDER", "decimals": 8},
    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm": {"symbol": "WIF", "decimals": 6},
}

# 稳定币列表
STABLECOINS = {"USDC", "USDT", "EURC"}

# EURC 对 USD 的汇率（欧元对美元）
EURC_USD_RATE = 1.04  # 1 EUR ≈ 1.04 USD

# SOL 类似代币（LST，价格约等于 SOL）
SOL_LIKE_TOKENS = {"SOL", "mSOL", "stSOL", "JitoSOL",
                   "bSOL", "jupSOL", "hSOL", "INF", "LST", "vSOL", "JSOL"}


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
    liquidatee: str

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


def decode_base64_u64(data: str) -> int:
    """解码 Base64 编码的 u64 (小端序)"""
    import base64
    try:
        decoded = base64.b64decode(data)
        if len(decoded) >= 8:
            return int.from_bytes(decoded[:8], 'little')
        elif len(decoded) > 0:
            return int.from_bytes(decoded, 'little')
    except:
        pass
    return 0


def parse_marginfi_logs(raw_logs: List[str]) -> Dict[str, Any]:
    """从原始日志中解析清算详细信息"""
    result = {
        "prices": {},
        "liquidation_info": {},
        "is_jupiter_swap": False,
        "jupiter_swap_output": 0,  # Jupiter Swap 输出金额
        "repay_amount": 0,  # 还款金额
        "raw_important_logs": []
    }

    for log in raw_logs:
        # 价格信息 (MarginFi 格式可能不同)
        price_match = re.search(r'price[:\s]+(\d+\.?\d*)', log, re.IGNORECASE)
        if price_match:
            result["raw_important_logs"].append(log)

        # 清算信息
        if 'liquidat' in log.lower():
            result["raw_important_logs"].append(log)

        # Jupiter Swap 返回值
        # 格式: "Program return: JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4 hPRAAAAAAAA="
        if "Program return: JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4" in log:
            result["is_jupiter_swap"] = True
            parts = log.split()
            if len(parts) >= 3:
                base64_data = parts[-1]
                result["jupiter_swap_output"] = decode_base64_u64(base64_data)
                result["raw_important_logs"].append(log)

        # Jupiter 检测
        if "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4" in log:
            result["is_jupiter_swap"] = True

        # MarginFi 还款数据
        # 格式包含 "+zE/AAAAAAAB" 这样的 Base64 数据
        if "Program data:" in log and "MFv2hWf31Z9kbCa1snEPYctwafyhdvnV7FZnsebVacA" in str(raw_logs):
            # 尝试从日志中提取还款金额
            # 这个需要更精确的解析，暂时跳过
            pass

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

    marginfi_instructions = tx.get("marginfi_instructions", [])
    token_changes = tx.get("token_balance_changes", [])
    liquidation_details = tx.get("liquidation_details", {}) or {}

    # 解析原始日志
    raw_logs = tx.get("raw_logs", [])
    log_data = parse_marginfi_logs(raw_logs)

    # 1. 检测闪电贷
    uses_flash_loan = False
    flash_loan_amount = 0.0
    flash_loan_token = ""
    flash_loan_fee = 0.0

    for instr in marginfi_instructions:
        instr_name = instr.get("name", "")
        if "flashloan" in instr_name.lower() or "flash_loan" in instr_name.lower():
            uses_flash_loan = True
            # 尝试从指令参数中获取闪电贷金额
            args = instr.get("args", {})
            if "end_index" in args:
                # 这是 start_flashloan 指令
                pass
            break

    # 2. 提取清算人和被清算人
    # 优先使用 liquidator_wallet（真正持有 token 的钱包地址）
    liquidator_wallet = liquidation_details.get('liquidator_wallet', '')
    liquidator = liquidation_details.get('liquidator', '')  # MarginFi 账户
    liquidatee = liquidation_details.get('liquidatee', '')

    # 如果没有 liquidator_wallet，尝试从 token 变化推断
    if not liquidator_wallet:
        # 找同时有正负变化的 owner（支付债务，获得抵押品）
        owner_changes = {}
        for change in token_changes:
            owner = change.get('owner', '')
            if owner:
                if owner not in owner_changes:
                    owner_changes[owner] = {'positive': [], 'negative': []}
                if change['change'] > 0:
                    owner_changes[owner]['positive'].append(change)
                elif change['change'] < 0:
                    owner_changes[owner]['negative'].append(change)

        for owner, changes in owner_changes.items():
            if changes['positive'] and changes['negative']:
                # 检查是否支付稳定币并获得非稳定币
                has_stable_payment = any(
                    c['symbol'] in STABLECOINS for c in changes['negative']
                )
                has_collateral_receive = any(
                    c['symbol'] not in STABLECOINS for c in changes['positive']
                )
                if has_stable_payment and has_collateral_receive:
                    liquidator_wallet = owner
                    break
                elif not liquidator_wallet:
                    liquidator_wallet = owner

    # 3. 提取债务和抵押品信息
    debt_token = liquidation_details.get('debt_token', '')
    debt_amount = liquidation_details.get('debt_amount', 0.0)
    collateral_token = liquidation_details.get('collateral_token', '')
    collateral_received = liquidation_details.get('collateral_amount', 0.0)

    # 从 token_changes 补充信息（使用 liquidator_wallet）
    if liquidator_wallet:
        if not debt_token or debt_amount == 0:
            for change in token_changes:
                owner = change.get('owner', '')
                symbol = change.get('symbol', '')
                amount_change = change.get('change', 0)

                if owner == liquidator_wallet and amount_change < 0:
                    if symbol in STABLECOINS:
                        debt_token = symbol
                        debt_amount = abs(amount_change)
                        break
                    elif not debt_token:
                        debt_token = symbol
                        debt_amount = abs(amount_change)

        if not collateral_token or collateral_received == 0:
            for change in token_changes:
                owner = change.get('owner', '')
                symbol = change.get('symbol', '')
                amount_change = change.get('change', 0)

                if owner == liquidator_wallet and amount_change > 0:
                    if symbol not in STABLECOINS:
                        collateral_token = symbol
                        collateral_received = amount_change
                        break

    # 4. 计算成本
    gas_fee_lamports = tx.get("fee", 0)
    gas_fee_sol = gas_fee_lamports / 1e9

    block_time = tx.get("blockTime", 0)

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

    protocol_fee = flash_loan_fee if uses_flash_loan else 0.0
    protocol_fee_token = flash_loan_token if uses_flash_loan else ""

    # 5. 计算利润（使用 liquidator_wallet）
    liquidator_gains = {}

    for change in token_changes:
        owner = change.get("owner", "")
        symbol = change.get("symbol", "")
        mint = change.get("mint", "")
        amount_change = change.get("change", 0)

        # 使用 liquidator_wallet 来追踪清算人的 token 变化
        if owner == liquidator_wallet:
            # 尝试获取真实 symbol
            real_symbol = symbol
            if symbol.endswith("...") and mint in KNOWN_TOKENS:
                real_symbol = KNOWN_TOKENS[mint]["symbol"]

            if real_symbol not in liquidator_gains:
                liquidator_gains[real_symbol] = 0.0
            liquidator_gains[real_symbol] += amount_change

    # 计算 USD 利润
    gross_profit_usd = 0.0
    unknown_tokens = {}  # 记录未知代币

    for symbol, amount in liquidator_gains.items():
        if amount == 0:
            continue

        if symbol == "EURC":
            # EURC 是欧元稳定币，需要乘以汇率
            gross_profit_usd += amount * EURC_USD_RATE
        elif symbol in STABLECOINS:
            # USDC/USDT 直接等于 USD
            gross_profit_usd += amount
        elif symbol in SOL_LIKE_TOKENS:
            # SOL 和 LST 代币使用 SOL 价格
            gross_profit_usd += amount * sol_price_used
        elif symbol in ["cbBTC", "WBTC"]:
            # BTC 代币需要单独获取价格，这里使用估计值
            gross_profit_usd += amount * 100000
        elif symbol in ["BONK"]:
            # BONK 价格很低
            gross_profit_usd += amount * 0.00001
        elif symbol in ["WIF"]:
            gross_profit_usd += amount * 1.5
        elif symbol in ["RENDER"]:
            gross_profit_usd += amount * 5.0
        elif symbol.endswith("..."):
            # 未知代币（显示为截断形式），记录但不计算价值
            unknown_tokens[symbol] = amount
        else:
            # 其他已知名称但未定价的代币
            unknown_tokens[symbol] = amount

    # 对于闪电贷清算，如果清算人获得了未知 token 并支付了稳定币
    # 可以通过 债务金额 vs 获得的代币 来估算利润
    # 典型模式：支付 X USDC 债务，获得 Y 抵押品，利润 = 抵押品价值 - 债务
    # 如果抵押品价值未知，我们用一个保守估计

    if unknown_tokens and debt_amount > 0 and collateral_received > 0:
        # 清算通常有 5-10% 的清算奖励
        # 假设抵押品价值 = 债务 * 1.05 (5% 清算奖励)
        estimated_collateral_value = debt_amount * 1.05
        estimated_profit = estimated_collateral_value - debt_amount
        gross_profit_usd = estimated_profit
        log_data['estimated_profit'] = True
        log_data['estimation_method'] = 'liquidation_bonus_5%'

    net_profit_usd = gross_profit_usd - priority_fee_usd

    # 使用 liquidator_wallet 作为显示的清算人（如果有的话）
    display_liquidator = liquidator_wallet if liquidator_wallet else liquidator

    return LiquidationAnalysis(
        signature=signature,
        datetime=datetime_str,
        slot=slot,
        success=success,
        liquidator=display_liquidator,
        liquidatee=liquidatee,
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
        liquidator_gains=liquidator_gains,
        log_data=log_data,
        raw_token_changes=token_changes
    )


# ============================================================================
# 主函数
# ============================================================================

def analyze_liquidations(input_file: str, output_file: Optional[str] = None) -> List[LiquidationAnalysis]:
    """分析清算数据文件"""

    print("=" * 70)
    print("MarginFi 清算分析 - 步骤 3: 分析数据")
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
        print(
            f"  使用闪电贷: {flash_loan_count} ({flash_loan_count/len(results)*100:.1f}%)")
        print(f"  不使用闪电贷: {len(results) - flash_loan_count}")

    # 成本和利润统计
    total_gross_profit = sum(r.gross_profit_usd for r in results)
    total_net_profit = sum(r.net_profit_usd for r in results)
    total_flash_fee = sum(
        r.flash_loan_fee for r in results if r.uses_flash_loan)
    total_priority_fee = sum(r.priority_fee_sol for r in results)
    total_priority_fee_usd = sum(r.priority_fee_usd for r in results)

    print(f"\n成本统计:")
    print(
        f"  总 Gas 费用: {total_gas_fee:.6f} SOL (${total_gas_fee * sol_price:.2f})")
    if results:
        print(f"  平均 Gas 费用: {total_gas_fee/len(results):.6f} SOL")
    print(
        f"  总 Priority Fee: {total_priority_fee:.6f} SOL (${total_priority_fee_usd:.4f})")
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
        pct = count/len(results)*100 if results else 0
        print(f"  {token}: {count} ({pct:.1f}%)")

    # 抵押品代币统计
    collateral_tokens = {}
    for r in results:
        if r.collateral_token:
            collateral_tokens[r.collateral_token] = collateral_tokens.get(
                r.collateral_token, 0) + 1

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
        print(
            f"  清算人: {r.liquidator[:24]}..." if r.liquidator else "  清算人: 未知")
        print(
            f"  被清算人: {r.liquidatee[:24]}..." if r.liquidatee else "  被清算人: 未知")
        print(f"  闪电贷: {'是' if r.uses_flash_loan else '否'}")

        print(f"  债务: {r.debt_amount:.4f} {r.debt_token}")
        print(f"  抵押品: {r.collateral_received:.6f} {r.collateral_token}")

        print(f"  清算人代币变化:")
        for token, amount in r.liquidator_gains.items():
            direction = "+" if amount > 0 else ""
            print(f"    {direction}{amount:.6f} {token}")

        print(f"  成本:")
        source_map = {"historical": "历史价格", "api_current": "当前API"}
        price_source = source_map.get(r.sol_price_source, r.sol_price_source)
        print(
            f"    Gas: {r.gas_fee_sol:.6f} SOL (${r.gas_fee_usd:.4f}) [SOL=${r.sol_price_used:.2f} from {price_source}]")
        print(
            f"    Priority Fee: {r.priority_fee_lamports} lamports ({r.priority_fee_sol:.6f} SOL)")

        print(f"  利润:")
        print(f"    毛利润: ${r.gross_profit_usd:.4f}")
        print(f"    净利润: ${r.net_profit_usd:.4f}")

    # 保存结果
    if output_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = Path(input_file).parent / \
            f"marginfi_liquidation_analysis_{timestamp}.json"

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
        print(
            "示例: python step3_analyze.py ./data/marginfi_liquidations_parsed_20260130.json")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    analyze_liquidations(input_file, output_file)


if __name__ == "__main__":
    main()
