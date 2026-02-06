#!/usr/bin/env python3
"""
完全基于 IDL 的交易解析器
不依赖 type 字段，纯粹通过 IDL 解析指令
"""

import json
import hashlib
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime


def load_kamino_idl(idl_path: Path):
    """加载 Kamino Lend IDL"""
    with open(idl_path, 'r') as f:
        return json.load(f)


def base58_decode(s):
    """Base58 解码"""
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    num = 0
    for char in s:
        num = num * 58 + alphabet.index(char)

    # 处理前导零
    leading_zeros = len(s) - len(s.lstrip('1'))

    # 转换为字节
    if num == 0:
        return b'\x00' * leading_zeros

    encoded = []
    while num > 0:
        num, remainder = divmod(num, 256)
        encoded.insert(0, remainder)

    return b'\x00' * leading_zeros + bytes(encoded)


def compute_anchor_discriminator(instruction_name):
    """计算 Anchor 指令的 discriminator"""
    preimage = f"global:{instruction_name}"
    return hashlib.sha256(preimage.encode()).digest()[:8]


def build_idl_instruction_map(idl):
    """构建 IDL 指令映射"""
    instruction_map = {}

    for instruction in idl.get('instructions', []):
        name = instruction.get('name', '')

        # 计算 discriminator
        discriminator = compute_anchor_discriminator(name)

        instruction_info = {
            'name': name,
            'accounts': instruction.get('accounts', []),
            'args': instruction.get('args', []),
            'account_count': len(instruction.get('accounts', [])),
            'discriminator': discriminator,
            'discriminator_hex': discriminator.hex()
        }

        instruction_map[discriminator] = instruction_info

    return instruction_map


def identify_instruction_by_discriminator(instruction_data, instruction_map):
    """通过 discriminator 识别指令"""
    if not instruction_data:
        return None

    try:
        # 解码 base58
        data_bytes = base58_decode(instruction_data)

        if len(data_bytes) < 8:
            return None

        # 提取 discriminator
        discriminator = data_bytes[:8]

        # 查找匹配
        if discriminator in instruction_map:
            return instruction_map[discriminator]

        return None

    except Exception as e:
        return None


def identify_instruction_by_pattern(instruction, all_instructions, instruction_map):
    """
    通过模式匹配识别指令
    当 discriminator 匹配失败时使用
    """
    program_id = instruction.get('programId', '')

    if program_id != 'KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD':
        return None

    accounts = instruction.get('accounts', [])
    account_count = len(accounts)

    # 尝试通过账户数量和其他指令的上下文来推断
    # 这是一个启发式方法

    # 检查是否有其他 Kamino 指令在同一交易中
    kamino_instructions = [
        instr for instr in all_instructions
        if instr.get('programId') == 'KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD'
    ]

    # 如果有多个 Kamino 指令，且账户数量较多，可能是复杂操作（如清算）
    if len(kamino_instructions) >= 3 and account_count >= 15:
        # 可能是清算
        return {
            'name': 'liquidateObligationAndRedeemReserveCollateral',
            'confidence': 'medium',
            'reason': f'多个Kamino指令 + 大量账户 ({account_count})'
        }

    return None


def parse_transaction_with_idl(tx, instruction_map):
    """使用 IDL 解析交易"""
    analysis = {
        'signature': tx.get('signature', ''),
        'slot': tx.get('slot', 0),
        'timestamp': tx.get('timestamp', 0),
        'datetime': datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat() if tx.get('timestamp') else None,
        'original_type': tx.get('type', ''),  # 保留原始 type 用于对比
        'parsed_instructions': [],
        'is_liquidation': False,
        'liquidation_confidence': 'none'
    }

    instructions = tx.get('instructions', [])

    for i, instruction in enumerate(instructions):
        program_id = instruction.get('programId', '')
        data = instruction.get('data', '')
        accounts = instruction.get('accounts', [])

        parsed_instr = {
            'index': i,
            'program_id': program_id,
            'account_count': len(accounts),
            'identified_instruction': None,
            'identification_method': None
        }

        # 只解析 Kamino 指令
        if program_id == 'KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD':
            # 方法 1: 通过 discriminator
            identified = identify_instruction_by_discriminator(
                data, instruction_map)

            if identified:
                parsed_instr['identified_instruction'] = identified['name']
                parsed_instr['identification_method'] = 'discriminator'

                # 检查是否为清算
                if 'liquidat' in identified['name'].lower():
                    analysis['is_liquidation'] = True
                    analysis['liquidation_confidence'] = 'high'
            else:
                # 方法 2: 通过模式匹配
                identified = identify_instruction_by_pattern(
                    instruction, instructions, instruction_map)

                if identified:
                    parsed_instr['identified_instruction'] = identified['name']
                    parsed_instr['identification_method'] = 'pattern'
                    parsed_instr['confidence'] = identified.get(
                        'confidence', 'low')

                    if 'liquidat' in identified['name'].lower():
                        analysis['is_liquidation'] = True
                        analysis['liquidation_confidence'] = identified.get(
                            'confidence', 'low')
                else:
                    parsed_instr['identified_instruction'] = 'unknown_kamino_instruction'
                    parsed_instr['identification_method'] = 'failed'
                    parsed_instr['data_preview'] = data[:20] + \
                        '...' if len(data) > 20 else data

        if program_id != 'KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD':
            parsed_instr['identification_method'] = 'non_kamino'

        analysis['parsed_instructions'].append(parsed_instr)

    return analysis


