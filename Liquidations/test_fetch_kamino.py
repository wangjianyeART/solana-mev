"""
测试脚本：获取 Kamino 过去 1 小时的原始交易数据
"""
import asyncio
import json
from datetime import datetime
from helius_client import HeliusClient
import config


async def fetch_kamino_raw():
    """获取 Kamino 原始交易数据"""

    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    print(f"Kamino 程序地址: {kamino_address}")
    print(f"Helius API Key: {config.HELIUS_API_KEY[:20]}...")
    print(f"查询范围: 过去 1 小时")
    print("=" * 80)

    async with HeliusClient(config.HELIUS_API_KEY) as client:
        # 获取过去 1 小时的交易
        print("\n开始查询交易...")
        transactions = await client.get_transactions_in_time_range(
            address=kamino_address,
            hours=1
        )

        print(f"\n总共获取到 {len(transactions)} 笔交易\n")

        # 保存原始数据
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"kamino_raw_transactions_{timestamp}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(transactions, f, indent=2, ensure_ascii=False)

        print(f"✓ 原始数据已保存到: {filename}\n")

        # 显示前 3 笔交易的摘要
        print("=" * 80)
        print("前 3 笔交易摘要:")
        print("=" * 80)

        for i, tx in enumerate(transactions[:3]):
            print(f"\n交易 #{i+1}:")
            print(f"  签名: {tx.get('signature', 'N/A')}")
            print(f"  时间: {datetime.fromtimestamp(tx.get('timestamp', 0))}")
            print(f"  类型: {tx.get('type', 'N/A')}")
            print(f"  描述: {tx.get('description', 'N/A')}")
            print(f"  费用支付者: {tx.get('feePayer', 'N/A')}")

            # 显示指令信息
            instructions = tx.get('instructions', [])
            print(f"  指令数量: {len(instructions)}")

            # 显示前 2 个指令
            for j, inst in enumerate(instructions[:2]):
                parsed = inst.get('parsed', {})
                if isinstance(parsed, dict):
                    inst_type = parsed.get('type', inst.get('programId', 'Unknown'))
                    print(f"    指令 {j+1}: {inst_type}")

            # 显示代币转账
            token_transfers = tx.get('tokenTransfers', [])
            if token_transfers:
                print(f"  代币转账: {len(token_transfers)} 笔")
                for j, transfer in enumerate(token_transfers[:2]):
                    print(f"    {j+1}. {transfer.get('tokenAmount', 0)} (mint: {transfer.get('mint', 'N/A')[:8]}...)")

            # 显示日志（查找清算相关关键词）
            logs = tx.get('logs', [])
            liquidation_logs = [log for log in logs if any(
                keyword in log for keyword in config.LIQUIDATION_KEYWORDS
            )]

            if liquidation_logs:
                print(f"  ⚠️  发现清算相关日志:")
                for log in liquidation_logs[:3]:
                    print(f"    - {log}")

        # 统计分析
        print("\n" + "=" * 80)
        print("统计分析:")
        print("=" * 80)

        # 按类型分组
        types = {}
        for tx in transactions:
            tx_type = tx.get('type', 'UNKNOWN')
            types[tx_type] = types.get(tx_type, 0) + 1

        print("\n交易类型分布:")
        for tx_type, count in sorted(types.items(), key=lambda x: x[1], reverse=True):
            print(f"  {tx_type}: {count}")

        # 检查是否有清算相关的交易
        print("\n清算相关检查:")
        liquidation_count = 0

        for tx in transactions:
            # 检查类型
            if tx.get('type') == 'LIQUIDATE':
                liquidation_count += 1
                continue

            # 检查描述
            description = tx.get('description', '')
            if any(keyword in description for keyword in config.LIQUIDATION_KEYWORDS):
                liquidation_count += 1
                continue

            # 检查日志
            logs = tx.get('logs', [])
            if any(any(keyword in log for keyword in config.LIQUIDATION_KEYWORDS) for log in logs):
                liquidation_count += 1

        print(f"  疑似清算交易: {liquidation_count} 笔")

        if liquidation_count > 0:
            print("\n  发现清算交易！让我显示详情:")
            for tx in transactions:
                if (tx.get('type') == 'LIQUIDATE' or
                    any(keyword in tx.get('description', '') for keyword in config.LIQUIDATION_KEYWORDS) or
                    any(any(keyword in log for keyword in config.LIQUIDATION_KEYWORDS) for log in tx.get('logs', []))):

                    print(f"\n  签名: {tx.get('signature')}")
                    print(f"  时间: {datetime.fromtimestamp(tx.get('timestamp', 0))}")
                    print(f"  类型: {tx.get('type')}")
                    print(f"  描述: {tx.get('description')}")

        return transactions


if __name__ == "__main__":
    asyncio.run(fetch_kamino_raw())
