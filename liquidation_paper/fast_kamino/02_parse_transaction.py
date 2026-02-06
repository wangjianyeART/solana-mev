#!/usr/bin/env python3
"""
步骤 2: 解析 Kamino 清算交易

功能：
- 使用 IDL 解析 Kamino 指令
- 提取清算相关的关键信息
- 解析日志中的清算数据

使用方法：
    python 02_parse_transaction.py <input_json>
    python 02_parse_transaction.py ./data/5gcWCkHxdYKJCH_raw.json
"""

import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

# 导入 Kamino 解码器
from kamino_decoder import KaminoDecoder, KAMINO_LENDING_PROGRAM_ID


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class TokenTransfer:
    """Token 转账记录"""
    mint: str
    symbol: str
    amount: float
    decimals: int
    from_account: str
    to_account: str
    direction: str  # 'in' 或 'out'


@dataclass
class BalanceChange:
    """余额变化"""
    account: str
    pre_balance: float
    post_balance: float
    change: float
    token_mint: Optional[str] = None
    token_symbol: Optional[str] = None


@dataclass
class LiquidationData:
    """清算数据"""
    # 基本信息
    signature: str
    slot: int
    timestamp: int
    datetime_str: str
    success: bool

    # 清算参数
    debt_amount: float          # 代偿债务数量
    debt_token: str             # 债务代币（如 USDC）
    debt_decimals: int

    collateral_amount: float    # 获得抵押品数量
    collateral_token: str       # 抵押品代币（如 SOL）
    collateral_decimals: int

    # 费用
    transaction_fee: float      # 交易费 (SOL)
    priority_fee: float         # 优先费 (SOL)
    protocol_fee: float         # 协议费 (SOL)

    # 账户
    liquidator: str             # 清算人
    obligation_owner: str       # 被清算人

    # 原始数据
    raw_logs: List[str]
    kamino_instructions: List[Dict]


# ============================================================================
# 已知 Token 信息
# ============================================================================

KNOWN_TOKENS = {
    "So11111111111111111111111111111111111111112": {
        "symbol": "SOL",
        "decimals": 9,
        "name": "Wrapped SOL"
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "symbol": "USDC",
        "decimals": 6,
        "name": "USD Coin"
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "symbol": "USDT",
        "decimals": 6,
        "name": "Tether USD"
    },
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {
        "symbol": "mSOL",
        "decimals": 9,
        "name": "Marinade staked SOL"
    },
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": {
        "symbol": "stSOL",
        "decimals": 9,
        "name": "Lido Staked SOL"
    },
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {
        "symbol": "JitoSOL",
        "decimals": 9,
        "name": "Jito Staked SOL"
    },
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": {
        "symbol": "bSOL",
        "decimals": 9,
        "name": "BlazeStake Staked SOL"
    },
}


def get_token_info(mint: str) -> Dict[str, Any]:
    """获取 Token 信息"""
    if mint in KNOWN_TOKENS:
        return KNOWN_TOKENS[mint]
    return {
        "symbol": mint[:8] + "...",
        "decimals": 9,  # 默认假设 9 位
        "name": "Unknown Token"
    }


# ============================================================================
# 解析器
# ============================================================================