def analyze_dataset(folder_path, idl, output_path):
    """分析整个数据集"""
    folder = Path(folder_path)
    json_files = sorted(folder.glob("kamino_batch_*.json"))

    # 构建指令映射
    print("构建 IDL 指令映射...")
    instruction_map = build_idl_instruction_map(idl)

    print(f"✓ 映射了 {len(instruction_map)} 个指令\n")

    # 显示一些指令的 discriminator
    print("清算相关指令的 discriminator:")
    for disc, info in instruction_map.items():
        if 'liquidat' in info['name'].lower():
            print(f"  {info['name']}")
            print(f"    Discriminator: {info['discriminator_hex']}")

    print("\n" + "=" * 80)
    print("开始解析交易...")
    print("=" * 80)

    stats = {
        'total_transactions': 0,
        'transactions_with_kamino': 0,
        'discriminator_matches': 0,
        'pattern_matches': 0,
        'failed_identifications': 0,
        'liquidations_found': 0,
        'liquidations_high_confidence': 0,
        'liquidations_medium_confidence': 0,
        'type_vs_idl_comparison': {
            'match': 0,
            'mismatch': 0,
            'details': []
        },
        'parsed_transactions': 0,
        'output_path': str(output_path)
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_file = open(output_path, 'w', encoding='utf-8')

    for i, json_file in enumerate(json_files, 1):
        try:
            print(f"[{i}/{len(json_files)}] 处理 {json_file.name}...")

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'transactions' not in data:
                continue

            for tx in data['transactions']:
                stats['total_transactions'] += 1

                # 检查是否有 Kamino 指令
                has_kamino = any(
                    instr.get(
                        'programId') == 'KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD'
                    for instr in tx.get('instructions', [])
                )

                if has_kamino:
                    stats['transactions_with_kamino'] += 1

                # 解析交易
                parsed = parse_transaction_with_idl(tx, instruction_map)

                # 统计识别方法
                for pinstr in parsed['parsed_instructions']:
                    method = pinstr.get('identification_method')
                    if method == 'discriminator':
                        stats['discriminator_matches'] += 1
                    elif method == 'pattern':
                        stats['pattern_matches'] += 1
                    elif method == 'failed':
                        stats['failed_identifications'] += 1

                # 统计清算
                if parsed['is_liquidation']:
                    stats['liquidations_found'] += 1

                    if parsed['liquidation_confidence'] == 'high':
                        stats['liquidations_high_confidence'] += 1
                    elif parsed['liquidation_confidence'] == 'medium':
                        stats['liquidations_medium_confidence'] += 1

                # 对比 type 字段和 IDL 解析结果
                original_type = parsed['original_type']
                is_liquidation_by_type = 'LIQUIDAT' in original_type or 'WITHDRAW_OBLIGATION_COLLATERAL' in original_type

                if is_liquidation_by_type == parsed['is_liquidation']:
                    stats['type_vs_idl_comparison']['match'] += 1
                else:
                    stats['type_vs_idl_comparison']['mismatch'] += 1
                    stats['type_vs_idl_comparison']['details'].append({
                        'signature': parsed['signature'],
                        'type': original_type,
                        'idl_says_liquidation': parsed['is_liquidation'],
                        'confidence': parsed['liquidation_confidence']
                    })

                output_file.write(json.dumps(
                    parsed, ensure_ascii=False) + "\n")
                stats['parsed_transactions'] += 1

        except Exception as e:
            print(f"  错误: {e}")
            continue

    output_file.close()
    return stats


def print_analysis_results(stats):
    """打印分析结果"""
    print("\n" + "=" * 80)
    print("IDL 解析结果")
    print("=" * 80)

    print(f"\n总体统计:")
    print(f"  总交易数: {stats['total_transactions']:,}")
    print(f"  包含 Kamino 指令: {stats['transactions_with_kamino']:,}")

    print(f"\n指令识别统计:")
    print(f"  Discriminator 匹配: {stats['discriminator_matches']}")
    print(f"  模式匹配: {stats['pattern_matches']}")
    print(f"  识别失败: {stats['failed_identifications']}")

    print(f"\n清算识别:")
    print(f"  总清算数: {stats['liquidations_found']}")
    print(f"  高置信度: {stats['liquidations_high_confidence']}")
    print(f"  中置信度: {stats['liquidations_medium_confidence']}")

    print(f"\ntype 字段 vs IDL 解析对比:")
    print(f"  匹配: {stats['type_vs_idl_comparison']['match']}")
    print(f"  不匹配: {stats['type_vs_idl_comparison']['mismatch']}")

    if stats['type_vs_idl_comparison']['details']:
        print(f"\n不匹配详情 (前5个):")
        for detail in stats['type_vs_idl_comparison']['details'][:5]:
            print(f"  签名: {detail['signature'][:20]}...")
            print(f"    type: {detail['type']}")
            print(f"    IDL判断为清算: {detail['idl_says_liquidation']}")
            print(f"    置信度: {detail['confidence']}")


def main():
    """主函数"""
    import argparse
    print("=" * 80)
    print("基于 IDL 的完整交易解析")
    print("=" * 80)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        default="/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_data_7d_20260129_215323/",
    )
    parser.add_argument(
        "--idl-path",
        default="/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_lending_idl.json",
    )
    parser.add_argument(
        "--output",
        default="/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_idl_parsed.jsonl",
    )
    args = parser.parse_args()

    # 加载 IDL
    print("\n加载 Kamino Lend IDL...")
    idl = load_kamino_idl(Path(args.idl_path))
    print(f"✓ IDL: {idl.get('name')} v{idl.get('version')}\n")

    # 分析
    folder_path = args.data_dir

    stats = analyze_dataset(folder_path, idl, Path(args.output))

    # 打印结果
    print_analysis_results(stats)

    print("\n" + "=" * 80)
    print("分析完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()
