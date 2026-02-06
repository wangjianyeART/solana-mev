#!/usr/bin/env python3
"""
Kamino Lending Protocol Standalone Parser
Fully parses all Kamino protocol instructions based on IDL

Features:
1. Parse all Kamino instructions (lending, liquidation, flash loan, etc.)
2. Decode instruction parameters (u64, u128, pubkey, bytes, etc.)
3. Map account names
4. Support batch transaction parsing
5. Extract liquidation/lending key information

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
# Base58 Encoding/Decoding (built-in implementation, no external dependencies)
# ============================================================================

BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
BASE58_ALPHABET_MAP = {char: i for i, char in enumerate(BASE58_ALPHABET)}


def base58_decode(s: str) -> bytes:
    """Base58 decode"""
    num = 0
    for char in s:
        if char not in BASE58_ALPHABET_MAP:
            raise ValueError(f"Invalid Base58 character: {char}")
        num = num * 58 + BASE58_ALPHABET_MAP[char]

    # Handle leading zeros
    leading_zeros = len(s) - len(s.lstrip('1'))

    # Convert to bytes
    if num == 0:
        return b'\x00' * leading_zeros

    encoded = []
    while num > 0:
        num, remainder = divmod(num, 256)
        encoded.insert(0, remainder)

    return b'\x00' * leading_zeros + bytes(encoded)


def base58_encode(data: bytes) -> str:
    """Base58 encode"""
    # Count leading zero bytes
    leading_zeros = len(data) - len(data.lstrip(b'\x00'))

    # Convert to integer
    num = int.from_bytes(data, 'big')

    if num == 0:
        return '1' * leading_zeros

    encoded = []
    while num > 0:
        num, remainder = divmod(num, 58)
        encoded.append(BASE58_ALPHABET[remainder])

    return '1' * leading_zeros + ''.join(reversed(encoded))


# ============================================================================
# Constants
# ============================================================================

KAMINO_LENDING_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"

# Liquidation-related instruction names
LIQUIDATION_INSTRUCTIONS = {
    'liquidateObligationAndRedeemReserveCollateral',
    'liquidateObligationAndRedeemReserveCollateralV2',
}

# Borrow-related instruction names
BORROW_INSTRUCTIONS = {
    'borrowObligationLiquidity',
    'borrowObligationLiquidityV2',
}

# Repay-related instruction names
REPAY_INSTRUCTIONS = {
    'repayObligationLiquidity',
    'repayObligationLiquidityV2',
}

# Deposit-related instruction names
DEPOSIT_INSTRUCTIONS = {
    'depositReserveLiquidity',
    'depositObligationCollateral',
    'depositObligationCollateralV2',
    'depositReserveLiquidityAndObligationCollateral',
    'depositReserveLiquidityAndObligationCollateralV2',
}

# Withdraw-related instruction names
WITHDRAW_INSTRUCTIONS = {
    'redeemReserveCollateral',
    'withdrawObligationCollateral',
    'withdrawObligationCollateralV2',
    'withdrawObligationCollateralAndRedeemReserveCollateral',
    'withdrawObligationCollateralAndRedeemReserveCollateralV2',
}

# Flash loan-related instruction names
FLASH_LOAN_INSTRUCTIONS = {
    'flashBorrowReserveLiquidity',
    'flashRepayReserveLiquidity',
}


# ============================================================================
# Dataclass Definitions
# ============================================================================

@dataclass
class ParsedInstruction:
    """Parsed instruction"""
    name: str
    discriminator: str
    args: Dict[str, Any]
    accounts: List[Dict[str, Any]]
    raw_data_hex: str
    instruction_type: str  # liquidation, borrow, repay, deposit, withdraw, flash_loan, other


@dataclass
class ParsedTransaction:
    """Parsed transaction"""
    signature: str
    slot: int
    timestamp: int
    datetime_str: str
    helius_type: str  # Type returned by Helius API
    instructions: List[ParsedInstruction]
    kamino_instructions: List[ParsedInstruction]
    is_liquidation: bool
    is_flash_loan: bool
    total_instructions: int
    kamino_instruction_count: int
    unknown_instruction_count: int


# ============================================================================
# Core Parser Class
# ============================================================================

class KaminoDecoder:
    """Kamino Lending Protocol Decoder"""

    def __init__(self, idl_path: Optional[str] = None):
        """
        Initialize the decoder

        Args:
            idl_path: IDL file path; uses default path if None
        """
        self.idl = None
        self.instruction_map: Dict[bytes, Dict[str, Any]] = {}
        self.instruction_map_hex: Dict[str, Dict[str, Any]] = {}
        self.types_map: Dict[str, Any] = {}

        # Load IDL
        if idl_path:
            self.load_idl(idl_path)

    def load_idl(self, idl_path: str) -> None:
        """Load IDL file"""
        with open(idl_path, 'r', encoding='utf-8') as f:
            self.idl = json.load(f)

        # Build type mapping
        self._build_types_map()

        # Build instruction mapping
        self._build_instruction_map()

        print(f"Loaded IDL: {self.idl.get('name')} v{self.idl.get('version')}")
        print(f"Instruction count: {len(self.instruction_map)}")
        print(f"Type count: {len(self.types_map)}")

    def _build_types_map(self) -> None:
        """Build type mapping"""
        for type_def in self.idl.get('types', []):
            self.types_map[type_def['name']] = type_def['type']

    @staticmethod
    def _camel_to_snake(name: str) -> str:
        """Convert camelCase to snake_case"""
        import re
        # Insert underscore before uppercase letters, then convert to lowercase
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    def _build_instruction_map(self) -> None:
        """Build instruction discriminator mapping (supports both camelCase and snake_case)"""
        for instruction in self.idl.get('instructions', []):
            name = instruction['name']

            # Flatten nested accounts
            flat_accounts = self._flatten_accounts(
                instruction.get('accounts', []))

            # Create instruction info (using original camelCase name)
            instruction_info_base = {
                'name': name,
                'args': instruction.get('args', []),
                'accounts': flat_accounts,
                'original_accounts': instruction.get('accounts', []),
            }

            # Compute camelCase discriminator
            discriminator_camel = self._compute_discriminator(name)
            instruction_info = instruction_info_base.copy()
            instruction_info['discriminator'] = discriminator_camel
            instruction_info['discriminator_hex'] = discriminator_camel.hex()

            self.instruction_map[discriminator_camel] = instruction_info
            self.instruction_map_hex[discriminator_camel.hex(
            )] = instruction_info

            # Also compute snake_case discriminator (if different)
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
        Flatten nested account structures
        Accounts in Anchor IDL can be nested (e.g., depositAccounts, farmsAccounts)
        """
        flat_accounts = []

        for acc in accounts:
            if 'accounts' in acc:
                # Nested account group
                nested_prefix = f"{prefix}{acc['name']}." if prefix else f"{acc['name']}."
                flat_accounts.extend(self._flatten_accounts(
                    acc['accounts'], nested_prefix))
            else:
                # Regular account
                acc_copy = acc.copy()
                acc_copy['full_name'] = f"{prefix}{acc['name']}" if prefix else acc['name']
                flat_accounts.append(acc_copy)

        return flat_accounts

    @staticmethod
    def _compute_discriminator(instruction_name: str) -> bytes:
        """
        Compute Anchor instruction discriminator
        discriminator = sha256("global:{instruction_name}")[:8]
        """
        preimage = f"global:{instruction_name}".encode()
        return hashlib.sha256(preimage).digest()[:8]

    def get_instruction_type(self, instruction_name: str) -> str:
        """Get instruction type classification"""
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
        Decode instruction data

        Args:
            data: Base58-encoded data or raw bytes
            accounts: List of account addresses

        Returns:
            ParsedInstruction or None
        """
        try:
            # Convert to bytes
            if isinstance(data, str):
                data_bytes = base58_decode(data)
            else:
                data_bytes = data

            if len(data_bytes) < 8:
                return None

            # Extract discriminator
            discriminator = data_bytes[:8]

            if discriminator not in self.instruction_map:
                return None

            instruction_info = self.instruction_map[discriminator]

            # Parse arguments
            params_data = data_bytes[8:]
            parsed_args = self._parse_args(
                params_data, instruction_info['args'])

            # Map accounts
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
            print(f"Decode error: {e}")
            return None

    def _parse_args(self, data: bytes, args_spec: List[Dict]) -> Dict[str, Any]:
        """
        Parse instruction arguments

        Args:
            data: Argument data bytes
            args_spec: Argument specification

        Returns:
            Parsed argument dictionary
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
                parsed[arg_name] = f"<parse error: {e}>"
                break

        return parsed

    def _parse_type(self, data: bytes, type_spec: Any) -> Tuple[Any, int]:
        """
        Parse a single type

        Args:
            data: Data bytes
            type_spec: Type specification

        Returns:
            (parsed value, bytes consumed)
        """
        if isinstance(type_spec, str):
            # Primitive type
            return self._parse_primitive(data, type_spec)

        elif isinstance(type_spec, dict):
            if 'array' in type_spec:
                # Array type [element_type, size]
                elem_type, size = type_spec['array']
                return self._parse_fixed_array(data, elem_type, size)

            elif 'vec' in type_spec:
                # Dynamic array
                elem_type = type_spec['vec']
                return self._parse_vec(data, elem_type)

            elif 'option' in type_spec:
                # Optional type
                inner_type = type_spec['option']
                return self._parse_option(data, inner_type)

            elif 'defined' in type_spec:
                # Custom type
                type_name = type_spec['defined']
                if type_name in self.types_map:
                    return self._parse_defined_type(data, self.types_map[type_name])
                else:
                    return f"<unknown type: {type_name}>", 0

        return f"<cannot parse: {type_spec}>", 0

    def _parse_primitive(self, data: bytes, type_name: str) -> Tuple[Any, int]:
        """Parse primitive type"""
        if type_name == 'u8':
            if len(data) < 1:
                raise ValueError("Insufficient data")
            return data[0], 1

        elif type_name == 'u16':
            if len(data) < 2:
                raise ValueError("Insufficient data")
            return struct.unpack('<H', data[:2])[0], 2

        elif type_name == 'u32':
            if len(data) < 4:
                raise ValueError("Insufficient data")
            return struct.unpack('<I', data[:4])[0], 4

        elif type_name == 'u64':
            if len(data) < 8:
                raise ValueError("Insufficient data")
            return struct.unpack('<Q', data[:8])[0], 8

        elif type_name == 'u128':
            if len(data) < 16:
                raise ValueError("Insufficient data")
            low = struct.unpack('<Q', data[:8])[0]
            high = struct.unpack('<Q', data[8:16])[0]
            return (high << 64) | low, 16

        elif type_name == 'i8':
            if len(data) < 1:
                raise ValueError("Insufficient data")
            return struct.unpack('<b', data[:1])[0], 1

        elif type_name == 'i16':
            if len(data) < 2:
                raise ValueError("Insufficient data")
            return struct.unpack('<h', data[:2])[0], 2

        elif type_name == 'i32':
            if len(data) < 4:
                raise ValueError("Insufficient data")
            return struct.unpack('<i', data[:4])[0], 4

        elif type_name == 'i64':
            if len(data) < 8:
                raise ValueError("Insufficient data")
            return struct.unpack('<q', data[:8])[0], 8

        elif type_name == 'i128':
            if len(data) < 16:
                raise ValueError("Insufficient data")
            # Handle signed 128-bit integer
            low = struct.unpack('<Q', data[:8])[0]
            high = struct.unpack('<q', data[8:16])[0]
            return (high << 64) | low, 16

        elif type_name == 'bool':
            if len(data) < 1:
                raise ValueError("Insufficient data")
            return data[0] != 0, 1

        elif type_name == 'publicKey':
            if len(data) < 32:
                raise ValueError("Insufficient data")
            return base58_encode(data[:32]), 32

        elif type_name == 'string':
            # Borsh string format: 4-byte length + content
            if len(data) < 4:
                raise ValueError("Insufficient data")
            length = struct.unpack('<I', data[:4])[0]
            if len(data) < 4 + length:
                raise ValueError("Insufficient string data")
            return data[4:4+length].decode('utf-8'), 4 + length

        elif type_name == 'bytes':
            # Dynamic byte array
            if len(data) < 4:
                raise ValueError("Insufficient data")
            length = struct.unpack('<I', data[:4])[0]
            if len(data) < 4 + length:
                raise ValueError("Insufficient byte data")
            return data[4:4+length].hex(), 4 + length

        else:
            return f"<unknown primitive type: {type_name}>", 0

    def _parse_fixed_array(self, data: bytes, elem_type: Any, size: int) -> Tuple[Any, int]:
        """Parse fixed-size array"""
        if elem_type == 'u8':
            # Optimization: handle u8 arrays directly
            if len(data) < size:
                raise ValueError(f"Insufficient data: need {size} bytes")
            return data[:size].hex(), size

        result = []
        offset = 0

        for _ in range(size):
            value, consumed = self._parse_type(data[offset:], elem_type)
            result.append(value)
            offset += consumed

        return result, offset

    def _parse_vec(self, data: bytes, elem_type: Any) -> Tuple[Any, int]:
        """Parse dynamic array (Vec)"""
        if len(data) < 4:
            raise ValueError("Insufficient data")

        length = struct.unpack('<I', data[:4])[0]
        offset = 4
        result = []

        for _ in range(length):
            value, consumed = self._parse_type(data[offset:], elem_type)
            result.append(value)
            offset += consumed

        return result, offset

    def _parse_option(self, data: bytes, inner_type: Any) -> Tuple[Any, int]:
        """Parse Option type"""
        if len(data) < 1:
            raise ValueError("Insufficient data")

        is_some = data[0] != 0

        if not is_some:
            return None, 1

        value, consumed = self._parse_type(data[1:], inner_type)
        return value, 1 + consumed

    def _parse_defined_type(self, data: bytes, type_def: Dict) -> Tuple[Any, int]:
        """Parse custom type"""
        kind = type_def.get('kind')

        if kind == 'struct':
            return self._parse_struct(data, type_def.get('fields', []))
        elif kind == 'enum':
            return self._parse_enum(data, type_def.get('variants', []))

        return f"<unknown type definition: {kind}>", 0

    def _parse_struct(self, data: bytes, fields: List[Dict]) -> Tuple[Dict, int]:
        """Parse struct"""
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
        """Parse enum"""
        if len(data) < 1:
            raise ValueError("Insufficient data")

        variant_index = data[0]

        if variant_index >= len(variants):
            return f"<invalid enum index: {variant_index}>", 1

        variant = variants[variant_index]
        variant_name = variant['name']

        # Check if there is associated data
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
        Decode instruction data, including unknown instructions

        Args:
            data: Base58-encoded data or raw bytes
            accounts: List of account addresses

        Returns:
            ParsedInstruction or None
        """
        try:
            # Convert to bytes
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

            # Extract discriminator
            discriminator = data_bytes[:8]

            if discriminator in self.instruction_map:
                return self.decode_instruction_data(data, accounts)

            # Unknown instruction - still return basic info
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
        Parse a complete transaction

        Args:
            tx: Transaction data (containing signature, slot, timestamp, instructions, etc.)

        Returns:
            ParsedTransaction
        """
        signature = tx.get('signature', '')
        slot = tx.get('slot', 0)
        timestamp = tx.get('timestamp', 0) or tx.get('blockTime', 0)

        # Type field returned by Helius API
        helius_type = tx.get('type', '')

        # Convert timestamp
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
                # Parse Kamino instruction
                data = instr.get('data', '')
                accounts = instr.get('accounts', [])

                # Try normal decoding first
                parsed = self.decode_instruction_data(data, accounts)

                if parsed:
                    parsed_instructions.append(parsed)
                    kamino_instructions.append(parsed)
                else:
                    # Try to get unknown instruction info
                    parsed = self.decode_instruction_data_with_unknown(
                        data, accounts)
                    if parsed:
                        parsed_instructions.append(parsed)
                        unknown_instructions.append(parsed)

        # Determine if it's a liquidation transaction (via IDL or Helius type)
        is_liquidation = (
            any(instr.instruction_type == 'liquidation' for instr in kamino_instructions) or
            'LIQUIDAT' in helius_type.upper()
        )

        # Determine if it's a flash loan
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
        """List all instructions and their discriminators"""
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
        """List all liquidation-related instructions"""
        return [
            instr for instr in self.list_all_instructions()
            if instr['type'] == 'liquidation'
        ]


