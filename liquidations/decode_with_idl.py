"""
使用 Kamino IDL 精确解析 UNKNOWN 交易
"""
import json
import base58
import hashlib
import struct
from typing import Dict, Any, Optional


def calculate_anchor_discriminator(instruction_name: str) -> bytes:
    """
    计算 Anchor 指令的 discriminator
    discriminator = sha256(b"global:instruction_name")[:8]
    """
    preimage = f"global:{instruction_name}".encode()
    hash_result = hashlib.sha256(preimage).digest()
    return hash_result[:8]


def build_instruction_map(idl: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    构建指令 discriminator 到指令信息的映射
    """
    instruction_map = {}

    for instruction in idl.get('instructions', []):
        name = instruction['name']
        discriminator = calculate_anchor_discriminator(name)
        discriminator_hex = discriminator.hex()

        instruction_map[discriminator_hex] = {
            'name': name,
            'discriminator': discriminator_hex,
            'args': instruction.get('args', []),
            'accounts': instruction.get('accounts', []),
        }

    return instruction_map


def decode_instruction_data(data_b58: str, instruction_map: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    解码指令数据
    """
    try:
        data_bytes = base58.b58decode(data_b58)

        if len(data_bytes) < 8:
            return None

        # 提取 discriminator
        discriminator = data_bytes[:8]
        discriminator_hex = discriminator.hex()

        # 查找匹配的指令
        if discriminator_hex not in instruction_map:
            return {
                'found': False,
                'discriminator': discriminator_hex,
                'data_hex': data_bytes.hex(),
                'data_bytes': data_bytes,
            }

        instruction_info = instruction_map[discriminator_hex]

        # 提取参数数据
        params_data = data_bytes[8:]

        # 解析参数
        parsed_params = {}
        offset = 0

        for arg in instruction_info['args']:
            arg_name = arg['name']
            arg_type = arg['type']

            # 根据类型解析参数
            if arg_type == 'u64':
                if offset + 8 <= len(params_data):
                    value = struct.unpack('<Q', params_data[offset:offset+8])[0]
                    parsed_params[arg_name] = value
                    offset += 8
            elif arg_type == 'u8':
                if offset + 1 <= len(params_data):
                    value = params_data[offset]
                    parsed_params[arg_name] = value
                    offset += 1
            elif arg_type == 'bool':
                if offset + 1 <= len(params_data):
                    value = params_data[offset] != 0
                    parsed_params[arg_name] = value
                    offset += 1
            else:
                # 其他类型暂时记录为原始字节
                parsed_params[arg_name] = f"<{arg_type}>"

        return {
            'found': True,
            'instruction_name': instruction_info['name'],
            'discriminator': discriminator_hex,
            'args': instruction_info['args'],
            'parsed_params': parsed_params,
            'accounts': instruction_info['accounts'],
            'data_hex': data_bytes.hex(),
        }

    except Exception as e:
        return {
            'error': str(e),
            'data': data_b58
        }


def main():
    """主函数"""

    print("=" * 120)
    print("使用 Kamino IDL 解析 UNKNOWN 交易")
    print("=" * 120)

    # 1. 读取 IDL
    print("\n步骤 1: 读取 Kamino Lending IDL...")
    with open('kamino_lending.json', 'r') as f:
        idl = json.load(f)

    print(f"✓ IDL 版本: {idl.get('version')}")
    print(f"✓ 程序名称: {idl.get('name')}")
    print(f"✓ 指令数量: {len(idl.get('instructions', []))}")

    # 2. 构建指令映射
    print("\n步骤 2: 构建指令 discriminator 映射...")
    instruction_map = build_instruction_map(idl)
    print(f"✓ 已映射 {len(instruction_map)} 个指令")

    # 3. 解析 UNKNOWN 交易的指令数据
    print("\n步骤 3: 解析 UNKNOWN 交易指令数据...")
    print("=" * 120)

    unknown_instruction_data = "3LRnkGy8cDh"
    print(f"\n指令数据 (Base58): {unknown_instruction_data}")

    result = decode_instruction_data(unknown_instruction_data, instruction_map)

    if result.get('found'):
        print(f"\n✓ 成功识别指令!")
        print(f"\n指令名称: {result['instruction_name']}")
        print(f"Discriminator: {result['discriminator']}")

        # 显示参数定义
        print(f"\n参数定义:")
        for arg in result['args']:
            print(f"  - {arg['name']}: {arg['type']}")

        # 显示解析的参数值
        if result['parsed_params']:
            print(f"\n解析的参数值:")
            for param_name, param_value in result['parsed_params'].items():
                print(f"  - {param_name} = {param_value}")

        # 显示账户要求
        print(f"\n需要的账户数量: {len(result['accounts'])}")
        if len(result['accounts']) <= 10:
            print(f"\n账户列表:")
            for i, acc in enumerate(result['accounts'], 1):
                mut = "可写" if acc.get('isMut') else "只读"
                signer = "签名者" if acc.get('isSigner') else ""
                print(f"  {i:2d}. {acc['name']:<40} ({mut} {signer})")

    else:
        print(f"\n❌ 未找到匹配的指令")
        print(f"Discriminator: {result.get('discriminator')}")

    # 4. 显示所有指令的 discriminator
    print("\n\n" + "=" * 120)
    print("Kamino Lending 所有指令的 Discriminator")
    print("=" * 120)

    print("\n搜索清算相关指令:")
    for disc_hex, info in instruction_map.items():
        if 'liquidat' in info['name'].lower():
            print(f"\n指令: {info['name']}")
            print(f"  Discriminator: {disc_hex}")
            print(f"  参数: {len(info['args'])} 个")
            for arg in info['args']:
                print(f"    - {arg['name']}: {arg['type']}")

    # 5. 从实际交易数据中分析
    print("\n\n" + "=" * 120)
    print("分析 UNKNOWN 交易数据")
    print("=" * 120)

    import glob
    json_files = glob.glob("kamino_transactions_categorized_*.json")
    if json_files:
        latest_file = max(json_files)
        with open(latest_file, 'r') as f:
            data = json.load(f)

        unknown_txs = data['transactions_by_type'].get('UNKNOWN', [])
        print(f"\n从文件读取 {len(unknown_txs)} 笔 UNKNOWN 交易")

        # 统计不同的指令数据
        instruction_data_counts = {}

        for tx in unknown_txs:
            instructions = tx.get('instructions', [])
            for inst in instructions:
                # 查找 Kamino 相关的指令
                program_id = inst.get('programId', '')
                if 'Kvau' in program_id or 'KLend' in program_id:
                    inst_data = inst.get('data', '')
                    if inst_data:
                        if inst_data not in instruction_data_counts:
                            instruction_data_counts[inst_data] = {
                                'count': 0,
                                'decoded': None,
                                'sample_tx': tx.get('signature', '')[:16]
                            }
                        instruction_data_counts[inst_data]['count'] += 1

        print(f"\n发现 {len(instruction_data_counts)} 种不同的指令数据模式:")

        for inst_data, info in sorted(instruction_data_counts.items(), key=lambda x: x[1]['count'], reverse=True):
            decoded = decode_instruction_data(inst_data, instruction_map)

            if decoded.get('found'):
                instruction_name = decoded['instruction_name']
                print(f"\n指令数据: {inst_data}")
                print(f"  ✓ 指令: {instruction_name}")
                print(f"  出现次数: {info['count']}")

                # 显示参数
                if decoded.get('parsed_params'):
                    print(f"  参数:")
                    for param_name, param_value in decoded['parsed_params'].items():
                        print(f"    - {param_name} = {param_value}")
            else:
                print(f"\n指令数据: {inst_data}")
                print(f"  ❌ 未识别")
                print(f"  出现次数: {info['count']}")

    print("\n" + "=" * 120)
    print("解析完成!")
    print("=" * 120)


if __name__ == "__main__":
    main()
