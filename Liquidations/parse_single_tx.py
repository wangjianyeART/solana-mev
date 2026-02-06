#!/usr/bin/env python3
"""
基于 Kamino IDL 的单笔交易解析器
功能：
1. 根据 IDL 定义，计算指令 discriminator
2. 通过 RPC 获取指定交易的详细信息
3. 匹配并解码交易中的 Kamino 指令（含参数解析）
"""

import config
import json
import base58
import hashlib
import struct
import requests
from pathlib import Path
from datetime import datetime

# -----------------------------------------------------------------------------
# 1. IDL 与 Discriminator 处理
# -----------------------------------------------------------------------------


def load_idl(idl_path):
    """加载 IDL 文件"""
    with open(idl_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_function_discriminator(name):
    """
    计算 Anchor 指令 discriminator
    规则: sha256("global:<instruction_name>")[:8]
    """
    preimage = f"global:{name}"
    return hashlib.sha256(preimage.encode("utf-8")).digest()[:8]


def snake_to_camel(snake_str):
    """转换 snake_case 为 camelCase"""
    components = snake_str.split('_')
    # 第一个单词小写，后续单词首字母大写
    return components[0] + ''.join(x.title() for x in components[1:])


def build_instruction_decoder_map(idl):
    """
    构建 指令 discriminator -> 指令定义 的映射表
    """
    decoder_map = {}
    for instr in idl.get('instructions', []):
        name = instr['name']

        # 策略1: 直接使用 IDL 中的名称
        disc = get_function_discriminator(name)
        decoder_map[disc] = instr

        # 策略2: 尝试转换为 camelCase (如果 IDL 是 snake_case)
        if '_' in name:
            camel_name = snake_to_camel(name)
            disc_camel = get_function_discriminator(camel_name)
            decoder_map[disc_camel] = instr

    # Debug: 打印部分生成的 discriminator
    print(f"Loaded {len(decoder_map)} instructions from IDL.")
    # for d, i in list(decoder_map.items())[:5]:
    #     print(f"  {d.hex()} -> {i['name']}")
    return decoder_map

# -----------------------------------------------------------------------------
# 2. 数据解码工具
# -----------------------------------------------------------------------------


def decode_instruction_data(data_b58, decoder_map):
    """
    解码指令数据
    :param data_b58: base58 编码的指令数据字符串
    :param decoder_map: discriminator 映射表
    """
    try:
        data_bytes = base58.b58decode(data_b58)
    except Exception:
        return {"error": "Invalid base58 data", "raw": data_b58}

    if len(data_bytes) < 8:
        return {"error": "Data too short for discriminator", "raw": data_bytes.hex()}

    # 提取 discriminator
    disc = data_bytes[:8]
    instr_def = decoder_map.get(disc)

    if not instr_def:
        # Debug: 打印未识别的 discriminator
        # print(f"Unrecognized discriminator: {disc.hex()}")
        return {
            "name": "Unknown",
            "discriminator": disc.hex(),
            "raw_hex": data_bytes.hex()
        }

    # 解析参数 (简单实现：仅处理部分基础类型，复杂类型需完整 Borsh 解码器)
    # 注意：这里仅作演示，完整解析需要完整的 Borsh 反序列化实现
    decoded_args = {}
    args_def = instr_def.get('args', [])

    # 简单的参数解析尝试 (仅针对定长数值类型，作为示例)
    offset = 8

    # Debug: 打印数据长度和偏移量
    # print(f"Parsing args for {instr_def.get('name')}: Data len={len(data_bytes)}")

    # 特殊处理：如果数据长度仅为 8 (只有 discriminator)，但 args 定义不为空，说明：
    # 1. 可能是 IDL 定义有误（该指令实际上没有参数）
    # 2. 可能是参数在 account 中（不太可能）
    # 3. 可能是 parse_single_tx.py 获取的 transaction 数据不完整

    # 针对 flashBorrowReserveLiquidity 和 flashRepayReserveLiquidity，
    # 观察到数据只有 8 字节。这很奇怪，因为通常需要 liquidity_amount。
    # 可能性：amount=0 或者是通过其他方式传递？
    # 或者，这些指令确实没有参数，IDL 定义或我的手动映射有误？
    # 检查 IDL：
    # FlashBorrowReserveLiquidity: 02da8aeb4fc91966
    # 查阅 Kamino 文档/源码：Flash Borrow 的 amount 和 refer_index 可能是在 data 中。
    # 如果 data 只有 8 字节，那么确实没有参数。

    if len(data_bytes) == 8 and len(args_def) > 0:
        decoded_args["_note"] = "Data contains only discriminator (no args data)"

    try:
        for arg in args_def:
            arg_name = arg['name']
            arg_type = arg['type']

            # 如果数据已经不够了，提前退出
            if offset >= len(data_bytes):
                decoded_args[arg_name] = "<missing_data>"
                continue

            # 处理 u64 / i64
            if arg_type in ['u64', 'i64']:
                if len(data_bytes) >= offset + 8:
                    val = struct.unpack('<Q', data_bytes[offset:offset+8])[0]
                    decoded_args[arg_name] = val
                    offset += 8
                else:
                    decoded_args[
                        arg_name] = f"<data_too_short: need {offset+8}, have {len(data_bytes)}>"
            # 处理 u8
            elif arg_type == 'u8':
                if len(data_bytes) >= offset + 1:
                    val = data_bytes[offset]
                    decoded_args[arg_name] = val
                    offset += 1
                else:
                    decoded_args[
                        arg_name] = f"<data_too_short: need {offset+1}, have {len(data_bytes)}>"
            else:
                decoded_args[arg_name] = f"<type:{arg_type}>"

    except Exception as e:
        decoded_args["_parsing_error"] = str(e)

    return {
        "name": instr_def['name'],
        "discriminator": disc.hex(),
        "args": decoded_args,
        "definition": instr_def
    }


# -----------------------------------------------------------------------------
# 3. 交易获取与处理
# -----------------------------------------------------------------------------


def get_transaction(signature, rpc_url=None):
    """获取交易详情"""
    if not rpc_url:
        # 优先使用用户提供的 API Key
        api_key = "798a5b24-e063-489c-b208-f0c324f12676"
        if api_key:
            rpc_url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
        elif config.HELIUS_API_KEY:
            rpc_url = f"https://mainnet.helius-rpc.com/?api-key={config.HELIUS_API_KEY}"
        else:
            rpc_url = "https://api.mainnet-beta.solana.com"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "json", "maxSupportedTransactionVersion": 0}
        ]
    }

    try:
        # 使用 Helius RPC (如果默认公共节点失败)
        # 也可以从环境变量或配置文件读取
        headers = {"Content-Type": "application/json"}
        print(f"Using RPC URL: {rpc_url}")
        resp = requests.post(rpc_url, json=payload,
                             headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if 'error' in data:
            raise Exception(data['error'])
        return data.get('result')
    except Exception as e:
        print(f"RPC Error: {e}")
        return None


def print_transaction_analysis(tx_data, decoder_map, kamino_program_id):
    """打印交易分析结果"""
    if not tx_data:
        return

    slot = tx_data.get('slot')
    block_time = tx_data.get('blockTime')
    meta = tx_data.get('meta', {})

    print("=" * 60)
    print(f"Transaction Analysis")
    print("=" * 60)
    print(f"Slot: {slot}")
    print(f"Time: {datetime.fromtimestamp(block_time)}")
    print(f"Status: {'Success' if meta.get('err') is None else 'Failed'}")
    print(f"Fee: {meta.get('fee')} lamports")

    # ---------------------------------------------------------
    # 分析指令
    # ---------------------------------------------------------
    print("\n[Instructions Analysis]")
    message = tx_data['transaction']['message']
    instructions = message['instructions']

    # 构建完整的 Account List
    # 注意：对于 Versioned Transaction (v0)，需要处理 Address Table Lookups
    # 这里为了简化，先尝试处理 Legacy 和基本的 v0 静态账户列表
    # 如果是 v0 且使用了 Lookup Table，accountKeys 只包含静态账户，需要结合 meta.loadedAddresses 解析

    account_keys = message.get('accountKeys', [])
    # 兼容 v0 格式，accountKeys 可能是 list[str] (legacy) 或 list[dict] (parsed) 或其他
    # Helius 返回的 json 格式，accountKeys 通常是 list[str]

    loaded_addresses = meta.get('loadedAddresses', {})
    writable_lookup = loaded_addresses.get('writable', [])
    readonly_lookup = loaded_addresses.get('readonly', [])

    all_accounts = account_keys + writable_lookup + readonly_lookup

    for idx, instr in enumerate(instructions):
        prog_id = instr.get('programId')

        # 如果没有 programId，尝试通过 programIdIndex 获取
        if not prog_id:
            prog_idx = instr.get('programIdIndex')
            if prog_idx is not None and prog_idx < len(all_accounts):
                prog_id = all_accounts[prog_idx]
            else:
                print(
                    f"\n#{idx+1} [Skipped] Cannot resolve programId (Index: {prog_idx}, Accounts: {len(all_accounts)})")
                continue

        data_b58 = instr.get('data', '')

        print(f"\n#{idx+1} Program: {prog_id}")

        # 仅针对 Kamino 合约进行详细解析
        if prog_id == kamino_program_id:
            decoded = decode_instruction_data(data_b58, decoder_map)
            print(f"   └── Function: {decoded.get('name', 'Unknown')}")

            if 'args' in decoded:
                print(f"   └── Args: {json.dumps(decoded['args'], indent=2)}")

            if decoded.get('name') == 'Unknown':
                print(f"   └── Discriminator: {decoded.get('discriminator')}")
        else:
            # 其他合约简单显示
            print(f"   └── Data (base58): {data_b58[:30]}...")

    # ---------------------------------------------------------
    # 余额变化 (重点关注 SOL 和 USDC)
    # ---------------------------------------------------------
    print("\n[Balance Changes]")

    # Pre/Post Token Balances
    pre_tokens = {(x['accountIndex'], x['mint'])                  : x for x in meta.get('preTokenBalances', [])}
    post_tokens = {(x['accountIndex'], x['mint'])                   : x for x in meta.get('postTokenBalances', [])}

    all_keys = set(pre_tokens.keys()) | set(post_tokens.keys())

    for (idx, mint) in all_keys:
        pre_bal = float(pre_tokens.get((idx, mint), {}).get(
            'uiTokenAmount', {}).get('uiAmount') or 0)
        post_bal = float(post_tokens.get((idx, mint), {}).get(
            'uiTokenAmount', {}).get('uiAmount') or 0)
        diff = post_bal - pre_bal

        if diff != 0:
            print(f"   Account #{idx} ({mint[:8]}...): {diff:+.6f}")

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main():
    # 配置
    TX_SIGNATURE = "5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg"
    KAMINO_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"
    IDL_PATH = "kamino_lending_idl.json"  # 请确保此文件存在于当前目录或指定绝对路径

    # 1. 加载 IDL
    print(f"Loading IDL from {IDL_PATH}...")
    try:
        # 使用绝对路径以防万一
        base_dir = Path(__file__).parent
        idl_full_path = base_dir / IDL_PATH
        if not idl_full_path.exists():
            # 尝试上一级目录或当前工作目录作为备选
            idl_full_path = Path("kamino_lending.json").resolve()

        idl = load_idl(idl_full_path)
        decoder_map = build_instruction_decoder_map(idl)
        print(f"Loaded {len(decoder_map)} instructions from IDL.")

        # 手动添加一些已知指令的 discriminator 映射
        # 这些指令的 discriminator 已通过交易分析确认，但在 IDL 中可能因名称不匹配而未生成
        # 并补充 args 定义以便解析参数
        manual_map = {
            bytes.fromhex("02da8aeb4fc91966"): {
                "name": "flashBorrowReserveLiquidity",
                "args": [{"name": "liquidity_amount", "type": "u64"}]
            },
            bytes.fromhex("218493e497c04859"): {
                "name": "flashRepayReserveLiquidity",
                "args": [{"name": "liquidity_amount", "type": "u64"}, {"name": "borrow_instruction_index", "type": "u8"}]
            },
            bytes.fromhex("8c90fd150a4af803"): {
                "name": "liquidateObligationAndRedeemReserveCollateral",
                "args": [{"name": "liquidity_amount", "type": "u64"}]
            },
            bytes.fromhex("b1479abce2854a37"): {
                "name": "refreshObligation",
                "args": []
            },
        }

        for disc, instr in manual_map.items():
            if disc not in decoder_map:
                decoder_map[disc] = instr
                print(
                    f"  Added manual mapping: {disc.hex()} -> {instr['name']}")

        # Debug: 打印 IDL 中的指令名称
        print("IDL Instructions (first 10):")
        for instr in idl.get('instructions', [])[:10]:
            print(f" - {instr['name']}")

        # 强制修正：根据实际 discriminator 反推
        # 02da8aeb4fc91966 -> flash_borrow_reserve_liquidity
        # 218493e497c04859 -> flash_repay_reserve_liquidity
        # 8c90fd150a4af803 -> liquidate_obligation_and_redeem_reserve_collateral
        # b1479abce2854a37 -> refresh_obligation

        # 检查 IDL 中是否有这些指令
        target_instrs = [
            "flash_borrow_reserve_liquidity",
            "flash_repay_reserve_liquidity",
            "liquidate_obligation_and_redeem_reserve_collateral",
            "refresh_obligation"
        ]

        print("Checking target instructions in IDL:")
        for target in target_instrs:
            found = False
            for instr in idl.get('instructions', []):
                if instr['name'] == target:
                    found = True
                    disc = get_function_discriminator(target)
                    print(
                        f"  Found '{target}': calculated discriminator = {disc.hex()}")
                    break
            if not found:
                print(f"  Warning: '{target}' not found in IDL")

    except Exception as e:
        print(f"Error loading IDL: {e}")
        import traceback
        traceback.print_exc()
        return

    # 2. 获取交易数据
    print(f"\nFetching transaction: {TX_SIGNATURE}...")
    tx_data = get_transaction(TX_SIGNATURE)

    if not tx_data:
        print("Failed to fetch transaction data.")
        return

    # 3. 解析并打印
    print_transaction_analysis(tx_data, decoder_map, KAMINO_PROGRAM_ID)


if __name__ == "__main__":
    main()
