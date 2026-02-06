"""
分析 UNKNOWN 类型的交易，查找可能的清算交易
"""
import json
import sys
from datetime import datetime


def analyze_unknown_transactions(filename):
    """分析 UNKNOWN 交易"""

    # 读取数据
    with open(filename, 'r', encoding='utf-8') as f:
        transactions = json.load(f)

    print(f"总交易数: {len(transactions)}")

    # 过滤 UNKNOWN 类型
    unknown_txs = [tx for tx in transactions if tx.get('type') == 'UNKNOWN']
    print(f"UNKNOWN 交易数: {len(unknown_txs)}\n")

    print("=" * 100)
    print("详细分析 UNKNOWN 交易:")
    print("=" * 100)

    liquidation_keywords = ['liquidate', 'Liquidate', 'LIQUIDATE', 'liquidation', 'Liquidation']

    for i, tx in enumerate(unknown_txs):
        print(f"\n交易 #{i+1}:")
        print(f"签名: {tx.get('signature')}")
        print(f"时间: {datetime.fromtimestamp(tx.get('timestamp', 0))}")
        print(f"费用支付者: {tx.get('feePayer', 'N/A')}")

        # 检查描述
        description = tx.get('description', '')
        if description:
            print(f"描述: {description}")

        # 检查指令
        instructions = tx.get('instructions', [])
        print(f"\n指令列表 ({len(instructions)} 个):")
        for j, inst in enumerate(instructions):
            program_id = inst.get('programId', 'N/A')
            print(f"  {j+1}. Program: {program_id}")

            # 检查 parsed 数据
            if 'parsed' in inst:
                parsed = inst['parsed']
                if isinstance(parsed, dict):
                    inst_type = parsed.get('type', 'N/A')
                    print(f"     Type: {inst_type}")

                    # 检查是否包含清算关键词
                    if any(keyword in str(parsed) for keyword in liquidation_keywords):
                        print(f"     ⚠️  发现清算关键词!")

        # 检查代币转账
        token_transfers = tx.get('tokenTransfers', [])
        if token_transfers:
            print(f"\n代币转账 ({len(token_transfers)} 笔):")
            for j, transfer in enumerate(token_transfers):
                print(f"  {j+1}. 数量: {transfer.get('tokenAmount', 0)}")
                print(f"     Mint: {transfer.get('mint', 'N/A')}")
                print(f"     从: {transfer.get('fromUserAccount', 'N/A')}")
                print(f"     到: {transfer.get('toUserAccount', 'N/A')}")

        # 检查日志
        logs = tx.get('logs', [])
        liquidation_logs = [log for log in logs if any(keyword in log for keyword in liquidation_keywords)]

        if liquidation_logs:
            print(f"\n⚠️⚠️⚠️ 发现清算相关日志:")
            for log in liquidation_logs:
                print(f"  - {log}")
        else:
            # 显示所有日志的前几条，看看有什么
            if logs:
                print(f"\n日志示例 (前 5 条):")
                for log in logs[:5]:
                    print(f"  - {log}")

        print("-" * 100)

        # 每 10 个交易暂停一下
        if (i + 1) % 10 == 0:
            response = input(f"\n已分析 {i+1} 个交易，继续？(y/n): ")
            if response.lower() != 'y':
                break


if __name__ == "__main__":
    # 使用最新的 JSON 文件
    import glob
    json_files = glob.glob("kamino_raw_transactions_*.json")
    if json_files:
        latest_file = max(json_files)
        print(f"分析文件: {latest_file}\n")
        analyze_unknown_transactions(latest_file)
    else:
        print("未找到交易数据文件")
