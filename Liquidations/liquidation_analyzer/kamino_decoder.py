#!/usr/bin/env python3
"""
Kamino Lending Protocol 独立解析器
基于 IDL 完全解析 Kamino 协议的所有指令

功能：
1. 解析所有 Kamino 指令（借贷、清算、闪电贷等）
2. 解码指令参数（u64, u128, pubkey, bytes 等）
3. 映射账户名称
4. 支持批量交易解析
5. 提取清算/借贷关键信息

Program ID: KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD
"""

import json
import hashlib
import struct
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum


# ============================================================================
# Base58 编解码 (内置实现，无需外部依赖)
# ============================================================================

BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
BASE58_ALPHABET_MAP = {char: i for i, char in enumerate(BASE58_ALPHABET)}


def base58_decode(s: str) -> bytes:
    """Base58 解码"""
    num = 0
    for char in s:
        if char not in BASE58_ALPHABET_MAP:
            raise ValueError(f"Invalid Base58 character: {char}")
        num = num * 58 + BASE58_ALPHABET_MAP[char]

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


def base58_encode(data: bytes) -> str:
    """Base58 编码"""
    # 计算前导零字节
    leading_zeros = len(data) - len(data.lstrip(b'\x00'))

    # 转换为整数
    num = int.from_bytes(data, 'big')

    if num == 0:
        return '1' * leading_zeros

    encoded = []
    while num > 0:
        num, remainder = divmod(num, 58)
        encoded.append(BASE58_ALPHABET[remainder])

    return '1' * leading_zeros + ''.join(reversed(encoded))


# ============================================================================
# 常量定义
# ============================================================================

KAMINO_LENDING_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"

# 清算相关指令名称
LIQUIDATION_INSTRUCTIONS = {
    'liquidateObligationAndRedeemReserveCollateral',
    'liquidateObligationAndRedeemReserveCollateralV2',
}

# 借贷相关指令名称
BORROW_INSTRUCTIONS = {
    'borrowObligationLiquidity',
    'borrowObligationLiquidityV2',
}

# 还款相关指令名称
REPAY_INSTRUCTIONS = {
    'repayObligationLiquidity',
    'repayObligationLiquidityV2',
}

# 存款相关指令名称
DEPOSIT_INSTRUCTIONS = {
    'depositReserveLiquidity',
    'depositObligationCollateral',
    'depositObligationCollateralV2',
    'depositReserveLiquidityAndObligationCollateral',
    'depositReserveLiquidityAndObligationCollateralV2',
}

# 取款相关指令名称
WITHDRAW_INSTRUCTIONS = {
    'redeemReserveCollateral',
    'withdrawObligationCollateral',
    'withdrawObligationCollateralV2',
    'withdrawObligationCollateralAndRedeemReserveCollateral',
    'withdrawObligationCollateralAndRedeemReserveCollateralV2',
}

# 闪电贷相关指令名称
FLASH_LOAN_INSTRUCTIONS = {
    'flashBorrowReserveLiquidity',
    'flashRepayReserveLiquidity',
}


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class ParsedInstruction:
    """解析后的指令"""
    name: str
    discriminator: str
    args: Dict[str, Any]
    accounts: List[Dict[str, Any]]
    raw_data_hex: str
    instruction_type: str  # liquidation, borrow, repay, deposit, withdraw, flash_loan, other


@dataclass
class ParsedTransaction:
    """解析后的交易"""
    signature: str
    slot: int
    timestamp: int
    datetime_str: str
    helius_type: str  # Helius API 返回的类型
    instructions: List[ParsedInstruction]
    kamino_instructions: List[ParsedInstruction]
    is_liquidation: bool
    is_flash_loan: bool
    total_instructions: int
    kamino_instruction_count: int
    unknown_instruction_count: int


# ============================================================================
# 核心解析类
# ============================================================================

