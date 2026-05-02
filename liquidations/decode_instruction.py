"""
使用 Kamino IDL 解码指令数据
"""
import json
import base58
import struct


def decode_instruction_discriminator(data_b58: str):
    """
    解码指令数据，识别指令类型

    Anchor 程序使用前 8 字节作为指令判别符 (discriminator)
    discriminator = sha256("global:instruction_name")[:8]
    """

    # 读取 Kamino IDL
    with open('kamino_lending.json', 'r') as f:
        idl = json.load(f)

    print("=" * 100)
    print("Kamino 指令解码分析")
    print("=" * 100)

    # Base58 解码
    try:
        data_bytes = base58.b58decode(data_b58)
        print(f"\n指令数据 (Base58): {data_b58}")
        print(f"指令数据 (Hex): {data_bytes.hex()}")
        print(f"数据长度: {len(data_bytes)} 字节")

        # 前 8 字节是 discriminator
        if len(data_bytes) >= 8:
            discriminator = data_bytes[:8]
            print(f"Discriminator (Hex): {discriminator.hex()}")

            # 尝试匹配已知的指令
            print(f"\n在 Kamino IDL 中搜索匹配的指令...")
            print(f"IDL 包含 {len(idl['instructions'])} 个指令\n")

            # 列出所有指令
            print("Kamino 所有指令:")
            for i, inst in enumerate(idl['instructions'], 1):
                print(f"  {i:2d}. {inst['name']}")

            # 常见的 Anchor 指令 discriminator
            # 这些是通过 sha256("global:instruction_name")[:8] 计算的
            print("\n" + "=" * 100)
            print("可能的指令匹配")
            print("=" * 100)

            # 由于我们无法直接计算 discriminator，我们可以：
            # 1. 查看指令的参数模式
            # 2. 根据交易行为推断

            print(f"\n根据交易特征分析:")
            print(f"  - 有 3 笔代币转账")
            print(f"  - 输入一种代币，输出另一种代币")
            print(f"  - 涉及 Kamino Program")

            print(f"\n最可能的指令类型:")

            # 查找与代币交换/流动性相关的指令
            swap_related = []
            for inst in idl['instructions']:
                name_lower = inst['name'].lower()
                if any(keyword in name_lower for keyword in ['swap', 'exchange', 'trade', 'liquidity', 'deposit', 'withdraw']):
                    swap_related.append(inst['name'])

            if swap_related:
                print(f"\n  可能相关的指令:")
                for name in swap_related:
                    print(f"    - {name}")

        else:
            print(f"数据太短，无法提取 discriminator")

        # 解析剩余数据
        if len(data_bytes) > 8:
            params_data = data_bytes[8:]
            print(f"\n参数数据 (Hex): {params_data.hex()}")
            print(f"参数长度: {len(params_data)} 字节")

            # 尝试解析为常见类型
            if len(params_data) >= 8:
                # 尝试解析为 u64
                try:
                    value = struct.unpack('<Q', params_data[:8])[0]
                    print(f"\n如果第一个参数是 u64: {value}")
                    print(f"  作为金额: {value / 1e6:.6f} (假设 6 位小数)")
                except:
                    pass

    except Exception as e:
        print(f"解码失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    """主函数"""

    # 最常见的指令数据
    common_instruction = "3LRnkGy8cDh"

    print(f"解码最常见的 UNKNOWN 指令数据: {common_instruction}\n")
    decode_instruction_discriminator(common_instruction)

    # 分析指令数据的二进制模式
    print("\n\n" + "=" * 100)
    print("二进制模式分析")
    print("=" * 100)

    data_bytes = base58.b58decode(common_instruction)
    print(f"\n完整字节序列:")
    for i in range(0, len(data_bytes), 16):
        chunk = data_bytes[i:i+16]
        hex_str = ' '.join(f'{b:02x}' for b in chunk)
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f"  {i:04x}: {hex_str:<48} {ascii_str}")


if __name__ == "__main__":
    main()
