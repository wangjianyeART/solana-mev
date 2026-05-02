#!/usr/bin/env python3
"""
基于现有数据的 Kamino 完整分析
- 统计所有交易类型
- 使用 IDL 识别清算交易
- 详细分析每笔清算交易
- 不需要查询 RPC
"""

import json
import base64
import hashlib
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

def load_kamino_idl():
    """加载 Kamino Lend IDL"""
    idl_path = Path(__file__).parent / "kamino_lending.json"
    with open(idl_path, 'r') as f:
        return json.load(f)

def get_instruction_discriminator(instruction_name):
    """
    计算 Anchor 指令的 discriminator (前8字节)
    Anchor 使用 sha256("global:<instruction_name>")[0:8]
    """
    preimage = f"global:{instruction_name}"
    hash_result = hashlib.sha256(preimage.encode()).digest()
    return hash_result[:8]

def build_discriminator_map(idl):
    """构建指令名称到 discriminator 的映射"""
    discriminator_map = {}

    for instruction in idl.get('instructions', []):
        name = instruction.get('name', '')
        disc = get_instruction_discriminator(name)
        discriminator_map[disc] = {
            'name': name,
            'args': instruction.get('args', []),
            'accounts': instruction.get('accounts', [])
        }

    return discriminator_map

def decode_base58(s):
    """解码 base58 字符串"""
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    decoded = 0
    for char in s:
        decoded = decoded * 58 + alphabet.index(char)
    return decoded.to_bytes((decoded.bit_length() + 7) // 8, 'big')

def identify_instruction(instruction_data, discriminator_map, program_id):
    """
    识别指令类型
    """
    if not instruction_data or program_id != "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD":
        return None

    try:
        # 解码 base58
        data_bytes = decode_base58(instruction_data)

        if len(data_bytes) < 8:
            return None

        # 提取 discriminator (前8字节)
        discriminator = data_bytes[:8]

        # 查找匹配的指令
        if discriminator in discriminator_map:
            return discriminator_map[discriminator]

    except Exception as e:
        pass

    return None

def is_liquidation_transaction(tx, discriminator_map):
    """
    判断是否为清算交易
    基于指令分析
    """
    instructions = tx.get('instructions', [])

    for instruction in instructions:
        program_id = instruction.get('programId', '')
        data = instruction.get('data', '')

        if program_id == "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD":
            instr_info = identify_instruction(data, discriminator_map, program_id)

            if instr_info and 'liquidat' in instr_info['name'].lower():
                return True, instr_info['name']

    # 也检查 type 字段
    tx_type = tx.get('type', '').upper()
    liquidation_keywords = [
        'LIQUIDATE',
        'WITHDRAW_OBLIGATION_COLLATERAL_AND_REDEEM_RESERVE_COLLATERAL'
    ]

    for keyword in liquidation_keywords:
        if keyword in tx_type:
            return True, tx_type

    return False, None

def analyze_liquidation_transaction(tx, idl):
    """详细分析清算交易"""

    analysis = {
        'signature': tx.get('signature', ''),
        'slot': tx.get('slot', 0),
        'timestamp': tx.get('timestamp', 0),
        'datetime': datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat() if tx.get('timestamp') else None,
        'type': tx.get('type', ''),
        'description': tx.get('description', ''),
        'source': tx.get('source', ''),
        'fee': tx.get('fee', 0),
        'fee_sol': tx.get('fee', 0) / 1e9,
        'fee_payer': tx.get('feePayer', ''),

        # 代币转账
        'token_transfers': [],
        'native_transfers': [],

        # 账户余额变化
        'account_balance_changes': [],

        # 指令信息
        'instructions': [],

        # 清算分析
        'liquidation_analysis': None
    }

    # 提取代币转账
    for transfer in tx.get('tokenTransfers', []):
        token_transfer = {
            'from': transfer.get('fromUserAccount', ''),
            'to': transfer.get('toUserAccount', ''),
            'amount': transfer.get('tokenAmount', 0),
            'mint': transfer.get('mint', ''),
            'token_standard': transfer.get('tokenStandard', '')
        }
        analysis['token_transfers'].append(token_transfer)

    # 提取原生转账
    for transfer in tx.get('nativeTransfers', []):
        native_transfer = {
            'from': transfer.get('fromUserAccount', ''),
            'to': transfer.get('toUserAccount', ''),
            'amount': transfer.get('amount', 0),
            'amount_sol': transfer.get('amount', 0) / 1e9
        }
        analysis['native_transfers'].append(native_transfer)

    # 提取账户余额变化
    for account in tx.get('accountData', []):
        native_change = account.get('nativeBalanceChange', 0)
        token_changes = account.get('tokenBalanceChanges', [])

        if native_change != 0 or token_changes:
            account_change = {
                'account': account.get('account', ''),
                'native_balance_change': native_change,
                'native_balance_change_sol': native_change / 1e9,
                'token_balance_changes': []
            }

            for tc in token_changes:
                raw_amount = tc.get('rawTokenAmount', {})
                token_amount_str = raw_amount.get('tokenAmount', '0')
                decimals = raw_amount.get('decimals', 0)

                try:
                    token_amount = int(token_amount_str)
                    normalized_amount = token_amount / (10 ** decimals)
                except:
                    token_amount = 0
                    normalized_amount = 0

                account_change['token_balance_changes'].append({
                    'mint': tc.get('mint', ''),
                    'amount': token_amount,
                    'normalized_amount': normalized_amount,
                    'decimals': decimals
                })

            analysis['account_balance_changes'].append(account_change)

    # 提取指令信息
    for instruction in tx.get('instructions', []):
        instr_data = {
            'program_id': instruction.get('programId', ''),
            'accounts': instruction.get('accounts', []),
            'data': instruction.get('data', ''),
            'inner_instructions_count': len(instruction.get('innerInstructions', []))
        }
        analysis['instructions'].append(instr_data)

    # 清算分析（基于余额变化推断）
    liquidation_analysis = analyze_liquidation_from_transfers(analysis)
    if liquidation_analysis:
        analysis['liquidation_analysis'] = liquidation_analysis

    return analysis

def analyze_liquidation_from_transfers(analysis):
    """
    从代币转账和余额变化中推断清算详情
    """
    # 查找 USDC 和 SOL 的转账
    usdc_mint = "EPjFWdd5AufqSSqeM2qN1rCzohJWQao7T4eMNZPCfJrx"
    sol_mint = "So11111111111111111111111111111111111111112"

    usdc_paid = 0
    sol_received = 0

    # 从余额变化中提取
    for account_change in analysis['account_balance_changes']:
        # 检查代币变化
        for token_change in account_change['token_balance_changes']:
            mint = token_change['mint']
            amount = token_change['amount']
            normalized = token_change['normalized_amount']

            # USDC 减少 = 支付
            if mint == usdc_mint and amount < 0:
                usdc_paid += abs(normalized)

            # SOL 增加 = 获得
            if mint == sol_mint and amount > 0:
                sol_received += normalized

        # 检查原生 SOL 变化
        native_change = account_change['native_balance_change_sol']
        if native_change > 0:
            sol_received += native_change

    if usdc_paid > 0 or sol_received > 0:
        return {
            'usdc_paid': usdc_paid,
            'sol_received': sol_received,
            'has_significant_amounts': usdc_paid > 1 or sol_received > 0.001
        }

    return None

def analyze_all_transactions(folder_path, idl):
    """分析所有交易"""
    folder = Path(folder_path)
    json_files = sorted(folder.glob("kamino_batch_*.json"))

    # 构建 discriminator 映射
    print("构建 IDL discriminator 映射...")
    discriminator_map = build_discriminator_map(idl)
    print(f"✓ 映射了 {len(discriminator_map)} 个指令")

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

    print(f"\n开始分析 {len(json_files)} 个文件...")
    print("=" * 80)

    for i, json_file in enumerate(json_files, 1):
        try:
            if i % 10 == 0:
                print(f"[{i}/{len(json_files)}] 处理中... 已找到 {len(stats['liquidation_transactions'])} 笔清算")

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'transactions' not in data:
                continue

            for tx in data['transactions']:
                # 基本统计
                tx_type = tx.get('type', 'UNKNOWN')
                tx_source = tx.get('source', 'UNKNOWN')
                tx_signature = tx.get('signature', '')

                stats['transaction_types'][tx_type] += 1
                stats['sources'][tx_source] += 1
                stats['unique_signatures'].add(tx_signature)
                stats['total_transactions'] += 1

                # 收集示例
                if len(type_examples[tx_type]) < 3:
                    type_examples[tx_type].append({
                        'signature': tx_signature,
                        'timestamp': tx.get('timestamp', 0),
                        'slot': tx.get('slot', 0)
                    })

                # 检查是否为清算
                is_liq, liq_type = is_liquidation_transaction(tx, discriminator_map)

                if is_liq:
                    liquidation_analysis = analyze_liquidation_transaction(tx, idl)
                    liquidation_analysis['liquidation_type'] = liq_type
                    stats['liquidation_transactions'].append(liquidation_analysis)

        except Exception as e:
            print(f"错误处理 {json_file.name}: {e}")
            continue

    print("=" * 80)
    print(f"分析完成！")
    print(f"总交易数: {stats['total_transactions']:,}")
    print(f"唯一交易数: {len(stats['unique_signatures']):,}")
    print(f"清算交易数: {len(stats['liquidation_transactions']):,}")

    stats['unique_transactions'] = len(stats['unique_signatures'])
    stats['unique_signatures'] = None

    return stats, type_examples

def save_results(stats, type_examples, output_dir):
    """保存分析结果"""
    output_dir = Path(output_dir)

    # 1. 保存完整统计
    statistics = {
        'analysis_time': datetime.now().isoformat(),
        'analysis_method': 'IDL-based instruction decoding (no RPC)',
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
        'sources': dict(stats['sources'])
    }

    stats_file = output_dir / "kamino_comprehensive_statistics.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(statistics, f, indent=2, ensure_ascii=False)
    print(f"\n✓ 统计结果: {stats_file}")

    # 2. 保存清算交易
    if stats['liquidation_transactions']:
        liquidations_file = output_dir / "kamino_all_liquidations.json"
        liquidations_data = {
            'analysis_time': datetime.now().isoformat(),
            'total_liquidations': len(stats['liquidation_transactions']),
            'liquidations': stats['liquidation_transactions']
        }

        with open(liquidations_file, 'w', encoding='utf-8') as f:
            json.dump(liquidations_data, f, indent=2, ensure_ascii=False)
        print(f"✓ 清算交易: {liquidations_file}")

        return stats_file, liquidations_file

    print("✗ 未发现清算交易")
    return stats_file, None

def print_summary(stats, type_examples):
    """打印统计摘要"""
    print("\n" + "=" * 80)
    print("统计摘要")
    print("=" * 80)

    print(f"\n总体统计:")
    print(f"  文件数: {stats['total_files']}")
    print(f"  总交易数: {stats['total_transactions']:,}")
    print(f"  唯一交易数: {stats['unique_transactions']:,}")
    print(f"  交易类型数: {len(stats['transaction_types'])}")
    print(f"  清算交易数: {len(stats['liquidation_transactions']):,}")

    print(f"\n交易类型分布:")
    print(f"{'类型':<70} {'数量':>12} {'占比':>8}")
    print("-" * 90)

    for tx_type, count in stats['transaction_types'].most_common():
        percentage = (count / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0
        print(f"{tx_type:<70} {count:>12,} {percentage:>7.2f}%")

    if stats['liquidation_transactions']:
        print(f"\n" + "=" * 80)
        print(f"清算交易详情 ({len(stats['liquidation_transactions'])} 笔)")
        print("=" * 80)

        for i, liq in enumerate(stats['liquidation_transactions'], 1):
            print(f"\n清算 #{i}")
            print(f"  签名: {liq['signature']}")
            print(f"  类型: {liq.get('liquidation_type', liq['type'])}")
            print(f"  时间: {liq['datetime']}")
            print(f"  代币转账: {len(liq['token_transfers'])} 笔")
            print(f"  原生转账: {len(liq['native_transfers'])} 笔")

            if liq.get('liquidation_analysis'):
                liq_analysis = liq['liquidation_analysis']
                print(f"  USDC 支付: {liq_analysis.get('usdc_paid', 0):.6f}")
                print(f"  SOL 获得: {liq_analysis.get('sol_received', 0):.9f}")

def main():
    """主函数"""
    print("=" * 80)
    print("Kamino 数据完整分析（基于现有数据）")
    print("=" * 80)

    # 加载 IDL
    print("\n加载 Kamino Lend IDL...")
    idl = load_kamino_idl()
    print(f"✓ IDL: {idl.get('name', 'unknown')} v{idl.get('version', 'unknown')}")

    # 显示清算相关指令
    print("\nIDL 中的清算指令:")
    for instruction in idl.get('instructions', []):
        name = instruction.get('name', '')
        if 'liquidat' in name.lower():
            print(f"  - {name}")

    # 分析
    folder_path = "liquidations/kamino_data_7d_20260129_215323/"
    output_dir = "liquidations/"

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
    print("完成！")
    print("=" * 80)

if __name__ == "__main__":
    main()
