#!/usr/bin/env python3
"""
详细分析清算交易的利润计算
使用 Kamino IDL 和链上数据
"""

import json
from pathlib import Path

def load_kamino_idl():
    """加载 Kamino Lend IDL"""
    idl_path = Path(__file__).parent / "kamino_lending.json"
    with open(idl_path, 'r') as f:
        return json.load(f)

def analyze_liquidation_profit():
    """
    根据交易 5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg
    进行详细的利润分析
    """

    print("=" * 80)
    print("Kamino 清算交易利润分析")
    print("=" * 80)
    print("\n交易签名: 5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg")

    # 加载 IDL 查看清算指令
    print("\n" + "=" * 80)
    print("Kamino IDL 信息")
    print("=" * 80)

    idl = load_kamino_idl()
    print(f"程序名称: {idl.get('name', 'unknown')}")
    print(f"IDL 版本: {idl.get('version', 'unknown')}")
    print(f"程序 ID: KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD")

    print("\n清算相关指令:")
    for instruction in idl.get('instructions', []):
        name = instruction.get('name', '')
        if 'liquidat' in name.lower():
            print(f"  - {name}")
            # 显示参数
            args = instruction.get('args', [])
            if args:
                print(f"    参数:")
                for arg in args:
                    print(f"      - {arg.get('name')}: {arg.get('type')}")

    # 链上数据（从之前的查询获得）
    print("\n" + "=" * 80)
    print("链上交易数据")
    print("=" * 80)

    # 基本信息
    slot = 306644843
    block_time = 1733853620
    tx_fee = 1322996  # lamports

    print(f"Slot: {slot}")
    print(f"区块时间: {block_time} (2024-12-10)")
    print(f"交易费用: {tx_fee} lamports ({tx_fee / 1e9:.9f} SOL)")

    # 价格信息（从日志提取）
    sol_price = 203.9150
    usdc_price = 0.9998

    print(f"\nOracle 价格:")
    print(f"  SOL: ${sol_price:.4f}")
    print(f"  USDC: ${usdc_price:.4f}")

    # 清算详情（从日志提取）
    print("\n" + "=" * 80)
    print("清算执行详情")
    print("=" * 80)

    # 从日志: "pnl: Liquidator repaid 10642299 and withdrew 53486176 collateral with fees 1304541"
    liquidation_amount = 10642299  # USDC (6 decimals)
    collateral_withdrawn = 53486176  # SOL (9 decimals)
    protocol_fee = 1304541  # SOL (9 decimals)

    print(f"\n清算人偿还 USDC: {liquidation_amount} ({liquidation_amount / 1e6:.6f} USDC)")
    print(f"清算人获得 SOL 抵押品: {collateral_withdrawn} ({collateral_withdrawn / 1e9:.9f} SOL)")
    print(f"协议费用 (SOL): {protocol_fee} ({protocol_fee / 1e9:.9f} SOL)")
    print(f"总 SOL 转移: {(collateral_withdrawn + protocol_fee) / 1e9:.9f} SOL")

    # 清算参数（从日志提取）
    print("\n清算参数:")
    print(f"  清算关闭因子: 20%")
    print(f"  清算奖励: 500 bps (5%)")
    print(f"  LTV: 75%/75% (已触及最大 LTV)")
    print(f"  借款价值: $53.2031")
    print(f"  抵押品价值: $70.5918")

    # 利润计算
    print("\n" + "=" * 80)
    print("利润计算详解")
    print("=" * 80)

    # 方式 1: 标准计算
    print("\n【方式 1: 标准利润计算】")

    usdc_paid = liquidation_amount / 1e6
    usdc_paid_value = usdc_paid * usdc_price

    sol_received = collateral_withdrawn / 1e9
    sol_received_value = sol_received * sol_price

    protocol_fee_sol = protocol_fee / 1e9
    protocol_fee_value = protocol_fee_sol * sol_price

    tx_fee_sol = tx_fee / 1e9
    tx_fee_value = tx_fee_sol * sol_price

    print(f"\n收入:")
    print(f"  获得 SOL: {sol_received:.9f} SOL")
    print(f"  价值: {sol_received:.9f} × ${sol_price:.4f} = ${sol_received_value:.6f}")

    print(f"\n支出:")
    print(f"  支付 USDC: {usdc_paid:.6f} USDC")
    print(f"  价值: {usdc_paid:.6f} × ${usdc_price:.4f} = ${usdc_paid_value:.6f}")
    print(f"  协议费用: {protocol_fee_sol:.9f} SOL")
    print(f"  价值: {protocol_fee_sol:.9f} × ${sol_price:.4f} = ${protocol_fee_value:.6f}")
    print(f"  交易费用: {tx_fee_sol:.9f} SOL")
    print(f"  价值: {tx_fee_sol:.9f} × ${sol_price:.4f} = ${tx_fee_value:.6f}")

    gross_profit = sol_received_value - usdc_paid_value
    net_profit_v1 = gross_profit - protocol_fee_value - tx_fee_value

    print(f"\n计算:")
    print(f"  毛利润 = ${sol_received_value:.6f} - ${usdc_paid_value:.6f} = ${gross_profit:.6f}")
    print(f"  净利润 = ${gross_profit:.6f} - ${protocol_fee_value:.6f} - ${tx_fee_value:.6f}")
    print(f"         = ${net_profit_v1:.6f}")

    # 方式 2: 按照用户提供的正确计算
    print("\n" + "=" * 80)
    print("【方式 2: 正确的利润计算】")
    print("The searcher initiated the liquidation by transferring 10.642 USDC")
    print("In exchange, the Kamino Reserve transferred 0.05479 SOL")
    print("The searcher paid a protocol fee of 0.0013 SOL")
    print("The searcher paid a priority fee of 0.001317 SOL")
    print("Net profit: 0.0492 USD")
    print("=" * 80)

    # 使用更精确的数字
    total_sol_transferred = (collateral_withdrawn + protocol_fee) / 1e9

    print(f"\n收入:")
    print(f"  储备转出 SOL: {total_sol_transferred:.9f} SOL")
    print(f"  价值: {total_sol_transferred:.9f} × ${sol_price:.4f} = ${total_sol_transferred * sol_price:.6f}")

    print(f"\n支出:")
    print(f"  支付 USDC: {usdc_paid:.6f} USDC")
    print(f"  价值: ${usdc_paid_value:.6f}")

    print(f"\n费用 (从获得的 SOL 中扣除):")
    print(f"  协议费用: {protocol_fee_sol:.9f} SOL = ${protocol_fee_value:.6f}")
    print(f"  优先费用 (tx fee): {tx_fee_sol:.9f} SOL = ${tx_fee_value:.6f}")

    print(f"\n计算过程:")
    print(f"  1. 储备转出价值: {total_sol_transferred:.9f} SOL × ${sol_price:.4f} = ${total_sol_transferred * sol_price:.6f}")
    print(f"  2. 减去 USDC 支付: ${total_sol_transferred * sol_price:.6f} - ${usdc_paid_value:.6f} = ${(total_sol_transferred * sol_price) - usdc_paid_value:.6f}")
    print(f"  3. 减去协议费用: ${(total_sol_transferred * sol_price) - usdc_paid_value:.6f} - ${protocol_fee_value:.6f} = ${(total_sol_transferred * sol_price) - usdc_paid_value - protocol_fee_value:.6f}")
    print(f"  4. 减去优先费用: ${(total_sol_transferred * sol_price) - usdc_paid_value - protocol_fee_value:.6f} - ${tx_fee_value:.6f} = ${(total_sol_transferred * sol_price) - usdc_paid_value - protocol_fee_value - tx_fee_value:.6f}")

    net_profit_v2 = (total_sol_transferred * sol_price) - usdc_paid_value - protocol_fee_value - tx_fee_value

    print(f"\n  最终净利润: ${net_profit_v2:.6f}")

    # 验证用户提供的数字
    print("\n" + "=" * 80)
    print("数据验证")
    print("=" * 80)

    user_sol_total = 0.05479
    user_protocol_fee = 0.0013
    user_priority_fee = 0.001317
    user_usdc_paid = 10.642
    user_net_profit = 0.0492

    print(f"\n链上数据 vs 用户数据:")
    print(f"  SOL 总转移: {total_sol_transferred:.9f} vs {user_sol_total:.9f} (差异: {abs(total_sol_transferred - user_sol_total):.9f})")
    print(f"  协议费用: {protocol_fee_sol:.9f} vs {user_protocol_fee:.9f} (差异: {abs(protocol_fee_sol - user_protocol_fee):.9f})")
    print(f"  优先费用: {tx_fee_sol:.9f} vs {user_priority_fee:.9f} (差异: {abs(tx_fee_sol - user_priority_fee):.9f})")
    print(f"  USDC 支付: {usdc_paid:.6f} vs {user_usdc_paid:.6f} (差异: {abs(usdc_paid - user_usdc_paid):.6f})")

    # 用用户的数字重新计算
    user_calculated_profit = (user_sol_total * sol_price) - (user_usdc_paid * usdc_price) - (user_protocol_fee * sol_price) - (user_priority_fee * sol_price)

    print(f"\n使用用户数字重新计算:")
    print(f"  净利润 = (0.05479 × ${sol_price:.4f}) - (10.642 × ${usdc_price:.4f}) - (0.0013 × ${sol_price:.4f}) - (0.001317 × ${sol_price:.4f})")
    print(f"         = ${user_sol_total * sol_price:.6f} - ${user_usdc_paid * usdc_price:.6f} - ${user_protocol_fee * sol_price:.6f} - ${user_priority_fee * sol_price:.6f}")
    print(f"         = ${user_calculated_profit:.6f}")

    print(f"\n用户声明的净利润: ${user_net_profit:.4f}")
    print(f"计算出的净利润: ${user_calculated_profit:.6f}")
    print(f"差异: ${abs(user_calculated_profit - user_net_profit):.6f}")

    # 总结
    print("\n" + "=" * 80)
    print("总结")
    print("=" * 80)

    print(f"\n根据链上数据的净利润: ${net_profit_v2:.6f} USD")
    print(f"用户提供的净利润: ${user_net_profit:.4f} USD")

    if abs(net_profit_v2 - user_net_profit) < 0.01:
        print(f"\n✅ 利润计算一致！")
    else:
        print(f"\n⚠️  利润计算存在差异，可能是:")
        print(f"   - 价格数据略有不同")
        print(f"   - 精度舍入差异")
        print(f"   - 时间点不同导致的价格差异")

    print(f"\n清算机制说明:")
    print(f"  1. 清算人向 Kamino Reserve 转入 {usdc_paid:.6f} USDC 偿还用户债务")
    print(f"  2. Kamino Reserve 转出 {total_sol_transferred:.9f} SOL 给清算人")
    print(f"  3. 其中 {protocol_fee_sol:.9f} SOL 是协议费用")
    print(f"  4. 清算人实际获得 {sol_received:.9f} SOL")
    print(f"  5. 清算人还需支付 {tx_fee_sol:.9f} SOL 的优先费用")
    print(f"  6. 最终净利润约 ${net_profit_v2:.4f} USD")

if __name__ == "__main__":
    analyze_liquidation_profit()
