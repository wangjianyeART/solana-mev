#!/usr/bin/env python3
"""
步骤 2: 批量解析 Jupiter Lend 清算交易

功能：
- 批量解析步骤1获取的清算交易
- 使用 IDL 解析 Jupiter Vaults 指令
- 提取余额变化和清算详情

使用方法：
    python step2_batch_parse.py <input_json>
    python step2_batch_parse.py ./data/jupiter_liquidations_raw_20260130.json
"""

import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

from jupiter_decoder import JupiterDecoder, JUPITER_VAULTS_PROGRAM_ID


# ============================================================================
# 已知 Token 信息
# ============================================================================

KNOWN_TOKENS = {
    "So11111111111111111111111111111111111111112": {
        "symbol": "SOL", "decimals": 9, "name": "Wrapped SOL"
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "symbol": "USDC", "decimals": 6, "name": "USD Coin"
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "symbol": "USDT", "decimals": 6, "name": "Tether USD"
    },
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {
        "symbol": "mSOL", "decimals": 9, "name": "Marinade staked SOL"
    },
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": {
        "symbol": "stSOL", "decimals": 9, "name": "Lido Staked SOL"
    },
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {
        "symbol": "JitoSOL", "decimals": 9, "name": "Jito Staked SOL"
    },
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": {
        "symbol": "bSOL", "decimals": 9, "name": "BlazeStake Staked SOL"
    },
    "jupSoLaHXQiZZTSfEWMTRRgpnyFm8f6sZdosWBjx93v": {
        "symbol": "jupSOL", "decimals": 9, "name": "Jupiter Staked SOL"
    },
    "JuprjznTrTSp2UFa3ZBUFgwdAmtZCq4MQCwysN55USD": {
        "symbol": "jupUSD", "decimals": 6, "name": "Jupiter USD"
    },
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {
        "symbol": "cbBTC", "decimals": 8, "name": "Coinbase Wrapped BTC"
    },
}


def get_token_info(mint: str) -> Dict[str, Any]:
    """获取 Token 信息"""
    if mint in KNOWN_TOKENS:
        return KNOWN_TOKENS[mint]
    return {"symbol": mint[:8] + "...", "decimals": 9, "name": "Unknown Token"}


# ============================================================================
# 批量解析器
# ============================================================================

