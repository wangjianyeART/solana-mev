#!/usr/bin/env python3
"""
全面分析 Kamino 数据：
1. 统计所有交易类型
2. 识别并详细解析所有清算交易
3. 使用 IDL 进行深度分析
"""

import json
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

def load_kamino_idl():
    """加载 Kamino Lend IDL"""
    idl_path = Path(__file__).parent / "kamino_lending.json"
    with open(idl_path, 'r') as f:
        return json.load(f)

def is_liquidation_transaction(tx):
    """
    判断是否为清算交易
    通过检查交易的 description 或其他字段
    """
    # 检查 type
    tx_type = tx.get('type', '')
    if 'liquidat' in tx_type.lower():
        return True

    # 检查 description
    description = tx.get('description', '')
    if 'liquidat' in description.lower():
        return True

    return False

def extract_liquidation_details_from_logs(tx):
    """
    从交易日志中提取清算详细信息
    注意：原始数据中可能没有日志，需要从 RPC 查询
    """
    # 这里我们暂时从已有的交易数据中提取
    # 实际应用中可能需要查询 RPC 获取完整日志

    details = {
        'has_liquidation_log': False,
        'liquidation_amount': None,
        'collateral_withdrawn': None,
        'protocol_fee': None,
        'sol_price': None,
        'usdc_price': None,
        'borrowed_value': None,
        'collateral_value': None,
        'ltv': None,
        'liquidation_bonus': None,
        'close_factor': None
    }

    return details

def analyze_liquidation_transaction(tx, idl):
    """
    深度分析单笔清算交易
    """
    analysis = {
        'signature': tx.get('signature', ''),
        'slot': tx.get('slot', 0),
        'timestamp': tx.get('timestamp', 0),
        'datetime': datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat() if tx.get('timestamp') else None,
        'type': tx.get('type', ''),
        'description': tx.get('description', ''),
        'fee': tx.get('fee', 0),
        'fee_payer': tx.get('feePayer', ''),

        # 代币转账
        'token_transfers': [],
        'native_transfers': [],

        # 账户余额变化
        'account_changes': [],

        # 清算详情（需要从日志或进一步分析中获取）
        'liquidation_details': None,

        # 利润分析
        'profit_analysis': None
    }

    # 提取代币转账
    for transfer in tx.get('tokenTransfers', []):
        analysis['token_transfers'].append({
            'from': transfer.get('fromUserAccount', ''),
            'to': transfer.get('toUserAccount', ''),
            'amount': transfer.get('tokenAmount', 0),
            'mint': transfer.get('mint', ''),
            'token_standard': transfer.get('tokenStandard', '')
        })

    # 提取原生转账
    for transfer in tx.get('nativeTransfers', []):
        analysis['native_transfers'].append({
            'from': transfer.get('fromUserAccount', ''),
            'to': transfer.get('toUserAccount', ''),
            'amount': transfer.get('amount', 0)
        })

    # 提取账户余额变化
    for account in tx.get('accountData', []):
        native_change = account.get('nativeBalanceChange', 0)
        token_changes = account.get('tokenBalanceChanges', [])

        if native_change != 0 or token_changes:
            analysis['account_changes'].append({
                'account': account.get('account', ''),
                'native_balance_change': native_change,
                'token_balance_changes': [
                    {
                        'mint': tc.get('mint', ''),
                        'amount': tc.get('rawTokenAmount', {}).get('tokenAmount', '0'),
                        'decimals': tc.get('rawTokenAmount', {}).get('decimals', 0)
                    }
                    for tc in token_changes
                ]
            })

    # 尝试提取清算详情
    liquidation_details = extract_liquidation_details_from_logs(tx)
    if liquidation_details['has_liquidation_log']:
        analysis['liquidation_details'] = liquidation_details

    return analysis

def analyze_all_transactions(folder_path, idl):
    """
    分析所有交易
    """
    folder = Path(folder_path)
    json_files = sorted(folder.glob("kamino_batch_*.json"))

    # 统计数据
    stats = {
        'total_files': len(json_files),
        'total_transactions': 0,
        'transaction_types': Counter(),
        'sources': Counter(),
        'unique_signatures': set(),
        'liquidation_transactions': []
    }

    # 按类型的交易示例
    type_examples = defaultdict(list)

    print(f"开始分析 {len(json_files)} 个文件...")
    print("=" * 80)

    for i, json_file in enumerate(json_files, 1):
        try:
            print(f"[{i}/{len(json_files)}] 处理 {json_file.name}...", end=" ")

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'transactions' not in data:
                print("跳过（无交易数据）")
                continue

            file_tx_count = 0
            file_liquidation_count = 0

            for tx in data['transactions']:
                # 基本统计
                tx_type = tx.get('type', 'UNKNOWN')
                tx_source = tx.get('source', 'UNKNOWN')
                tx_signature = tx.get('signature', '')

                stats['transaction_types'][tx_type] += 1
                stats['sources'][tx_source] += 1
                stats['unique_signatures'].add(tx_signature)
                stats['total_transactions'] += 1
                file_tx_count += 1

                # 收集示例（每种类型最多3个）
                if len(type_examples[tx_type]) < 3:
                    type_examples[tx_type].append({
                        'signature': tx_signature,
                        'timestamp': tx.get('timestamp', 0),
                        'description': tx.get('description', ''),
                        'fee': tx.get('fee', 0),
                        'token_transfers_count': len(tx.get('tokenTransfers', []))
                    })

                # 检查是否为清算交易
                if is_liquidation_transaction(tx):
                    file_liquidation_count += 1
                    liquidation_analysis = analyze_liquidation_transaction(tx, idl)
                    stats['liquidation_transactions'].append(liquidation_analysis)

            print(f"完成 (交易: {file_tx_count}, 清算: {file_liquidation_count})")

        except Exception as e:
            print(f"错误: {e}")
            continue

    # 转换 unique_signatures 为数量
    stats['unique_transactions'] = len(stats['unique_signatures'])
    stats['unique_signatures'] = None  # 不保存所有签名到 JSON

    print("=" * 80)
    print(f"分析完成！")
    print(f"总交易数: {stats['total_transactions']:,}")
    print(f"唯一交易数: {stats['unique_transactions']:,}")
    print(f"清算交易数: {len(stats['liquidation_transactions']):,}")

    return stats, type_examples

