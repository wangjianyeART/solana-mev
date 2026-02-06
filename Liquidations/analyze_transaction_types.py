#!/usr/bin/env python3
"""
统计 Kamino 数据文件夹中所有交易类型
"""

import json
import os
from collections import Counter
from pathlib import Path

def analyze_transaction_types(folder_path):
    """
    分析文件夹中所有 JSON 文件的交易类型

    Args:
        folder_path: JSON 文件所在的文件夹路径

    Returns:
        Counter: 交易类型统计结果
    """
    transaction_types = Counter()
    total_transactions = 0
    processed_files = 0

    folder = Path(folder_path)
    json_files = sorted(folder.glob("*.json"))

    print(f"找到 {len(json_files)} 个 JSON 文件")
    print("=" * 60)

    for json_file in json_files:
        try:
            print(f"正在处理: {json_file.name}...", end=" ")

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 统计当前文件的交易
            file_tx_count = 0
            if 'transactions' in data:
                for tx in data['transactions']:
                    if 'type' in tx:
                        transaction_types[tx['type']] += 1
                        file_tx_count += 1
                        total_transactions += 1

            processed_files += 1
            print(f"完成 ({file_tx_count} 笔交易)")

        except Exception as e:
            print(f"错误: {e}")
            continue

    print("=" * 60)
    print(f"\n处理完成！")
    print(f"总共处理了 {processed_files} 个文件")
    print(f"总交易数: {total_transactions:,}\n")

    return transaction_types

def print_statistics(transaction_types, total_transactions):
    """
    打印统计结果

    Args:
        transaction_types: Counter 对象
        total_transactions: 总交易数
    """
    print("=" * 60)
    print("交易类型统计结果")
    print("=" * 60)
    print(f"{'交易类型':<40} {'数量':>10} {'占比':>8}")
    print("-" * 60)

    for tx_type, count in transaction_types.most_common():
        percentage = (count / total_transactions * 100) if total_transactions > 0 else 0
        print(f"{tx_type:<40} {count:>10,} {percentage:>7.2f}%")

    print("=" * 60)
    print(f"{'总计':<40} {total_transactions:>10,} {'100.00%':>8}")
    print("=" * 60)

def save_results(transaction_types, output_file):
    """
    保存统计结果到文件

    Args:
        transaction_types: Counter 对象
        output_file: 输出文件路径
    """
    results = {
        'total_transactions': sum(transaction_types.values()),
        'unique_types': len(transaction_types),
        'transaction_types': dict(transaction_types.most_common())
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n统计结果已保存到: {output_file}")

if __name__ == "__main__":
    # 设置文件夹路径
    folder_path = "/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_data_7d_20260129_215323/"
    output_file = "/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/transaction_types_statistics.json"

    # 分析交易类型
    transaction_types = analyze_transaction_types(folder_path)

    # 打印统计结果
    total_transactions = sum(transaction_types.values())
    print_statistics(transaction_types, total_transactions)

    # 保存结果
    save_results(transaction_types, output_file)
