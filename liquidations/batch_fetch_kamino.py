"""
批量获取 Kamino 过去 7 天的交易数据
- 每 1000 笔保存到一个 JSON 文件
- 统计所有交易类型
"""
import asyncio
import json
import os
from datetime import datetime
from collections import defaultdict
from helius_client import HeliusClient
import config


async def batch_fetch_kamino_data():
    """
    批量获取并保存 Kamino 数据
    """
    print("=" * 120)
    print("Kamino 批量数据获取程序")
    print("=" * 120)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"时间范围: 过去 7 天")
    print(f"保存策略: 每 1000 笔一个文件")
    print("=" * 120)

    # 1. 创建文件夹
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_folder = f"kamino_data_{timestamp}"
    stats_folder = f"kamino_stats_{timestamp}"

    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(stats_folder, exist_ok=True)

    print(f"\n创建文件夹:")
    print(f"  数据文件夹: {data_folder}/")
    print(f"  统计文件夹: {stats_folder}/")

    # 2. 获取 Kamino 数据
    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    print(f"\nKamino 程序地址: {kamino_address}")

    async with HeliusClient(config.HELIUS_API_KEY) as client:
        print("\n步骤 1: 获取交易数据...")

        # 获取 7 天的数据
        all_transactions = await client.get_transactions_in_time_range(
            address=kamino_address,
            hours=168  # 7 天
        )

        total_count = len(all_transactions)
        print(f"✓ 成功获取 {total_count} 笔交易")

        # 3. 分批保存
        print(f"\n步骤 2: 分批保存数据 (每 1000 笔一个文件)...")

        batch_size = 1000
        batch_count = (total_count + batch_size - 1) // batch_size  # 向上取整

        batch_files = []

        for i in range(batch_count):
            start_idx = i * batch_size
            end_idx = min((i + 1) * batch_size, total_count)
            batch_transactions = all_transactions[start_idx:end_idx]

            # 构建文件名
            batch_filename = f"kamino_batch_{i+1:03d}_of_{batch_count:03d}.json"
            batch_filepath = os.path.join(data_folder, batch_filename)

            # 保存批次数据
            batch_data = {
                "metadata": {
                    "batch_number": i + 1,
                    "total_batches": batch_count,
                    "batch_size": len(batch_transactions),
                    "start_index": start_idx,
                    "end_index": end_idx,
                    "timestamp": datetime.now().isoformat(),
                },
                "transactions": batch_transactions
            }

            with open(batch_filepath, 'w', encoding='utf-8') as f:
                json.dump(batch_data, f, indent=2, ensure_ascii=False)

            batch_files.append(batch_filename)
            print(f"  ✓ 批次 {i+1}/{batch_count}: {len(batch_transactions)} 笔交易 -> {batch_filename}")

        print(f"\n✓ 已保存 {batch_count} 个批次文件")

        # 4. 统计交易类型
        print(f"\n步骤 3: 统计交易类型...")

        type_counts = defaultdict(int)
        type_details = defaultdict(list)

        for tx in all_transactions:
            tx_type = tx.get('type', 'UNKNOWN')
            type_counts[tx_type] += 1

            # 保存每种类型的示例交易（最多 5 个）
            if len(type_details[tx_type]) < 5:
                type_details[tx_type].append({
                    "signature": tx.get('signature'),
                    "timestamp": tx.get('timestamp'),
                    "feePayer": tx.get('feePayer'),
                })

        # 排序
        sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)

        print(f"\n交易类型统计 (共 {len(type_counts)} 种):")
        for tx_type, count in sorted_types:
            percentage = (count / total_count) * 100
            print(f"  {tx_type:<50} {count:>6} 笔 ({percentage:>5.2f}%)")

        # 5. 保存统计信息
        print(f"\n步骤 4: 保存统计数据...")

        # 创建统计摘要
        statistics = {
            "summary": {
                "total_transactions": total_count,
                "time_range_days": 7,
                "fetch_time": datetime.now().isoformat(),
                "total_types": len(type_counts),
                "data_folder": data_folder,
                "total_batches": batch_count,
                "batch_size": batch_size,
            },
            "type_counts": dict(type_counts),
            "type_percentages": {
                tx_type: round((count / total_count) * 100, 2)
                for tx_type, count in type_counts.items()
            },
            "type_examples": {
                tx_type: examples
                for tx_type, examples in type_details.items()
            },
            "sorted_by_count": [
                {
                    "type": tx_type,
                    "count": count,
                    "percentage": round((count / total_count) * 100, 2)
                }
                for tx_type, count in sorted_types
            ],
            "batch_files": batch_files,
        }

        # 保存统计文件
        stats_filename = f"transaction_statistics.json"
        stats_filepath = os.path.join(stats_folder, stats_filename)

        with open(stats_filepath, 'w', encoding='utf-8') as f:
            json.dump(statistics, f, indent=2, ensure_ascii=False)

        print(f"  ✓ 统计数据已保存: {stats_filepath}")

        # 6. 创建索引文件
        index_data = {
            "created_at": datetime.now().isoformat(),
            "protocol": "Kamino",
            "time_range_days": 7,
            "total_transactions": total_count,
            "total_batches": batch_count,
            "data_folder": data_folder,
            "stats_folder": stats_folder,
            "batch_files": batch_files,
            "statistics_file": stats_filename,
        }

        index_filepath = f"index_{timestamp}.json"
        with open(index_filepath, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)

        print(f"  ✓ 索引文件已创建: {index_filepath}")

        # 7. 生成摘要报告
        print("\n" + "=" * 120)
        print("执行摘要")
        print("=" * 120)

        print(f"\n数据获取:")
        print(f"  总交易数: {total_count}")
        print(f"  时间范围: 7 天")
        print(f"  协议: Kamino")

        print(f"\n文件组织:")
        print(f"  数据文件夹: {data_folder}/")
        print(f"    - 批次文件: {batch_count} 个")
        print(f"    - 每批次: ~{batch_size} 笔")
        print(f"  统计文件夹: {stats_folder}/")
        print(f"    - 统计文件: {stats_filename}")
        print(f"  索引文件: {index_filepath}")

        print(f"\n交易类型分布 (前 10):")
        for i, (tx_type, count) in enumerate(sorted_types[:10], 1):
            percentage = (count / total_count) * 100
            print(f"  {i:2d}. {tx_type:<45} {count:>6} 笔 ({percentage:>5.2f}%)")

        # 时间分布
        if all_transactions:
            earliest = min(tx.get('timestamp', 0) for tx in all_transactions)
            latest = max(tx.get('timestamp', 0) for tx in all_transactions)
            print(f"\n时间分布:")
            print(f"  最早: {datetime.fromtimestamp(earliest)}")
            print(f"  最新: {datetime.fromtimestamp(latest)}")
            print(f"  跨度: {(latest - earliest) / 3600:.1f} 小时")

        print("\n" + "=" * 120)
        print("完成!")
        print("=" * 120)
        print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 120)

        return {
            "success": True,
            "data_folder": data_folder,
            "stats_folder": stats_folder,
            "index_file": index_filepath,
            "total_transactions": total_count,
            "batch_count": batch_count,
        }


async def main():
    """主函数"""
    try:
        result = await batch_fetch_kamino_data()

        if result['success']:
            print(f"\n✓ 所有数据已成功保存!")
            print(f"\n快速访问:")
            print(f"  数据文件: {result['data_folder']}/")
            print(f"  统计文件: {result['stats_folder']}/")
            print(f"  索引文件: {result['index_file']}")

    except Exception as e:
        print(f"\n❌ 程序执行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