class KaminoDecoder:
    """Kamino Lending 协议解码器"""

    def __init__(self, idl_path: Optional[str] = None):
        """
        初始化解码器

        Args:
            idl_path: IDL 文件路径，如果为 None 则使用默认路径
        """
        self.idl = None
        self.instruction_map: Dict[bytes, Dict[str, Any]] = {}
        self.instruction_map_hex: Dict[str, Dict[str, Any]] = {}
        self.types_map: Dict[str, Any] = {}

        # 加载 IDL
        if idl_path:
            self.load_idl(idl_path)

    def load_idl(self, idl_path: str) -> None:
        """加载 IDL 文件"""
        with open(idl_path, 'r', encoding='utf-8') as f:
            self.idl = json.load(f)

        # 构建类型映射
        self._build_types_map()

        # 构建指令映射
        self._build_instruction_map()

        print(f"✓ 已加载 IDL: {self.idl.get('name')} v{self.idl.get('version')}")
        print(f"✓ 指令数量: {len(self.instruction_map)}")
        print(f"✓ 类型数量: {len(self.types_map)}")

    def _build_types_map(self) -> None:
        """构建类型映射"""
        for type_def in self.idl.get('types', []):
            self.types_map[type_def['name']] = type_def['type']

    @staticmethod
    def _camel_to_snake(name: str) -> str:
        """将 camelCase 转换为 snake_case"""
        import re
        # 在大写字母前插入下划线，然后转小写
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    def _build_instruction_map(self) -> None:
        """构建指令 discriminator 映射（同时支持 camelCase 和 snake_case）"""
        for instruction in self.idl.get('instructions', []):
            name = instruction['name']

            # 展开嵌套账户
            flat_accounts = self._flatten_accounts(
                instruction.get('accounts', []))

            # 创建指令信息（使用原始 camelCase 名称）
            instruction_info_base = {
                'name': name,
                'args': instruction.get('args', []),
                'accounts': flat_accounts,
                'original_accounts': instruction.get('accounts', []),
            }

            # 计算 camelCase discriminator
            discriminator_camel = self._compute_discriminator(name)
            instruction_info = instruction_info_base.copy()
            instruction_info['discriminator'] = discriminator_camel
            instruction_info['discriminator_hex'] = discriminator_camel.hex()

            self.instruction_map[discriminator_camel] = instruction_info
            self.instruction_map_hex[discriminator_camel.hex(
            )] = instruction_info

            # 也计算 snake_case discriminator（如果不同）
            snake_name = self._camel_to_snake(name)
            if snake_name != name:
                discriminator_snake = self._compute_discriminator(snake_name)
                if discriminator_snake != discriminator_camel:
                    instruction_info_snake = instruction_info_base.copy()
                    instruction_info_snake['discriminator'] = discriminator_snake
                    instruction_info_snake['discriminator_hex'] = discriminator_snake.hex(
                    )
                    instruction_info_snake['original_name'] = name
                    instruction_info_snake['snake_case_name'] = snake_name

                    self.instruction_map[discriminator_snake] = instruction_info_snake
                    self.instruction_map_hex[discriminator_snake.hex(
                    )] = instruction_info_snake

    def _flatten_accounts(self, accounts: List[Dict], prefix: str = '') -> List[Dict]:
        """
        展开嵌套的账户结构
        Anchor IDL 中的账户可能是嵌套的（如 depositAccounts, farmsAccounts）
        """
        flat_accounts = []

        for acc in accounts:
            if 'accounts' in acc:
                # 嵌套账户组
                nested_prefix = f"{prefix}{acc['name']}." if prefix else f"{acc['name']}."
                flat_accounts.extend(self._flatten_accounts(
                    acc['accounts'], nested_prefix))
            else:
                # 普通账户
                acc_copy = acc.copy()
                acc_copy['full_name'] = f"{prefix}{acc['name']}" if prefix else acc['name']
                flat_accounts.append(acc_copy)

        return flat_accounts

    @staticmethod
    def _compute_discriminator(instruction_name: str) -> bytes:
        """
        计算 Anchor 指令的 discriminator
        discriminator = sha256("global:{instruction_name}")[:8]
        """
        preimage = f"global:{instruction_name}".encode()
        return hashlib.sha256(preimage).digest()[:8]

    def get_instruction_type(self, instruction_name: str) -> str:
        """获取指令类型分类"""
        if instruction_name in LIQUIDATION_INSTRUCTIONS:
            return 'liquidation'
        elif instruction_name in BORROW_INSTRUCTIONS:
            return 'borrow'
        elif instruction_name in REPAY_INSTRUCTIONS:
            return 'repay'
        elif instruction_name in DEPOSIT_INSTRUCTIONS:
            return 'deposit'
        elif instruction_name in WITHDRAW_INSTRUCTIONS:
            return 'withdraw'
        elif instruction_name in FLASH_LOAN_INSTRUCTIONS:
            return 'flash_loan'
        else:
            return 'other'

    def decode_instruction_data(
        self,
        data: Union[str, bytes],
        accounts: Optional[List[str]] = None
    ) -> Optional[ParsedInstruction]:
        """
        解码指令数据

        Args:
            data: Base58 编码的数据或原始字节
            accounts: 账户地址列表

        Returns:
            ParsedInstruction 或 None
        """
        try:
            # 转换为字节
            if isinstance(data, str):
                data_bytes = base58_decode(data)
            else:
                data_bytes = data

            if len(data_bytes) < 8:
                return None

            # 提取 discriminator
            discriminator = data_bytes[:8]

            if discriminator not in self.instruction_map:
                return None

            instruction_info = self.instruction_map[discriminator]

            # 解析参数
            params_data = data_bytes[8:]
            parsed_args = self._parse_args(
                params_data, instruction_info['args'])

            # 映射账户
            mapped_accounts = []
            if accounts:
                for i, acc_info in enumerate(instruction_info['accounts']):
                    if i < len(accounts):
                        mapped_accounts.append({
                            'name': acc_info.get('full_name', acc_info['name']),
                            'address': accounts[i],
                            'is_mutable': acc_info.get('isMut', False),
                            'is_signer': acc_info.get('isSigner', False),
                            'is_optional': acc_info.get('isOptional', False),
                        })

            instruction_name = instruction_info['name']

            return ParsedInstruction(
                name=instruction_name,
                discriminator=discriminator.hex(),
                args=parsed_args,
                accounts=mapped_accounts,
                raw_data_hex=data_bytes.hex(),
                instruction_type=self.get_instruction_type(instruction_name),
            )

        except Exception as e:
            print(f"解码错误: {e}")
            return None

    def _parse_args(self, data: bytes, args_spec: List[Dict]) -> Dict[str, Any]:
        """
        解析指令参数

        Args:
            data: 参数数据字节
            args_spec: 参数规范

        Returns:
            解析后的参数字典
        """
        parsed = {}
        offset = 0

        for arg in args_spec:
            arg_name = arg['name']
            arg_type = arg['type']

            try:
                value, consumed = self._parse_type(data[offset:], arg_type)
                parsed[arg_name] = value
                offset += consumed
            except Exception as e:
                parsed[arg_name] = f"<解析错误: {e}>"
                break

        return parsed

    def _parse_type(self, data: bytes, type_spec: Any) -> Tuple[Any, int]:
        """
        解析单个类型

        Args:
            data: 数据字节
            type_spec: 类型规范

        Returns:
            (解析值, 消耗字节数)
        """
        if isinstance(type_spec, str):
            # 基本类型
            return self._parse_primitive(data, type_spec)

        elif isinstance(type_spec, dict):
            if 'array' in type_spec:
                # 数组类型 [element_type, size]
                elem_type, size = type_spec['array']
                return self._parse_fixed_array(data, elem_type, size)

            elif 'vec' in type_spec:
                # 动态数组
                elem_type = type_spec['vec']
                return self._parse_vec(data, elem_type)

            elif 'option' in type_spec:
                # 可选类型
                inner_type = type_spec['option']
                return self._parse_option(data, inner_type)

            elif 'defined' in type_spec:
                # 自定义类型
                type_name = type_spec['defined']
                if type_name in self.types_map:
                    return self._parse_defined_type(data, self.types_map[type_name])
                else:
                    return f"<未知类型: {type_name}>", 0

        return f"<无法解析: {type_spec}>", 0

    def _parse_primitive(self, data: bytes, type_name: str) -> Tuple[Any, int]:
        """解析基本类型"""
        if type_name == 'u8':
            if len(data) < 1:
                raise ValueError("数据不足")
            return data[0], 1

        elif type_name == 'u16':
            if len(data) < 2:
                raise ValueError("数据不足")
            return struct.unpack('<H', data[:2])[0], 2

        elif type_name == 'u32':
            if len(data) < 4:
                raise ValueError("数据不足")
            return struct.unpack('<I', data[:4])[0], 4

        elif type_name == 'u64':
            if len(data) < 8:
                raise ValueError("数据不足")
            return struct.unpack('<Q', data[:8])[0], 8

        elif type_name == 'u128':
            if len(data) < 16:
                raise ValueError("数据不足")
            low = struct.unpack('<Q', data[:8])[0]
            high = struct.unpack('<Q', data[8:16])[0]
            return (high << 64) | low, 16

        elif type_name == 'i8':
            if len(data) < 1:
                raise ValueError("数据不足")
            return struct.unpack('<b', data[:1])[0], 1

        elif type_name == 'i16':
            if len(data) < 2:
                raise ValueError("数据不足")
            return struct.unpack('<h', data[:2])[0], 2

        elif type_name == 'i32':
            if len(data) < 4:
                raise ValueError("数据不足")
            return struct.unpack('<i', data[:4])[0], 4

        elif type_name == 'i64':
            if len(data) < 8:
                raise ValueError("数据不足")
            return struct.unpack('<q', data[:8])[0], 8

        elif type_name == 'i128':
            if len(data) < 16:
                raise ValueError("数据不足")
            # 处理有符号 128 位整数
            low = struct.unpack('<Q', data[:8])[0]
            high = struct.unpack('<q', data[8:16])[0]
            return (high << 64) | low, 16

        elif type_name == 'bool':
            if len(data) < 1:
                raise ValueError("数据不足")
            return data[0] != 0, 1

        elif type_name == 'publicKey':
            if len(data) < 32:
                raise ValueError("数据不足")
            return base58_encode(data[:32]), 32

        elif type_name == 'string':
            # Borsh 字符串格式: 4字节长度 + 内容
            if len(data) < 4:
                raise ValueError("数据不足")
            length = struct.unpack('<I', data[:4])[0]
            if len(data) < 4 + length:
                raise ValueError("字符串数据不足")
            return data[4:4+length].decode('utf-8'), 4 + length

        elif type_name == 'bytes':
            # 动态字节数组
            if len(data) < 4:
                raise ValueError("数据不足")
            length = struct.unpack('<I', data[:4])[0]
            if len(data) < 4 + length:
                raise ValueError("字节数据不足")
            return data[4:4+length].hex(), 4 + length

        else:
            return f"<未知基本类型: {type_name}>", 0

    def _parse_fixed_array(self, data: bytes, elem_type: Any, size: int) -> Tuple[Any, int]:
        """解析固定大小数组"""
        if elem_type == 'u8':
            # 优化：直接处理 u8 数组
            if len(data) < size:
                raise ValueError(f"数据不足: 需要 {size} 字节")
            return data[:size].hex(), size

        result = []
        offset = 0

        for _ in range(size):
            value, consumed = self._parse_type(data[offset:], elem_type)
            result.append(value)
            offset += consumed

        return result, offset

    def _parse_vec(self, data: bytes, elem_type: Any) -> Tuple[Any, int]:
        """解析动态数组 (Vec)"""
        if len(data) < 4:
            raise ValueError("数据不足")

        length = struct.unpack('<I', data[:4])[0]
        offset = 4
        result = []

        for _ in range(length):
            value, consumed = self._parse_type(data[offset:], elem_type)
            result.append(value)
            offset += consumed

        return result, offset

    def _parse_option(self, data: bytes, inner_type: Any) -> Tuple[Any, int]:
        """解析 Option 类型"""
        if len(data) < 1:
            raise ValueError("数据不足")

        is_some = data[0] != 0

        if not is_some:
            return None, 1

        value, consumed = self._parse_type(data[1:], inner_type)
        return value, 1 + consumed

    def _parse_defined_type(self, data: bytes, type_def: Dict) -> Tuple[Any, int]:
        """解析自定义类型"""
        kind = type_def.get('kind')

        if kind == 'struct':
            return self._parse_struct(data, type_def.get('fields', []))
        elif kind == 'enum':
            return self._parse_enum(data, type_def.get('variants', []))

        return f"<未知类型定义: {kind}>", 0

    def _parse_struct(self, data: bytes, fields: List[Dict]) -> Tuple[Dict, int]:
        """解析结构体"""
        result = {}
        offset = 0

        for field in fields:
            field_name = field['name']
            field_type = field['type']

            value, consumed = self._parse_type(data[offset:], field_type)
            result[field_name] = value
            offset += consumed

        return result, offset

    def _parse_enum(self, data: bytes, variants: List[Dict]) -> Tuple[Any, int]:
        """解析枚举"""
        if len(data) < 1:
            raise ValueError("数据不足")

        variant_index = data[0]

        if variant_index >= len(variants):
            return f"<无效枚举索引: {variant_index}>", 1

        variant = variants[variant_index]
        variant_name = variant['name']

        # 检查是否有关联数据
        if 'fields' in variant:
            value, consumed = self._parse_struct(data[1:], variant['fields'])
            return {variant_name: value}, 1 + consumed

        return variant_name, 1

    def decode_instruction_data_with_unknown(
        self,
        data: Union[str, bytes],
        accounts: Optional[List[str]] = None
    ) -> Optional[ParsedInstruction]:
        """
        解码指令数据，包括未知指令

        Args:
            data: Base58 编码的数据或原始字节
            accounts: 账户地址列表

        Returns:
            ParsedInstruction 或 None
        """
        try:
            # 转换为字节
            if isinstance(data, str):
                data_bytes = base58_decode(data)
            else:
                data_bytes = data

            if len(data_bytes) < 8:
                return ParsedInstruction(
                    name='unknown_short_data',
                    discriminator=data_bytes.hex() if data_bytes else '',
                    args={'raw_data': data_bytes.hex()},
                    accounts=[],
                    raw_data_hex=data_bytes.hex(),
                    instruction_type='unknown',
                )

            # 提取 discriminator
            discriminator = data_bytes[:8]

            if discriminator in self.instruction_map:
                return self.decode_instruction_data(data, accounts)

            # 未知指令 - 仍然返回基本信息
            mapped_accounts = []
            if accounts:
                for i, addr in enumerate(accounts):
                    mapped_accounts.append({
                        'name': f'account_{i}',
                        'address': addr,
                        'is_mutable': False,
                        'is_signer': False,
                        'is_optional': False,
                    })

            return ParsedInstruction(
                name='unknown_instruction',
                discriminator=discriminator.hex(),
                args={'raw_params': data_bytes[8:].hex() if len(
                    data_bytes) > 8 else ''},
                accounts=mapped_accounts,
                raw_data_hex=data_bytes.hex(),
                instruction_type='unknown',
            )

        except Exception as e:
            return None

    def decode_transaction(
        self,
        tx: Dict[str, Any]
    ) -> ParsedTransaction:
        """
        解析完整交易

        Args:
            tx: 交易数据（包含 signature, slot, timestamp, instructions 等）

        Returns:
            ParsedTransaction
        """
        signature = tx.get('signature', '')
        slot = tx.get('slot', 0)
        timestamp = tx.get('timestamp', 0) or tx.get('blockTime', 0)

        # Helius API 返回的 type 字段
        helius_type = tx.get('type', '')

        # 转换时间戳
        if timestamp:
            dt_str = datetime.fromtimestamp(timestamp).isoformat()
        else:
            dt_str = ''

        instructions = tx.get('instructions', [])

        parsed_instructions = []
        kamino_instructions = []
        unknown_instructions = []

        for instr in instructions:
            program_id = instr.get('programId', '')

            if program_id == KAMINO_LENDING_PROGRAM_ID:
                # 解析 Kamino 指令
                data = instr.get('data', '')
                accounts = instr.get('accounts', [])

                # 先尝试正常解码
                parsed = self.decode_instruction_data(data, accounts)

                if parsed:
                    parsed_instructions.append(parsed)
                    kamino_instructions.append(parsed)
                else:
                    # 尝试获取未知指令信息
                    parsed = self.decode_instruction_data_with_unknown(
                        data, accounts)
                    if parsed:
                        parsed_instructions.append(parsed)
                        unknown_instructions.append(parsed)

        # 判断是否为清算交易（通过 IDL 或 Helius type）
        is_liquidation = (
            any(instr.instruction_type == 'liquidation' for instr in kamino_instructions) or
            'LIQUIDAT' in helius_type.upper()
        )

        # 判断是否为闪电贷
        is_flash_loan = (
            any(instr.instruction_type == 'flash_loan' for instr in kamino_instructions) or
            'FLASH' in helius_type.upper()
        )

        return ParsedTransaction(
            signature=signature,
            slot=slot,
            timestamp=timestamp,
            datetime_str=dt_str,
            helius_type=helius_type,
            instructions=[asdict(i) for i in parsed_instructions],
            kamino_instructions=[asdict(i) for i in kamino_instructions],
            is_liquidation=is_liquidation,
            is_flash_loan=is_flash_loan,
            total_instructions=len(instructions),
            kamino_instruction_count=len(
                kamino_instructions) + len(unknown_instructions),
            unknown_instruction_count=len(unknown_instructions),
        )

    def list_all_instructions(self) -> List[Dict[str, Any]]:
        """列出所有指令及其 discriminator"""
        result = []

        for disc_bytes, info in self.instruction_map.items():
            result.append({
                'name': info['name'],
                'discriminator_hex': info['discriminator_hex'],
                'arg_count': len(info['args']),
                'account_count': len(info['accounts']),
                'type': self.get_instruction_type(info['name']),
            })

        return sorted(result, key=lambda x: x['name'])

    def list_liquidation_instructions(self) -> List[Dict[str, Any]]:
        """列出所有清算相关指令"""
        return [
            instr for instr in self.list_all_instructions()
            if instr['type'] == 'liquidation'
        ]