def save_results(stats, type_examples, output_dir):
    """
    保存分析结果
    """
    output_dir = Path(output_dir)

    # 1. 保存完整统计结果
    statistics = {
        'analysis_time': datetime.now().isoformat(),
        'summary': {
            'total_files': stats['total_files'],
            'total_transactions': stats['total_transactions'],
            'unique_transactions': stats['unique_transactions'],
            'total_liquidations': len(stats['liquidation_transactions']),
            'unique_types': len(stats['transaction_types']),
            'unique_sources': len(stats['sources'])
        },
        'transaction_types': {
            tx_type: {
                'count': count,
                'percentage': (count / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0,
                'examples': type_examples.get(tx_type, [])
            }
            for tx_type, count in stats['transaction_types'].most_common()
        },
        'sources': dict(stats['sources']),
        'liquidation_summary': {
            'total_count': len(stats['liquidation_transactions']),
            'percentage': (len(stats['liquidation_transactions']) / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0
        }
    }

    stats_file = output_dir / "kamino_analysis_statistics.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(statistics, f, indent=2, ensure_ascii=False)
    print(f"\n✓ 统计结果已保存: {stats_file}")

    # 2. 保存所有清算交易
    if stats['liquidation_transactions']:
        liquidations_file = output_dir / "kamino_liquidation_transactions.json"
        liquidations_data = {
            'analysis_time': datetime.now().isoformat(),
            'total_liquidations': len(stats['liquidation_transactions']),
            'liquidations': stats['liquidation_transactions']
        }

        with open(liquidations_file, 'w', encoding='utf-8') as f:
            json.dump(liquidations_data, f, indent=2, ensure_ascii=False)
        print(f"✓ 清算交易已保存: {liquidations_file}")
    else:
        print("✗ 未发现清算交易")

    return stats_file, liquidations_file if stats['liquidation_transactions'] else None

def print_summary(stats, type_examples):
    """
    打印统计摘要
    """
    print("\n" + "=" * 80)
    print("统计摘要")
    print("=" * 80)

    print(f"\n总体统计:")
    print(f"  文件数: {stats['total_files']}")
    print(f"  总交易数: {stats['total_transactions']:,}")
    print(f"  唯一交易数: {stats['unique_transactions']:,}")
    print(f"  交易类型数: {len(stats['transaction_types'])}")
    print(f"  清算交易数: {len(stats['liquidation_transactions']):,}")

    print(f"\n交易类型分布 (Top 10):")
    print(f"{'类型':<60} {'数量':>12} {'占比':>8}")
    print("-" * 80)

    for tx_type, count in stats['transaction_types'].most_common(10):
        percentage = (count / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0
        print(f"{tx_type:<60} {count:>12,} {percentage:>7.2f}%")

    if len(stats['transaction_types']) > 10:
        remaining = sum(count for _, count in list(stats['transaction_types'].most_common())[10:])
        percentage = (remaining / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0
        print(f"{'其他':<60} {remaining:>12,} {percentage:>7.2f}%")

    print(f"\n数据源分布:")
    for source, count in stats['sources'].most_common():
        percentage = (count / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0
        print(f"  {source}: {count:,} ({percentage:.2f}%)")

    if stats['liquidation_transactions']:
        print(f"\n清算交易分析:")
        print(f"  找到 {len(stats['liquidation_transactions'])} 笔清算交易")
        print(f"  占总交易的 {(len(stats['liquidation_transactions']) / stats['total_transactions'] * 100):.4f}%")

        # 按类型分组清算交易
        liquidation_types = Counter()
        for liq in stats['liquidation_transactions']:
            liquidation_types[liq['type']] += 1

        print(f"\n  清算交易类型:")
        for liq_type, count in liquidation_types.most_common():
            print(f"    {liq_type}: {count}")

def main():
    """
    主函数
    """
    print("=" * 80)
    print("Kamino 数据全面分析工具")
    print("=" * 80)

    # 加载 IDL
    print("\n加载 Kamino Lend IDL...")
    idl = load_kamino_idl()
    print(f"✓ IDL 加载成功: {idl.get('name', 'unknown')} v{idl.get('version', 'unknown')}")

    # 显示 IDL 中的清算指令
    print("\nIDL 中的清算相关指令:")
    for instruction in idl.get('instructions', []):
        name = instruction.get('name', '')
        if 'liquidat' in name.lower():
            print(f"  - {name}")

    # 分析所有交易
    folder_path = "/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_data_7d_20260129_215323/"
    output_dir = "/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/"

    print("\n" + "=" * 80)
    stats, type_examples = analyze_all_transactions(folder_path, idl)

    # 打印摘要
    print_summary(stats, type_examples)

    # 保存结果
    print("\n" + "=" * 80)
    print("保存结果...")
    print("=" * 80)

    stats_file, liquidations_file = save_results(stats, type_examples, output_dir)

    print("\n" + "=" * 80)
    print("分析完成！")
    print("=" * 80)
    print(f"\n生成的文件:")
    print(f"  1. 统计结果: {stats_file}")
    if liquidations_file:
        print(f"  2. 清算交易: {liquidations_file}")

if __name__ == "__main__":
    main()