class LiquidationParser:
    """清算交易解析器"""

    def __init__(self, idl_path: Optional[str] = None):
        """
        初始化解析器

        Args:
            idl_path: Kamino IDL 文件路径
        """
        if idl_path is None:
            idl_path = str(Path(__file__).parent / "kamino_lending_idl.json")

        self.decoder = KaminoDecoder(idl_path)

    def parse_transaction(self, tx_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析交易数据

        Args:
            tx_data: 从步骤1获取的交易数据

        Returns:
            解析后的交易数据
        """
        result = {
            'signature': tx_data.get('signature', ''),
            'slot': tx_data.get('slot', 0),
            'blockTime': tx_data.get('blockTime', 0),
            'datetime': tx_data.get('datetime', ''),
            'success': tx_data.get('success', False),
            'fee': tx_data.get('fee', 0),

            # Kamino 相关
            'is_kamino_tx': False,
            'is_liquidation': False,
            'kamino_instructions': [],

            # 余额变化
            'sol_balance_changes': [],
            'token_balance_changes': [],

            # 日志解析
            'parsed_logs': {},

            # 清算详情（如果是清算交易）
            'liquidation_details': None
        }

        # 解析 Kamino 指令
        instructions = tx_data.get('instructions', [])
        kamino_instructions = []

        for instr in instructions:
            if instr.get('programId') == KAMINO_LENDING_PROGRAM_ID:
                result['is_kamino_tx'] = True

                # 使用解码器解析
                data = instr.get('data', '')
                accounts = instr.get('accounts', [])

                parsed = self.decoder.decode_instruction_data(data, accounts)

                if parsed:
                    kamino_instructions.append({
                        'name': parsed.name,
                        'type': parsed.instruction_type,
                        'discriminator': parsed.discriminator,
                        'args': parsed.args,
                        'accounts': parsed.accounts,
                    })

                    # 检查是否为清算指令
                    if parsed.instruction_type == 'liquidation':
                        result['is_liquidation'] = True

        result['kamino_instructions'] = kamino_instructions

        # 解析 SOL 余额变化
        accounts = tx_data.get('accounts', [])
        pre_balances = tx_data.get('preBalances', [])
        post_balances = tx_data.get('postBalances', [])

        for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
            if pre != post:
                change = (post - pre) / 1e9  # 转换为 SOL
                result['sol_balance_changes'].append({
                    'account': accounts[i] if i < len(accounts) else f'account_{i}',
                    'pre_balance': pre / 1e9,
                    'post_balance': post / 1e9,
                    'change': change
                })

        # 解析 Token 余额变化
        pre_token = tx_data.get('preTokenBalances', [])
        post_token = tx_data.get('postTokenBalances', [])

        token_changes = self._parse_token_balance_changes(
            pre_token, post_token, accounts
        )
        result['token_balance_changes'] = token_changes

        # 解析日志
        logs = tx_data.get('logMessages', [])
        result['parsed_logs'] = self._parse_logs(logs)
        result['raw_logs'] = logs

        # 如果是清算交易，提取详细信息
        if result['is_liquidation']:
            result['liquidation_details'] = self._extract_liquidation_details(
                tx_data, result, kamino_instructions
            )

        return result

    def _parse_token_balance_changes(
        self,
        pre_token: List[Dict],
        post_token: List[Dict],
        accounts: List[str]
    ) -> List[Dict]:
        """解析 Token 余额变化"""
        changes = []

        # 构建 pre 和 post 的映射
        pre_map = {}
        for item in pre_token:
            acc_idx = item.get('accountIndex', -1)
            owner = item.get('owner', '')
            mint = item.get('mint', '')
            amount = item.get('uiTokenAmount', {})
            key = f"{acc_idx}_{owner}_{mint}"
            pre_map[key] = {
                'accountIndex': acc_idx,
                'owner': owner,
                'mint': mint,
                'amount': float(amount.get('uiAmount', 0) or 0),
                'decimals': amount.get('decimals', 0)
            }

        post_map = {}
        for item in post_token:
            acc_idx = item.get('accountIndex', -1)
            owner = item.get('owner', '')
            mint = item.get('mint', '')
            amount = item.get('uiTokenAmount', {})
            key = f"{acc_idx}_{owner}_{mint}"
            post_map[key] = {
                'accountIndex': acc_idx,
                'owner': owner,
                'mint': mint,
                'amount': float(amount.get('uiAmount', 0) or 0),
                'decimals': amount.get('decimals', 0)
            }

        # 计算变化
        all_keys = set(pre_map.keys()) | set(post_map.keys())

        for key in all_keys:
            pre = pre_map.get(key, {'amount': 0, 'decimals': 0})
            post = post_map.get(key, {'amount': 0, 'decimals': 0})

            pre_amount = pre.get('amount', 0)
            post_amount = post.get('amount', 0)
            change = post_amount - pre_amount

            if abs(change) > 1e-10:  # 忽略极小变化
                mint = post.get('mint') or pre.get('mint', '')
                token_info = get_token_info(mint)

                changes.append({
                    'owner': post.get('owner') or pre.get('owner', ''),
                    'mint': mint,
                    'symbol': token_info['symbol'],
                    'pre_amount': pre_amount,
                    'post_amount': post_amount,
                    'change': change,
                    'decimals': post.get('decimals') or pre.get('decimals', 0)
                })

        return changes

    def _parse_logs(self, logs: List[str]) -> Dict[str, Any]:
        """解析日志中的关键信息"""
        result = {
            'program_invokes': [],
            'kamino_events': [],
            'errors': [],
            'liquidation_log': None
        }

        for log in logs:
            # 程序调用
            if 'invoke' in log.lower():
                result['program_invokes'].append(log)

            # 错误
            if 'error' in log.lower() or 'failed' in log.lower():
                result['errors'].append(log)

            # Kamino 特定日志
            if 'Kamino' in log or 'kamino' in log:
                result['kamino_events'].append(log)

            # 清算相关日志 - 查找数字模式
            if 'liquidat' in log.lower():
                result['liquidation_log'] = log

        return result

    def _extract_liquidation_details(
        self,
        tx_data: Dict[str, Any],
        parsed_result: Dict[str, Any],
        kamino_instructions: List[Dict]
    ) -> Dict[str, Any]:
        """提取清算详细信息"""
        details = {
            'liquidator': '',
            'obligation_owner': '',
            'debt_token': '',
            'debt_amount': 0.0,
            'debt_decimals': 6,
            'collateral_token': '',
            'total_collateral': 0.0,        # 总抵押品（扣除协议费前）
            'received_collateral': 0.0,      # 清算人实际收到的抵押品
            'collateral_amount': 0.0,        # 用于计算的抵押品（=总抵押品）
            'collateral_decimals': 9,
            'protocol_fee': 0.0,
            'protocol_fee_deducted': False,  # 协议费是否已从 received_collateral 扣除
            'transaction_fee': tx_data.get('fee', 0) / 1e9,
            'priority_fee': 0.0
        }

        # 从 Token 余额变化中提取
        token_changes = parsed_result.get('token_balance_changes', [])

        # 首先找清算人（支付 USDC/USDT 的人）
        for change in token_changes:
            if change['change'] < 0 and change['symbol'] in ['USDC', 'USDT']:
                details['debt_token'] = change['symbol']
                details['debt_amount'] = abs(change['change'])
                details['debt_decimals'] = change['decimals']
                details['liquidator'] = change['owner']
                break

        # 找被清算方（SOL 类代币减少的人，且不是清算人）
        for change in token_changes:
            if change['change'] < -0.01 and change['symbol'] in ['SOL', 'mSOL', 'stSOL', 'JitoSOL', 'bSOL']:
                if change['owner'] != details['liquidator']:
                    details['obligation_owner'] = change['owner']
                    details['total_collateral'] = abs(change['change'])
                    details['collateral_token'] = change['symbol']
                    break

        # 找清算人收到的抵押品
        if details['liquidator']:
            for change in token_changes:
                if change['owner'] == details['liquidator'] and change['change'] > 0:
                    if change['symbol'] in ['SOL', 'mSOL', 'stSOL', 'JitoSOL', 'bSOL']:
                        details['received_collateral'] = change['change']
                        if not details['collateral_token']:
                            details['collateral_token'] = change['symbol']
                        break

        # 找协议费（被清算方收到的小额 SOL）
        if details['obligation_owner']:
            for change in token_changes:
                if change['owner'] == details['obligation_owner']:
                    if 0 < change['change'] < 0.01 and change['symbol'] in ['SOL', 'mSOL', 'stSOL', 'JitoSOL', 'bSOL']:
                        details['protocol_fee'] = change['change']
                        break

        # 验证协议费是否已从 received_collateral 扣除
        # 如果 received_collateral + protocol_fee ≈ total_collateral，则已扣除
        if details['total_collateral'] > 0 and details['protocol_fee'] > 0:
            if abs(details['received_collateral'] + details['protocol_fee'] - details['total_collateral']) < 0.0001:
                details['protocol_fee_deducted'] = True

        # 用于计算的抵押品 = 总抵押品（这样协议费只会被扣除一次）
        details['collateral_amount'] = details['total_collateral'] if details['total_collateral'] > 0 else details['received_collateral']

        # 计算优先费
        base_fee = 5000 / 1e9
        total_fee = details['transaction_fee']
        details['priority_fee'] = max(0, total_fee - base_fee)

        return details


# ============================================================================
# 主函数
# ============================================================================

def parse_transaction(
    input_file: str,
    output_file: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    解析交易

    Args:
        input_file: 输入的 JSON 文件（步骤1的输出）
        output_file: 输出文件路径（可选）
        verbose: 是否打印详细信息

    Returns:
        解析后的数据
    """
    if verbose:
        print("=" * 70)
        print("Kamino 清算分析 - 步骤 2: 解析交易")
        print("=" * 70)

    # 加载交易数据
    with open(input_file, 'r', encoding='utf-8') as f:
        tx_data = json.load(f)

    if verbose:
        print(f"\n输入文件: {input_file}")
        print(f"交易签名: {tx_data.get('signature', '')[:32]}...")

    # 创建解析器
    parser = LiquidationParser()

    # 解析交易
    result = parser.parse_transaction(tx_data)

    if verbose:
        print(f"\n解析结果:")
        print(f"  是 Kamino 交易: {result['is_kamino_tx']}")
        print(f"  是清算交易: {result['is_liquidation']}")
        print(f"  Kamino 指令数: {len(result['kamino_instructions'])}")

        if result['kamino_instructions']:
            print(f"\n  Kamino 指令:")
            for instr in result['kamino_instructions']:
                print(f"    - {instr['name']} ({instr['type']})")

        if result['sol_balance_changes']:
            print(f"\n  SOL 余额变化:")
            for change in result['sol_balance_changes'][:5]:
                direction = "+" if change['change'] > 0 else ""
                print(
                    f"    {change['account'][:16]}... : {direction}{change['change']:.9f} SOL")

        if result['token_balance_changes']:
            print(f"\n  Token 余额变化:")
            for change in result['token_balance_changes']:
                direction = "+" if change['change'] > 0 else ""
                print(
                    f"    {change['owner'][:16]}... : {direction}{change['change']:.6f} {change['symbol']}")

        if result['liquidation_details']:
            details = result['liquidation_details']
            print(f"\n  清算详情:")
            print(f"    清算人: {details['liquidator'][:32]}...")
            print(
                f"    代偿债务: {details['debt_amount']:.6f} {details['debt_token']}")
            print(
                f"    获得抵押品: {details['collateral_amount']:.9f} {details['collateral_token']}")
            print(f"    交易费: {details['transaction_fee']:.9f} SOL")
            print(f"    优先费: {details['priority_fee']:.9f} SOL")

    # 保存结果
    if output_file:
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        if verbose:
            print(f"\n✓ 已保存到: {output_file}")

    return result


def main():
    """命令行入口"""
    if len(sys.argv) < 2:
        print("用法: python 02_parse_transaction.py <input_json> [output_json]")
        print("\n示例:")
        print("  python 02_parse_transaction.py ./data/5gcWCkHxdYKJCH_raw.json")
        print("  python 02_parse_transaction.py ./data/tx_raw.json ./data/tx_parsed.json")
        sys.exit(1)

    input_file = sys.argv[1]

    # 生成默认输出文件名
    input_path = Path(input_file)
    default_output = input_path.parent / f"{input_path.stem}_parsed.json"
    output_file = sys.argv[2] if len(sys.argv) > 2 else str(default_output)

    try:
        result = parse_transaction(input_file, output_file)
        print(f"\n{'=' * 70}")
        print("✓ 步骤 2 完成！可以继续执行步骤 3 计算利润")
        print(f"  python 03_calculate_profit.py {output_file}")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