# ============================================================================
# Batch Processing Tools
# ============================================================================

class KaminoBatchProcessor:
    """Kamino Batch Transaction Processor"""

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
        """Process a single JSON file"""
        # Try different encodings
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

            # Count Helius types
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

                # Count parsed and unknown instructions
                self.stats['parsed_instructions'] += parsed.kamino_instruction_count - \
                    parsed.unknown_instruction_count
                self.stats['unknown_instructions'] += parsed.unknown_instruction_count

                # Count instruction types
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
                        # Record unknown discriminator
                        disc = instr.get('discriminator', '')
                        if disc:
                            self.stats['unknown_discriminators'][disc] = \
                                self.stats['unknown_discriminators'].get(
                                    disc, 0) + 1

                # Count all parsed instructions
                for instr in parsed.instructions:
                    if instr.get('instruction_type') != 'unknown':
                        instr_name = instr['name']
                        self.stats['instruction_counts'][instr_name] = \
                            self.stats['instruction_counts'].get(
                                instr_name, 0) + 1

                results.append(parsed)

        return results

    def process_directory(self, dir_path: str, pattern: str = "*.json") -> List[ParsedTransaction]:
        """Process all JSON files in a directory"""
        folder = Path(dir_path)
        json_files = sorted(folder.glob(pattern))

        all_results = []

        for i, json_file in enumerate(json_files, 1):
            print(f"[{i}/{len(json_files)}] Processing {json_file.name}...")

            try:
                results = self.process_file(str(json_file))
                all_results.extend(results)
            except Exception as e:
                print(f"  Error: {e}")

        return all_results

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics"""
        return self.stats

    def print_stats(self) -> None:
        """Print statistics"""
        print("\n" + "=" * 60)
        print("Kamino Transaction Statistics")
        print("=" * 60)

        print(f"\nOverall Statistics:")
        print(f"  Total transactions: {self.stats['total_transactions']:,}")
        print(f"  Kamino transactions: {self.stats['kamino_transactions']:,}")
        print(f"  Liquidation transactions: {self.stats['liquidations']:,}")
        print(f"  Flash loan transactions: {self.stats['flash_loans']:,}")

        print(f"\nOperation Statistics:")
        print(f"  Borrows: {self.stats['borrows']:,}")
        print(f"  Repays: {self.stats['repays']:,}")
        print(f"  Deposits: {self.stats['deposits']:,}")
        print(f"  Withdrawals: {self.stats['withdrawals']:,}")

        print(f"\nInstruction Parsing Statistics:")
        print(f"  Identified instructions: {self.stats['parsed_instructions']:,}")
        print(f"  Unidentified instructions: {self.stats['unknown_instructions']:,}")

        print(f"\nHelius Type Distribution (top 15):")
        sorted_types = sorted(
            self.stats['helius_type_counts'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for t, count in sorted_types[:15]:
            print(f"  {t}: {count:,}")

        print(f"\nIdentified Instruction Distribution (top 15):")
        sorted_counts = sorted(
            self.stats['instruction_counts'].items(),
            key=lambda x: x[1],
            reverse=True
        )
        for name, count in sorted_counts[:15]:
            print(f"  {name}: {count:,}")

        if self.stats['unknown_discriminators']:
            print(f"\nUnidentified Discriminators (top 10):")
            sorted_unknown = sorted(
                self.stats['unknown_discriminators'].items(),
                key=lambda x: x[1],
                reverse=True
            )
            for disc, count in sorted_unknown[:10]:
                print(f"  {disc}: {count:,}")


# ============================================================================
# Main Function and CLI
# ============================================================================

def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description='Kamino Lending Protocol Parser')
    parser.add_argument(
        '--idl',
        default='/Volumes/T7 Shield/solana_rug_research/mev_solana/Liquidations/kamino_lending_idl.json',
        help='IDL file path'
    )
    parser.add_argument(
        '--mode',
        choices=['info', 'decode', 'batch'],
        default='info',
        help='Run mode: info=show info, decode=decode data, batch=batch process'
    )
    parser.add_argument(
        '--data',
        help='Base58 data to decode (decode mode)'
    )
    parser.add_argument(
        '--file',
        help='JSON file to process (batch mode)'
    )
    parser.add_argument(
        '--dir',
        help='Directory to process (batch mode)'
    )
    parser.add_argument(
        '--output',
        help='Output file path'
    )
    parser.add_argument(
        '--pattern',
        default='*.json',
        help='File matching pattern (batch mode)'
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Kamino Lending Protocol Parser")
    print("=" * 60)

    # Initialize decoder
    decoder = KaminoDecoder(args.idl)

    if args.mode == 'info':
        # Show all instruction info
        print("\nAll Instructions:")
        print("-" * 60)

        instructions = decoder.list_all_instructions()

        for instr in instructions:
            type_marker = f"[{instr['type']}]" if instr['type'] != 'other' else ''
            print(f"  {instr['name']:<50} {type_marker}")
            print(f"    Discriminator: {instr['discriminator_hex']}")
            print(
                f"    Arg count: {instr['arg_count']}, Account count: {instr['account_count']}")

        print(f"\nTotal: {len(instructions)} instructions")

        print("\n\nLiquidation-related Instructions:")
        print("-" * 60)
        for instr in decoder.list_liquidation_instructions():
            print(f"  {instr['name']}")
            print(f"    Discriminator: {instr['discriminator_hex']}")

    elif args.mode == 'decode':
        if not args.data:
            print("Error: decode mode requires --data argument")
            return

        print(f"\nDecoding data: {args.data}")
        print("-" * 60)

        result = decoder.decode_instruction_data(args.data)

        if result:
            print(f"Instruction name: {result.name}")
            print(f"  Type: {result.instruction_type}")
            print(f"  Discriminator: {result.discriminator}")

            if result.args:
                print(f"\n  Arguments:")
                for name, value in result.args.items():
                    print(f"    {name}: {value}")
        else:
            print("Unable to decode instruction")

    elif args.mode == 'batch':
        processor = KaminoBatchProcessor(decoder)

        if args.dir:
            print(f"\nProcessing directory: {args.dir}")
            results = processor.process_directory(args.dir, args.pattern)
        elif args.file:
            print(f"\nProcessing file: {args.file}")
            results = processor.process_file(args.file)
        else:
            print("Error: batch mode requires --file or --dir argument")
            return

        # Print statistics
        processor.print_stats()

        # Save results
        if args.output:
            print(f"\nSaving results to: {args.output}")
            with open(args.output, 'w', encoding='utf-8') as f:
                for tx in results:
                    f.write(json.dumps(asdict(tx), ensure_ascii=False) + '\n')
            print(f"Saved {len(results)} records")


if __name__ == "__main__":
    main()
