#!/usr/bin/env python3
"""
查询 Solana 交易详情
"""

import requests
import json
import sys

def get_transaction(signature, rpc_url="https://api.mainnet-beta.solana.com"):
    """
    从 Solana RPC 获取交易详情
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0
            }
        ]
    }

    try:
        response = requests.post(rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()

        if 'error' in result:
            print(f"错误: {result['error']}")
            return None

        return result.get('result')
    except Exception as e:
        print(f"查询失败: {e}")
        return None

def analyze_transaction(tx_data):
    """
    分析交易是否为清算交易
    """
    if not tx_data:
        print("未找到交易数据")
        return

    print("=" * 80)
    print("交易详细信息")
    print("=" * 80)

    # 基本信息
    meta = tx_data.get('meta', {})
    slot = tx_data.get('slot', 'N/A')
    block_time = tx_data.get('blockTime', 'N/A')

    print(f"Slot: {slot}")
    print(f"区块时间: {block_time}")
    print(f"交易费用: {meta.get('fee', 0)} lamports")
    print(f"执行状态: {'成功' if meta.get('err') is None else '失败'}")

    if meta.get('err'):
        print(f"错误信息: {meta['err']}")

    # 指令分析
    print("\n" + "=" * 80)
    print("指令分析")
    print("=" * 80)

    transaction = tx_data.get('transaction', {})
    message = transaction.get('message', {})
    instructions = message.get('instructions', [])

    print(f"总指令数: {len(instructions)}")

    liquidation_indicators = []

    for i, instruction in enumerate(instructions, 1):
        print(f"\n指令 #{i}:")

        program = instruction.get('program', 'Unknown')
        program_id = instruction.get('programId', 'Unknown')
        parsed = instruction.get('parsed', {})

        print(f"  程序: {program}")
        print(f"  程序ID: {program_id}")

        if parsed:
            instruction_type = parsed.get('type', 'Unknown')
            print(f"  类型: {instruction_type}")

            # 检查是否包含清算相关的关键词
            instruction_str = json.dumps(instruction).lower()

            if any(keyword in instruction_str for keyword in [
                'liquidat', 'obligation', 'collateral',
                'repay', 'borrow', 'withdraw_obligation'
            ]):
                liquidation_indicators.append(f"指令 #{i} 包含清算相关关键词")
        else:
            # 如果没有 parsed，显示原始数据
            if 'data' in instruction:
                print(f"  数据: {instruction['data'][:50]}...")

    # 代币余额变化
    print("\n" + "=" * 80)
    print("代币余额变化")
    print("=" * 80)

    pre_token_balances = meta.get('preTokenBalances', [])
    post_token_balances = meta.get('postTokenBalances', [])

    if pre_token_balances or post_token_balances:
        # 创建余额变化映射
        balance_changes = {}

        for pre in pre_token_balances:
            account_index = pre.get('accountIndex')
            mint = pre.get('mint')
            ui_amount = pre.get('uiTokenAmount', {}).get('uiAmount')
            pre_amount = float(ui_amount) if ui_amount is not None else 0.0

            key = (account_index, mint)
            balance_changes[key] = {'pre': pre_amount, 'post': 0, 'mint': mint}

        for post in post_token_balances:
            account_index = post.get('accountIndex')
            mint = post.get('mint')
            ui_amount = post.get('uiTokenAmount', {}).get('uiAmount')
            post_amount = float(ui_amount) if ui_amount is not None else 0.0

            key = (account_index, mint)
            if key in balance_changes:
                balance_changes[key]['post'] = post_amount
            else:
                balance_changes[key] = {'pre': 0, 'post': post_amount, 'mint': mint}

        for (account_index, mint), changes in balance_changes.items():
            change = changes['post'] - changes['pre']
            if change != 0:
                print(f"\n账户索引 {account_index} ({mint[:20]}...):")
                print(f"  变化: {change:+.6f}")

                # 大额变化可能是清算
                if abs(change) > 1000:
                    liquidation_indicators.append(f"账户 {account_index} 有大额代币变化: {change:+.2f}")

    # 日志分析
    print("\n" + "=" * 80)
    print("程序日志")
    print("=" * 80)

    log_messages = meta.get('logMessages', [])

    print(f"总日志数: {len(log_messages)}\n")

    for log in log_messages:  # 显示所有日志
        print(f"  {log}")

        # 检查日志中的清算关键词
        log_lower = log.lower()
        if any(keyword in log_lower for keyword in [
            'liquidat', 'liquidation', 'seize',
            'obligation', 'collateral'
        ]):
            liquidation_indicators.append(f"日志包含清算关键词: {log[:80]}")

    # 清算判断
    print("\n" + "=" * 80)
    print("🔍 清算分析结果")
    print("=" * 80)

    if liquidation_indicators:
        print("\n⚠️  发现清算相关特征:")
        for indicator in liquidation_indicators:
            print(f"  - {indicator}")
        print("\n结论: 这个交易很可能是清算交易")
    else:
        print("\n未发现明显的清算特征")
        print("结论: 这可能不是清算交易，或者是使用了特殊的清算机制")

if __name__ == "__main__":
    signature = "5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg"

    if len(sys.argv) > 1:
        signature = sys.argv[1]

    print(f"查询交易: {signature}\n")

    tx_data = get_transaction(signature)
    analyze_transaction(tx_data)
