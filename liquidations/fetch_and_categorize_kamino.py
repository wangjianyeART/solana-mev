"""
获取 Kamino 过去 1 小时的所有交易并分类
结果保存到本地 JSON 文件
"""
import asyncio
import json
from datetime import datetime, timedelta
from collections import defaultdict
from helius_client import HeliusClient
import config


async def fetch_and_categorize_kamino_transactions(hours: int = 1):
    """
    获取并分类 Kamino 交易

    Args:
        hours: 时间范围（小时）

    Returns:
        包含所有交易和统计信息的字典
    """

    kamino_address = config.LENDING_PROTOCOLS["Kamino"]

    print("=" * 100)
    print(f"Kamino 交易获取与分类程序")
    print("=" * 100)
    print(f"协议: Kamino Lending")
    print(f"程序地址: {kamino_address}")
    print(f"时间范围: 过去 {hours} 小时")
    print(f"数据源: Helius API")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    # 使用 Helius 客户端获取交易
    async with HeliusClient(config.HELIUS_API_KEY) as client:
        print("\n步骤 1: 获取交易数据...")
        transactions = await client.get_transactions_in_time_range(
            address=kamino_address,
            hours=hours
        )

        print(f"✓ 成功获取 {len(transactions)} 笔交易\n")

        # 步骤 2: 分类统计
        print("步骤 2: 交易分类统计...")

        # 按类型分类
        transactions_by_type = defaultdict(list)
        type_counts = defaultdict(int)

        for tx in transactions:
            tx_type = tx.get('type', 'UNKNOWN')
            transactions_by_type[tx_type].append(tx)
            type_counts[tx_type] += 1

        # 显示统计
        print(f"\n交易类型统计 (共 {len(type_counts)} 种类型):\n")
        sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)

        for tx_type, count in sorted_types:
            percentage = (count / len(transactions)) * 100
            print(f"  {tx_type:<50} {count:>6} 笔 ({percentage:>5.2f}%)")

        # 步骤 3: 准备保存的数据
        print("\n步骤 3: 准备保存数据...")

        result = {
            "metadata": {
                "protocol": "Kamino",
                "program_address": kamino_address,
                "time_range_hours": hours,
                "fetch_time": datetime.now().isoformat(),
                "total_transactions": len(transactions),
                "transaction_types_count": len(type_counts),
            },
            "statistics": {
                "by_type": dict(type_counts),
                "by_type_percentage": {
                    tx_type: round((count / len(transactions)) * 100, 2)
                    for tx_type, count in type_counts.items()
                }
            },
            "transactions_by_type": {
                tx_type: txs for tx_type, txs in transactions_by_type.items()
            },
            "all_transactions": transactions
        }

        # 步骤 4: 保存到文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"kamino_transactions_categorized_{timestamp}.json"

        print(f"\n步骤 4: 保存到文件...")
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        print(f"✓ 数据已保存到: {filename}")

        # 步骤 5: 生成摘要报告
        print("\n" + "=" * 100)
        print("摘要报告")
        print("=" * 100)

        print(f"\n基本信息:")
        print(f"  总交易数: {len(transactions)}")
        print(f"  交易类型数: {len(type_counts)}")
        print(f"  时间范围: {hours} 小时")

        print(f"\n前 10 种交易类型:")
        for i, (tx_type, count) in enumerate(sorted_types[:10], 1):
            percentage = (count / len(transactions)) * 100
            print(f"  {i:2d}. {tx_type:<45} {count:>6} 笔 ({percentage:>5.2f}%)")

        # 时间分布分析
        print(f"\n时间分布:")
        if transactions:
            earliest = min(tx.get('timestamp', 0) for tx in transactions)
            latest = max(tx.get('timestamp', 0) for tx in transactions)
            print(f"  最早交易: {datetime.fromtimestamp(earliest)}")
            print(f"  最新交易: {datetime.fromtimestamp(latest)}")
            print(f"  时间跨度: {(latest - earliest) / 60:.1f} 分钟")

        # 代币转账统计
        print(f"\n代币转账统计:")
        total_token_transfers = sum(
            len(tx.get('tokenTransfers', [])) for tx in transactions
        )
        txs_with_transfers = sum(
            1 for tx in transactions if tx.get('tokenTransfers')
        )
        print(f"  总代币转账数: {total_token_transfers}")
        print(f"  含代币转账的交易: {txs_with_transfers} 笔")
        if txs_with_transfers > 0:
            print(f"  平均每笔: {total_token_transfers / txs_with_transfers:.2f} 次转账")

        # 费用统计
        print(f"\n费用统计:")
        total_fees = sum(tx.get('fee', 0) for tx in transactions) / 1e9  # lamports to SOL
        avg_fee = total_fees / len(transactions) if transactions else 0
        print(f"  总费用: {total_fees:.6f} SOL")
        print(f"  平均费用: {avg_fee:.6f} SOL")

        print("\n" + "=" * 100)
        print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 100)

        return result


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description="获取并分类 Kamino 交易"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=1,
        help="时间范围（小时），默认 1 小时"
    )

    args = parser.parse_args()

    try:
        result = await fetch_and_categorize_kamino_transactions(hours=args.hours)
        print("\n✓ 程序执行成功!")

    except Exception as e:
        print(f"\n✗ 程序执行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
