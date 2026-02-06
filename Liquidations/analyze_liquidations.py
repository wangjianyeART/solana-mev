#!/usr/bin/env python3
"""
深度分析 Kamino 数据，查找可能的清算交易模式
"""

import json
from pathlib import Path
from collections import Counter, defaultdict

def analyze_liquidation_patterns(folder_path):
    """
    分析可能的清算相关交易模式
    """
    folder = Path(folder_path)
    json_files = sorted(folder.glob("kamino_batch_*.json"))

    # 统计数据
    all_types = Counter()
    descriptions = Counter()

    # 可能与清算相关的交易
    suspicious_transactions = []

    # 按类型收集示例交易
    type_examples = defaultdict(list)

    print(f"分析 {len(json_files)} 个文件中可能的清算交易...\n")

    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'transactions' not in data:
                continue

            for tx in data['transactions']:
                tx_type = tx.get('type', 'UNKNOWN')
                all_types[tx_type] += 1

                # 收集 description
                desc = tx.get('description', '').strip()
                if desc:
                    descriptions[desc] += 1

                # 收集每种类型的示例（最多3个）
                if len(type_examples[tx_type]) < 3:
                    type_examples[tx_type].append({
                        'signature': tx.get('signature', ''),
                        'timestamp': tx.get('timestamp', 0),
                        'description': desc,
                        'tokenTransfers': len(tx.get('tokenTransfers', [])),
                        'fee': tx.get('fee', 0)
                    })

                # 查找可能的清算特征：
                # 1. 大额的代币转账
                # 2. 涉及 OBLIGATION 和 COLLATERAL 的操作
                # 3. REPAY + WITHDRAW 的组合
                token_transfers = tx.get('tokenTransfers', [])

                is_suspicious = False
                reasons = []

                # 检查是否有大额转账（>1000 tokens）
                for transfer in token_transfers:
                    amount = transfer.get('tokenAmount', 0)
                    if amount > 1000:
                        is_suspicious = True
                        reasons.append(f"大额转账: {amount}")

                # 检查类型名称中包含清算相关关键词
                liquidation_keywords = [
                    'OBLIGATION', 'COLLATERAL', 'REPAY',
                    'WITHDRAW_OBLIGATION', 'BORROW'
                ]

                for keyword in liquidation_keywords:
                    if keyword in tx_type:
                        is_suspicious = True
                        reasons.append(f"关键词: {keyword}")
                        break

                if is_suspicious:
                    suspicious_transactions.append({
                        'type': tx_type,
                        'signature': tx.get('signature', ''),
                        'timestamp': tx.get('timestamp', 0),
                        'reasons': reasons,
                        'tokenTransfers': len(token_transfers),
                        'description': desc
                    })

        except Exception as e:
            print(f"处理 {json_file.name} 时出错: {e}")
            continue

    return all_types, descriptions, suspicious_transactions, type_examples

def print_analysis_results(all_types, descriptions, suspicious_transactions, type_examples):
    """
    打印详细分析结果
    """
    print("=" * 80)
    print("📊 所有交易类型统计")
    print("=" * 80)
    for tx_type, count in all_types.most_common():
        print(f"{tx_type:<60} {count:>10,}")

    print("\n" + "=" * 80)
    print("🔍 可能与清算相关的交易类型")
    print("=" * 80)

    liquidation_related = [
        'WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL',
        'DEPOSIT_RESERVE_LIQUIDITY_AND_OBLIGATION_COLLATERAL',
        'BORROW_OBLIGATION_LIQUIDITY',
        'REPAY_OBLIGATION_LIQUIDITY',
        'REFRESH_OBLIGATION'
    ]

    for tx_type in liquidation_related:
        count = all_types.get(tx_type, 0)
        if count > 0:
            print(f"\n{tx_type}: {count:,} 笔")

            # 显示示例
            examples = type_examples.get(tx_type, [])
            if examples:
                print("  示例交易:")
                for i, ex in enumerate(examples, 1):
                    print(f"    {i}. 签名: {ex['signature'][:20]}...")
                    print(f"       代币转账数: {ex['tokenTransfers']}")
                    print(f"       手续费: {ex['fee']} lamports")
                    if ex['description']:
                        print(f"       描述: {ex['description']}")

    print("\n" + "=" * 80)
    print("🚨 可疑交易分析")
    print("=" * 80)
    print(f"找到 {len(suspicious_transactions)} 笔可能相关的交易\n")

    # 按类型分组可疑交易
    suspicious_by_type = defaultdict(int)
    for tx in suspicious_transactions:
        suspicious_by_type[tx['type']] += 1

    print("可疑交易按类型分组:")
    for tx_type, count in sorted(suspicious_by_type.items(), key=lambda x: x[1], reverse=True):
        print(f"  {tx_type}: {count:,} 笔")

    print("\n" + "=" * 80)
    print("📝 非空描述统计 (前10)")
    print("=" * 80)
    for desc, count in descriptions.most_common(10):
        print(f"{count:>6} 次: {desc}")

    print("\n" + "=" * 80)
    print("💡 分析结论")
    print("=" * 80)
    print("""
可能的情况：
1. Kamino Lend 可能使用不同的方式标识清算交易
2. 清算可能通过组合交易实现，而不是单独的 LIQUIDATE 类型
3. 关注这些交易类型：
   - WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL (可能是清算的一部分)
   - BORROW_OBLIGATION_LIQUIDITY + REPAY_OBLIGATION_LIQUIDITY (可能的清算流程)
   - REFRESH_OBLIGATION (清算前的状态刷新)
4. 这 7 天内可能确实没有发生清算事件（市场状况良好）
    """)

if __name__ == "__main__":
    folder_path = "/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_data_7d_20260129_215323/"

    print("开始深度分析...\n")
    all_types, descriptions, suspicious_transactions, type_examples = analyze_liquidation_patterns(folder_path)
    print_analysis_results(all_types, descriptions, suspicious_transactions, type_examples)

    # 保存可疑交易到文件
    output_file = "/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/suspicious_transactions.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'total_suspicious': len(suspicious_transactions),
            'transactions': suspicious_transactions[:100]  # 只保存前100笔
        }, f, indent=2)

    print(f"\n可疑交易已保存到: {output_file}")