class BatchLiquidationParser:
    """批量清算交易解析器"""

    def __init__(self, idl_path: Optional[str] = None):
        if idl_path is None:
            idl_path = str(Path(__file__).parent / "vaults.json")
        self.decoder = JupiterDecoder(idl_path)

    def parse_raw_transaction(self, tx_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        解析原始交易数据（来自步骤1的格式）

        Args:
            tx_data: 步骤1输出的交易数据，包含 signature, slot, blockTime, transaction

        Returns:
            解析后的交易数据
        """
        signature = tx_data.get('signature', '')
        slot = tx_data.get('slot', 0)
        block_time = tx_data.get('blockTime', 0)

        # 从 transaction 字段获取详细信息
        raw_tx = tx_data.get('transaction', {})
        meta = raw_tx.get('meta', {})
        transaction = raw_tx.get('transaction', {})
        message = transaction.get('message', {})

        # 基础信息
        result = {
            'signature': signature,
            'slot': slot,
            'blockTime': block_time,
            'datetime': datetime.fromtimestamp(block_time).strftime('%Y-%m-%dT%H:%M:%S') if block_time else '',
            'success': meta.get('err') is None,
            'fee': meta.get('fee', 0),

            'is_jupiter_tx': False,
            'is_liquidation': False,
            'jupiter_instructions': [],

            'sol_balance_changes': [],
            'token_balance_changes': [],

            'raw_logs': meta.get('logMessages', []),
            'liquidation_details': None
        }

        # 获取账户列表
        account_keys = message.get('accountKeys', [])
        accounts = []
        for acc in account_keys:
            if isinstance(acc, dict):
                accounts.append(acc.get('pubkey', ''))
            else:
                accounts.append(acc)

        # 解析指令
        instructions = message.get('instructions', [])
        inner_instructions = meta.get('innerInstructions', [])

        # 合并所有指令（包括内部指令）
        all_instructions = []

        for idx, instr in enumerate(instructions):
            all_instructions.append(('outer', idx, instr))

        for inner in inner_instructions:
            for instr in inner.get('instructions', []):
                all_instructions.append(('inner', inner.get('index', -1), instr))

        # 解析 Jupiter Vaults 指令
        jupiter_instructions = []

        for instr_type, parent_idx, instr in all_instructions:
            # 获取程序 ID
            program_id = instr.get('programId', '')
            if not program_id and 'programIdIndex' in instr:
                idx = instr['programIdIndex']
                if idx < len(accounts):
                    program_id = accounts[idx]

            if program_id == JUPITER_VAULTS_PROGRAM_ID:
                result['is_jupiter_tx'] = True

                # 获取指令数据和账户
                data = instr.get('data', '')

                # 获取指令账户
                instr_accounts = []
                if 'accounts' in instr:
                    for acc in instr['accounts']:
                        if isinstance(acc, int) and acc < len(accounts):
                            instr_accounts.append(accounts[acc])
                        elif isinstance(acc, str):
                            instr_accounts.append(acc)

                # 使用解码器解析
                parsed = self.decoder.decode_instruction_data(data, instr_accounts)

                if parsed:
                    jupiter_instructions.append({
                        'name': parsed.name,
                        'type': parsed.instruction_type,
                        'discriminator': parsed.discriminator,
                        'args': parsed.args,
                        'accounts': parsed.accounts,
                    })

                    if parsed.instruction_type == 'liquidation':
                        result['is_liquidation'] = True

        result['jupiter_instructions'] = jupiter_instructions

        # 解析 SOL 余额变化
        pre_balances = meta.get('preBalances', [])
        post_balances = meta.get('postBalances', [])

        for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
            if pre != post:
                change = (post - pre) / 1e9
                result['sol_balance_changes'].append({
                    'account': accounts[i] if i < len(accounts) else f'account_{i}',
                    'pre_balance': pre / 1e9,
                    'post_balance': post / 1e9,
                    'change': change
                })

        # 解析 Token 余额变化
        pre_token = meta.get('preTokenBalances', [])
        post_token = meta.get('postTokenBalances', [])

        result['token_balance_changes'] = self._parse_token_balance_changes(
            pre_token, post_token, accounts
        )

        # 如果是清算交易，提取详细信息
        if result['is_liquidation']:
            result['liquidation_details'] = self._extract_liquidation_details(
                result, jupiter_instructions
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

        all_keys = set(pre_map.keys()) | set(post_map.keys())

        for key in all_keys:
            pre = pre_map.get(key, {'amount': 0, 'decimals': 0})
            post = post_map.get(key, {'amount': 0, 'decimals': 0})

            pre_amount = pre.get('amount', 0)
            post_amount = post.get('amount', 0)
            change = post_amount - pre_amount

            if abs(change) > 1e-10:
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

    def _extract_liquidation_details(
        self,
        parsed_result: Dict[str, Any],
        jupiter_instructions: List[Dict]
    ) -> Dict[str, Any]:
        """提取清算详细信息"""
        details = {
            'liquidator': '',
            'debt_token': '',
            'debt_amount': 0.0,
            'collateral_token': '',
            'collateral_amount': 0.0,
            'transaction_fee': parsed_result.get('fee', 0) / 1e9,
            'priority_fee': 0.0
        }

        # 从清算指令中提取信息
        for instr in jupiter_instructions:
            if instr.get('type') == 'liquidation':
                accounts = instr.get('accounts', [])
                args = instr.get('args', {})

                # 从账户中获取清算人
                if accounts:
                    details['liquidator'] = accounts[0].get('address', '')

                # 从参数中获取债务金额
                if 'debt_amt' in args:
                    # 假设 jupUSD 是 6 位小数
                    details['debt_amount'] = args['debt_amt'] / 1e6

                # 从账户中获取代币信息
                for acc in accounts:
                    name = acc.get('name', '')
                    if name == 'supply_token':
                        details['collateral_token'] = acc.get('address', '')
                    elif name == 'borrow_token':
                        details['debt_token'] = acc.get('address', '')

        # 从 token 变化中提取更多信息
        token_changes = parsed_result.get('token_balance_changes', [])

        # 找清算人收到的抵押品
        for change in token_changes:
            if change['change'] > 0 and change['symbol'] in ['jupSOL', 'SOL', 'mSOL', 'stSOL', 'JitoSOL', 'bSOL']:
                if details['collateral_amount'] == 0:
                    details['collateral_amount'] = change['change']
                    details['collateral_token'] = change['symbol']

        # 计算优先费
        base_fee = 5000 / 1e9
        total_fee = details['transaction_fee']
        details['priority_fee'] = max(0, total_fee - base_fee)

        return details


def batch_parse(
    input_file: str,
    output_file: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    批量解析交易

    Args:
        input_file: 步骤1输出的 JSON 文件
        output_file: 输出文件路径
        verbose: 是否打印详细信息

    Returns:
        解析后的数据
    """
    if verbose:
        print("=" * 70)
        print("Jupiter Lend - 步骤 2: 批量解析交易")
        print("=" * 70)

    # 加载数据
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    metadata = data.get('metadata', {})
    transactions = data.get('transactions', [])

    if verbose:
        print(f"\n输入文件: {input_file}")
        print(f"交易数量: {len(transactions)}")

    # 创建解析器
    parser = BatchLiquidationParser()

    # 解析所有交易
    parsed_txs = []
    liquidation_count = 0

    for i, tx in enumerate(transactions):
        parsed = parser.parse_raw_transaction(tx)
        parsed_txs.append(parsed)

        if parsed['is_liquidation']:
            liquidation_count += 1

        if verbose and (i + 1) % 10 == 0:
            print(f"  处理: {i + 1}/{len(transactions)}")

    if verbose:
        print(f"\n解析完成:")
        print(f"  总交易数: {len(parsed_txs)}")
        print(f"  清算交易: {liquidation_count}")
        print(f"  非清算交易: {len(parsed_txs) - liquidation_count}")

    # 只保留清算交易
    liquidation_txs = [tx for tx in parsed_txs if tx['is_liquidation']]

    # 保存结果
    if output_file is None:
        input_path = Path(input_file)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = input_path.parent / f"jupiter_liquidations_parsed_{timestamp}.json"

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(liquidation_txs, f, indent=2, ensure_ascii=False)

    if verbose:
        print(f"\n已保存: {output_file}")
        print(f"清算交易数: {len(liquidation_txs)}")

    print("\n" + "=" * 70)
    print("步骤 2 完成!")
    print(f"下一步: python step3_analyze.py {output_file}")
    print("=" * 70)

    return {
        'metadata': metadata,
        'parsed_count': len(parsed_txs),
        'liquidation_count': liquidation_count,
        'output_file': str(output_file),
        'liquidations': liquidation_txs
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python step2_batch_parse.py <input_json> [output_json]")
        print("\n示例:")
        print("  python step2_batch_parse.py ./data/jupiter_liquidations_raw_20260130.json")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        batch_parse(input_file, output_file)
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
