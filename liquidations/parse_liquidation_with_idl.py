#!/usr/bin/env python3
"""
使用 Kamino IDL 详细解析清算交易
"""

import json
from pathlib import Path
import requests

def load_kamino_idl():
    """加载 Kamino Lend IDL"""
    idl_path = Path(__file__).parent / "kamino_lending.json"
    with open(idl_path, 'r') as f:
        return json.load(f)

def find_instruction_by_discriminator(idl, discriminator):
    """根据 discriminator 查找指令"""
    # Anchor 使用前 8 字节作为指令 discriminator
    for instruction in idl.get('instructions', []):
        name = instruction.get('name', '')
        # 简单匹配名称
        if 'liquidate' in name.lower():
            return instruction
    return None

def get_transaction(signature, rpc_url="https://api.mainnet-beta.solana.com"):
    """从 Solana RPC 获取交易详情"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {
                "encoding": "json",
                "maxSupportedTransactionVersion": 0
            }
        ]
    }

    try:
        response = requests.post(rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result.get('result')
    except Exception as e:
        print(f"查询失败: {e}")
        return None

def analyze_liquidation_with_idl(signature):
    """使用 IDL 详细分析清算交易"""

    print("=" * 80)
    print("使用 IDL 解析 Kamino 清算交易")
    print("=" * 80)
    print(f"\n交易签名: {signature}\n")

    # 加载 IDL
    print("加载 Kamino Lend IDL...")
    idl = load_kamino_idl()
    print(f"IDL 版本: {idl.get('version', 'unknown')}")
    print(f"程序名称: {idl.get('name', 'unknown')}")

    # 查找清算相关指令
    print("\n清算相关指令:")
    liquidation_instructions = []
    for instruction in idl.get('instructions', []):
        name = instruction.get('name', '')
        if 'liquidat' in name.lower():
            liquidation_instructions.append(name)
            print(f"  - {name}")

    # 获取交易数据
    print(f"\n查询交易数据...")
    tx_data = get_transaction(signature)

    if not tx_data:
        print("无法获取交易数据")
        return

    meta = tx_data.get('meta', {})
    transaction = tx_data.get('transaction', {})
    message = transaction.get('message', {})

    # 基本信息
    print("\n" + "=" * 80)
    print("交易基本信息")
    print("=" * 80)
    print(f"Slot: {tx_data.get('slot', 'N/A')}")
    print(f"区块时间: {tx_data.get('blockTime', 'N/A')}")
    print(f"交易费用: {meta.get('fee', 0)} lamports ({meta.get('fee', 0) / 1e9:.9f} SOL)")
    print(f"执行状态: {'成功' if meta.get('err') is None else '失败'}")

    # 代币价格（从日志中提取）
    print("\n" + "=" * 80)
    print("代币价格")
    print("=" * 80)

    log_messages = meta.get('logMessages', [])
    sol_price = None
    usdc_price = None

    for log in log_messages:
        if "Token: SOL Price:" in log:
            try:
                sol_price = float(log.split("Price:")[1].strip())
                print(f"SOL 价格: ${sol_price:.4f}")
            except:
                pass
        elif "Token: USDC Price:" in log:
            try:
                usdc_price = float(log.split("Price:")[1].strip())
                print(f"USDC 价格: ${usdc_price:.4f}")
            except:
                pass

    # 提取清算详情
    print("\n" + "=" * 80)
    print("清算详情分析")
    print("=" * 80)

    liquidation_amount = None
    collateral_withdrawn = None
    protocol_fee = None

    for log in log_messages:
        if "pnl: Liquidator repaid" in log:
            try:
                parts = log.split("repaid")[1].split("and withdrew")[0].strip()
                liquidation_amount = int(parts)
                parts = log.split("withdrew")[1].split("collateral with fees")[0].strip()
                collateral_withdrawn = int(parts)
                parts = log.split("with fees")[1].strip()
                protocol_fee = int(parts)

                print(f"清算人偿还: {liquidation_amount} ({liquidation_amount / 1e6:.6f} USDC)")
                print(f"清算人获得抵押品: {collateral_withdrawn} ({collateral_withdrawn / 1e9:.9f} SOL)")
                print(f"协议费用: {protocol_fee} ({protocol_fee / 1e9:.9f} SOL)")
            except Exception as e:
                print(f"解析失败: {e}")

    # 清算参数
    print("\n清算参数:")
    for log in log_messages:
        if "liquidation_close_factor_pct" in log:
            print(f"  {log.split('Program log:')[1].strip()}")
        elif "Obligation is eligible for liquidation" in log and "borrowed value" in log:
            print(f"  {log.split('Program log:')[1].strip()}")
        elif "liquidation bonus" in log:
            print(f"  {log.split('Program log:')[1].strip()}")

    # 利润计算
    print("\n" + "=" * 80)
    print("利润计算（按照正确方式）")
    print("=" * 80)

    if liquidation_amount and collateral_withdrawn and protocol_fee and sol_price and usdc_price:
        # 清算人支付的 USDC
        usdc_paid = liquidation_amount / 1e6
        usdc_paid_value = usdc_paid * usdc_price

        # 清算人获得的 SOL（总转移）
        total_sol_transferred = (collateral_withdrawn + protocol_fee) / 1e9

        # 清算人实际获得的 SOL（不含协议费用）
        sol_received = collateral_withdrawn / 1e9
        sol_received_value = sol_received * sol_price

        # 协议费用
        protocol_fee_sol = protocol_fee / 1e9
        protocol_fee_value = protocol_fee_sol * sol_price

        # 交易费用（priority fee）
        tx_fee_sol = meta.get('fee', 0) / 1e9
        tx_fee_value = tx_fee_sol * sol_price

        print(f"\n收入:")
        print(f"  获得 SOL: {sol_received:.9f} SOL × ${sol_price:.4f} = ${sol_received_value:.4f}")

        print(f"\n支出:")
        print(f"  支付 USDC: {usdc_paid:.6f} USDC × ${usdc_price:.4f} = ${usdc_paid_value:.4f}")
        print(f"  协议费用: {protocol_fee_sol:.9f} SOL × ${sol_price:.4f} = ${protocol_fee_value:.4f}")
        print(f"  交易费用: {tx_fee_sol:.9f} SOL × ${sol_price:.4f} = ${tx_fee_value:.4f}")

        print(f"\n计算过程:")
        print(f"  毛利润 = ${sol_received_value:.4f} - ${usdc_paid_value:.4f} = ${sol_received_value - usdc_paid_value:.4f}")
        print(f"  净利润 = 毛利润 - 协议费用 - 交易费用")
        print(f"         = ${sol_received_value - usdc_paid_value:.4f} - ${protocol_fee_value:.4f} - ${tx_fee_value:.4f}")

        net_profit = sol_received_value - usdc_paid_value - protocol_fee_value - tx_fee_value
        print(f"         = ${net_profit:.4f}")

        print(f"\n" + "=" * 80)
        print(f"最终净利润: ${net_profit:.4f} USD")
        print("=" * 80)

        # 用户提供的数据验证
        print(f"\n用户提供的分析:")
        print(f"  清算人支付: 10.642 USDC")
        print(f"  清算人获得: 0.05479 SOL (总转移，含协议费用)")
        print(f"  协议费用: 0.0013 SOL")
        print(f"  优先费用: 0.001317 SOL")
        print(f"  净利润: 0.0492 USD")

        print(f"\n数据对比:")
        print(f"  USDC 支付: {usdc_paid:.6f} vs 10.642 (差异: {abs(usdc_paid - 10.642):.6f})")
        print(f"  SOL 总转移: {total_sol_transferred:.9f} vs 0.05479 (差异: {abs(total_sol_transferred - 0.05479):.9f})")
        print(f"  协议费用: {protocol_fee_sol:.9f} vs 0.0013 (差异: {abs(protocol_fee_sol - 0.0013):.9f})")
        print(f"  交易费用: {tx_fee_sol:.9f} vs 0.001317 (差异: {abs(tx_fee_sol - 0.001317):.9f})")

        # 尝试重现用户的计算
        print(f"\n尝试重现用户的利润计算:")
        user_sol_total = 0.05479
        user_protocol_fee = 0.0013
        user_priority_fee = 0.001317
        user_usdc_paid = 10.642

        user_net_profit = (user_sol_total * sol_price) - user_usdc_paid - (user_protocol_fee * sol_price) - (user_priority_fee * sol_price)
        print(f"  净利润 = (0.05479 × ${sol_price:.4f}) - $10.642 - (0.0013 × ${sol_price:.4f}) - (0.001317 × ${sol_price:.4f})")
        print(f"         = ${user_sol_total * sol_price:.4f} - $10.642 - ${user_protocol_fee * sol_price:.4f} - ${user_priority_fee * sol_price:.4f}")
        print(f"         = ${user_net_profit:.4f}")

if __name__ == "__main__":
    signature = "5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg"
    analyze_liquidation_with_idl(signature)