# ============================================================================
# 批量处理工具
# ============================================================================

class KaminoBatchProcessor:
    """Kamino 批量交易处理器"""

    def __init__(self, decoder: KaminoDecoder):
        self.decoder = decoder
        self.stats = {
            'total_transactions': 0,
            'kamino_transactions': 0,
            'liquidations': 0,
            'flash_loans': 0,
            'borrows': 0,
            'repays': 0,
            'deposits': 0,
            'withdrawals': 0,
            'instruction_counts': {},
            'helius_type_counts': {},
            'unknown_discriminators': {},
            'parsed_instructions': 0,
            'unknown_instructions': 0,
        }

    def process_file(self, file_path: str) -> List[ParsedTransaction]:
        """处理单个 JSON 文件"""
        # 尝试不同的编码
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except UnicodeDecodeError:
            with open(file_path, 'rb') as f:
                data = json.load(f)

        transactions = data.get('transactions', [])
        if not transactions and isinstance(data, list):
            transactions = data

        results = []

        for tx in transactions:
            self.stats['total_transactions'] += 1

            # 统计 Helius 类型
            helius_type = tx.get('type', 'UNKNOWN')
            self.stats['helius_type_counts'][helius_type] = \
                self.stats['helius_type_counts'].get(helius_type, 0) + 1

            parsed = self.decoder.decode_transaction(tx)

            if parsed.kamino_instruction_count > 0:
                self.stats['kamino_transactions'] += 1

                if parsed.is_liquidation:
                    self.stats['liquidations'] += 1
                if parsed.is_flash_loan:
                    self.stats['flash_loans'] += 1

                # 统计解析和未知指令
                self.stats['parsed_instructions'] += parsed.kamino_instruction_count - \
                    parsed.unknown_instruction_count
                self.stats['unknown_instructions'] += parsed.unknown_instruction_count

                # 统计指令类型
                for instr in parsed.kamino_instructions:
                    instr_name = instr['name']
                    self.stats['instruction_counts'][instr_name] = \
                        self.stats['instruction_counts'].get(instr_name, 0) + 1

                    instr_type = instr['instruction_type']
                    if instr_type == 'borrow':
                        self.stats['borrows'] += 1
                    elif instr_type == 'repay':
                        self.stats['repays'] += 1
                    elif instr_type == 'deposit':
                        self.stats['deposits'] += 1
                    elif instr_type == 'withdraw':
                        self.stats['withdrawals'] += 1
                    elif instr_type == 'unknown':
                        # 记录未知 discriminator
                        disc = instr.get('discriminator', '')
                        if disc:
                            self.stats['unknown_discriminators'][disc] = \
                                self.stats['unknown_discriminators'].get(
                                    disc, 0) + 1

                # 统计所有解析的指令
                for instr in parsed.instructions:
                    if instr.get('instruction_type') != 'unknown':
                        instr_name = instr['name']
                        self.stats['instruction_counts'][instr_name] = \
                            self.stats['instruction_counts'].get(
                                instr_name, 0) + 1

                results.append(parsed)

        return results

    def process_directory(self, dir_path: str, pattern: str = "*.json") -> List[ParsedTransaction]:
        """处理目录中的所有 JSON 文件"""
        folder = Path(dir_path)
        json_files = sorted(folder.glob(pattern))

        all_results = []

        for i, json_file in enumerate(json_files, 1):
            print(f"[{i}/{len(json_files)}] 处理 {json_file.name}...")

            try:
                results = self.process_file(str(json_file))
                all_results.extend(results)
            except Exception as e:
                print(f"  错误: {e}")

        return all_results

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self.stats

    def print_stats(self) -> None:
        """打印统计信息"""
        print("\n" + "=" * 60)
        print("Kamino 交易统计")
        print("=" * 60)

        print(f"\n总体统计:")
        print(f"  总交易数: {self.stats['total_transactions']:,}")
        print(f"  Kamino 交易: {self.stats['kamino_transactions']:,}")
        print(f"  清算交易: {self.stats['liquidations']:,}")
        print(f"  闪电贷交易: {self.stats['flash_loans']:,}")

        print(f"\n操作统计:")
        print(f"  借款: {self.stats['borrows']:,}")
        print(f"  还款: {self.stats['repays']:,}")
        print(f"  存款: {self.stats['deposits']:,}")
        print(f"  取款: {self.stats['withdrawals']:,}")

        print(f"\n指令解析统计:")
        print(f"  已识别指令: {self.stats['parsed_instructions']:,}")
        print(f"  未识别指令: {self.stats['unknown_instructions']:,}")

        print(f"\nHelius 类型分布 (前15):")
        sorted_types = sorted(
            self.stats['helius_type_counts'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for t, count in sorted_types[:15]:
            print(f"  {t}: {count:,}")

        print(f"\n已识别指令分布 (前15):")
        sorted_counts = sorted(
            self.stats['instruction_counts'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for name, count in sorted_counts[:15]:
            print(f"  {name}: {count:,}")

        if self.stats['unknown_discriminators']:
            print(f"\n未识别的 Discriminators (前10):")
            sorted_unknown = sorted(
                self.stats['unknown_discriminators'].items(),
                key=lambda x: x[1],
                reverse=True
            )
            for disc, count in sorted_unknown[:10]:
                print(f"  {disc}: {count:,}")


# ============================================================================
# 主函数和命令行接口
# ============================================================================

def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='Kamino Lending 协议解析器')
    parser.add_argument(
        '--idl',
        default='/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_lending_idl.json',
        help='IDL 文件路径'
    )
    parser.add_argument(
        '--mode',
        choices=['info', 'decode', 'batch'],
        default='info',
        help='运行模式: info=显示信息, decode=解码数据, batch=批量处理'
    )
    parser.add_argument(
        '--data',
        help='要解码的 Base58 数据 (decode 模式)'
    )
    parser.add_argument(
        '--file',
        help='要处理的 JSON 文件 (batch 模式)'
    )
    parser.add_argument(
        '--dir',
        help='要处理的目录 (batch 模式)'
    )
    parser.add_argument(
        '--output',
        help='输出文件路径'
    )
    parser.add_argument(
        '--pattern',
        default='*.json',
        help='文件匹配模式 (batch 模式)'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Kamino Lending 协议解析器")
    print("=" * 60)

    # 初始化解码器
    decoder = KaminoDecoder(args.idl)

    if args.mode == 'info':
        # 显示所有指令信息
        print("\n所有指令列表:")
        print("-" * 60)

        instructions = decoder.list_all_instructions()

        for instr in instructions:
            type_marker = f"[{instr['type']}]" if instr['type'] != 'other' else ''
            print(f"  {instr['name']:<50} {type_marker}")
            print(f"    Discriminator: {instr['discriminator_hex']}")
            print(
                f"    参数数量: {instr['arg_count']}, 账户数量: {instr['account_count']}")

        print(f"\n共 {len(instructions)} 个指令")

        print("\n\n清算相关指令:")
        print("-" * 60)
        for instr in decoder.list_liquidation_instructions():
            print(f"  {instr['name']}")
            print(f"    Discriminator: {instr['discriminator_hex']}")

    elif args.mode == 'decode':
        if not args.data:
            print("错误: decode 模式需要 --data 参数")
            return

        print(f"\n解码数据: {args.data}")
        print("-" * 60)

        result = decoder.decode_instruction_data(args.data)

        if result:
            print(f"✓ 指令名称: {result.name}")
            print(f"  类型: {result.instruction_type}")
            print(f"  Discriminator: {result.discriminator}")

            if result.args:
                print(f"\n  参数:")
                for name, value in result.args.items():
                    print(f"    {name}: {value}")
        else:
            print("❌ 无法解码指令")

    elif args.mode == 'batch':
        processor = KaminoBatchProcessor(decoder)

        if args.dir:
            print(f"\n处理目录: {args.dir}")
            results = processor.process_directory(args.dir, args.pattern)
        elif args.file:
            print(f"\n处理文件: {args.file}")
            results = processor.process_file(args.file)
        else:
            print("错误: batch 模式需要 --file 或 --dir 参数")
            return

        # 打印统计
        processor.print_stats()

        # 保存结果
        if args.output:
            print(f"\n保存结果到: {args.output}")
            with open(args.output, 'w', encoding='utf-8') as f:
                for tx in results:
                    f.write(json.dumps(asdict(tx), ensure_ascii=False) + '\n')
            print(f"✓ 已保存 {len(results)} 条记录")


if __name__ == "__main__":
    main()
