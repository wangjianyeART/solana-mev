#!/usr/bin/env python3
"""
Kamino 清算分析器 - 一站式分析工具

功能：
- 输入交易签名，自动完成获取、解析、计算利润的全流程
- 支持批量分析多个清算交易
- 支持手动输入参数进行快速计算

使用方法：
    # 分析单个交易
    python kamino_liquidation_analyzer.py 5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg
    
    # 指定 SOL 价格
    python kamino_liquidation_analyzer.py <signature> --sol-price 204.92
    
    # 手动计算（不需要交易）
    python kamino_liquidation_analyzer.py --manual \\
        --collateral 0.05479 \\
        --protocol-fee 0.0013 \\
        --priority-fee 0.001317 \\
        --debt 10.642 \\
        --sol-price 204.92
"""

import urllib.error
import urllib.request
import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import asdict

# 导入各步骤模块
from kamino_decoder import KaminoDecoder, KAMINO_LENDING_PROGRAM_ID


# ============================================================================
# 配置
# ============================================================================

def load_env():
    """加载 .env 文件"""
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()


# ============================================================================
# 内联的 Helius 客户端（避免循环导入）
# ============================================================================


class HeliusClient:
    """Helius API 客户端"""

    def __init__(self, api_key: Optional[str] = None):
        load_env()
        self.api_key = api_key or os.environ.get('HELIUS_API_KEY')
        if not self.api_key:
            raise ValueError("未找到 HELIUS_API_KEY")
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    def _make_rpc_request(self, method: str, params: list) -> Dict[str, Any]:
        """发送 RPC 请求"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }

        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}

        request = urllib.request.Request(
            self.rpc_url,
            data=data,
            headers=headers,
            method='POST'
        )

        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            if 'error' in result:
                raise Exception(f"RPC 错误: {result['error']}")
            return result.get('result')

    def get_transaction(self, signature: str) -> Optional[Dict[str, Any]]:
        """获取交易"""
        params = [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
        ]
        return self._make_rpc_request("getTransaction", params)


# ============================================================================
# 已知 Token
# ============================================================================

KNOWN_TOKENS = {
    "So11111111111111111111111111111111111111112": {"symbol": "SOL", "decimals": 9},
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "decimals": 6},
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {"symbol": "USDT", "decimals": 6},
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {"symbol": "mSOL", "decimals": 9},
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {"symbol": "JitoSOL", "decimals": 9},
}


def get_token_symbol(mint: str) -> str:
    return KNOWN_TOKENS.get(mint, {}).get('symbol', mint[:8])


# ============================================================================
# 价格获取
# ============================================================================

def get_sol_price() -> Optional[float]:
    """获取当前 SOL 价格"""
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT"
        request = urllib.request.Request(
            url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            return float(data.get('price', 0))
    except:
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
            request = urllib.request.Request(
                url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data.get('solana', {}).get('usd')
        except:
            return None


def get_historical_sol_price(timestamp: int) -> Optional[float]:
    """
    获取历史 SOL 价格（基于 Binance K 线数据）

    Args:
        timestamp: Unix 时间戳（秒）

    Returns:
        当时的 SOL 价格（USD）
    """
    try:
        # 将时间向下取整到分钟，然后获取该分钟的 K 线
        # Binance K 线的 startTime 是 K 线的开盘时间
        minute_start = (timestamp // 60) * 60 * 1000  # 向下取整到分钟，转为毫秒

        url = f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=1m&startTime={minute_start}&limit=1"

        request = urllib.request.Request(
            url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            klines = json.loads(response.read().decode('utf-8'))

        if klines:
            # K线数据: [开盘时间, 开盘价, 最高价, 最低价, 收盘价, ...]
            # 使用该分钟的收盘价作为交易时的价格
            close_price = float(klines[0][4])
            return close_price
    except:
        pass

    return None


# ============================================================================
# 核心分析类
# ============================================================================

class KaminoLiquidationAnalyzer:
    """Kamino 清算分析器"""

    def __init__(self, sol_price: Optional[float] = None):
        """
        初始化分析器

        Args:
            sol_price: SOL 价格，如果为 None 则自动获取
        """
        self.sol_price = sol_price
        self.decoder = None
        self.client = None

    def _init_decoder(self):
        """延迟初始化解码器"""
        if self.decoder is None:
            idl_path = Path(__file__).parent / "kamino_lending_idl.json"
            self.decoder = KaminoDecoder(str(idl_path))

    def _init_client(self):
        """延迟初始化 Helius 客户端"""
        if self.client is None:
            self.client = HeliusClient()

    def analyze_transaction(
        self,
        signature: str,
        sol_price: Optional[float] = None,
        protocol_fee: Optional[float] = None,
        verbose: bool = True
    ) -> Dict[str, Any]:
        """
        分析单个清算交易

        Args:
            signature: 交易签名
            sol_price: SOL 价格（可选）
            protocol_fee: 协议费（可选）
            verbose: 是否打印详细信息

        Returns:
            分析结果字典
        """
        price = sol_price or self.sol_price

        if verbose:
            print("=" * 70)
            print("🔍 Kamino 清算分析器")
            print("=" * 70)
            print(f"\n交易签名: {signature}")

        # ==========================================
        # 步骤 1: 获取交易数据
        # ==========================================
        if verbose:
            print("\n📥 步骤 1: 获取交易数据...")

        self._init_client()
        tx_raw = self.client.get_transaction(signature)

        if not tx_raw:
            raise Exception(f"无法获取交易: {signature}")

        # 解析原始数据
        meta = tx_raw.get('meta', {})
        transaction = tx_raw.get('transaction', {})
        message = transaction.get('message', {})

        # 账户列表
        account_keys = message.get('accountKeys', [])
        accounts = []
        for acc in account_keys:
            if isinstance(acc, dict):
                accounts.append(acc.get('pubkey', ''))
            else:
                accounts.append(acc)

        if verbose:
            print(f"   ✓ 区块: {tx_raw.get('slot')}")
            print(
                f"   ✓ 时间: {datetime.fromtimestamp(tx_raw.get('blockTime', 0)).isoformat()}")
            print(f"   ✓ 账户数: {len(accounts)}")

        # ==========================================
        # 步骤 2: 解析交易
        # ==========================================
        if verbose:
            print("\n📋 步骤 2: 解析交易...")

        self._init_decoder()

        # 解析指令
        instructions = message.get('instructions', [])
        kamino_instructions = []
        is_liquidation = False

        for instr in instructions:
            program_id = instr.get('programId', '')
            if not program_id and 'programIdIndex' in instr:
                idx = instr['programIdIndex']
                if idx < len(accounts):
                    program_id = accounts[idx]

            if program_id == KAMINO_LENDING_PROGRAM_ID:
                # 获取数据和账户
                data = instr.get('data', '')
                acc_indices = instr.get('accounts', [])
                instr_accounts = []
                for idx in acc_indices:
                    if isinstance(idx, int) and idx < len(accounts):
                        instr_accounts.append(accounts[idx])

                # 解码
                parsed = self.decoder.decode_instruction_data(
                    data, instr_accounts)
                if parsed:
                    kamino_instructions.append({
                        'name': parsed.name,
                        'type': parsed.instruction_type,
                        'args': parsed.args,
                        'accounts': parsed.accounts
                    })
                    if parsed.instruction_type == 'liquidation':
                        is_liquidation = True

        if verbose:
            print(f"   ✓ Kamino 指令数: {len(kamino_instructions)}")
            print(f"   ✓ 是清算交易: {is_liquidation}")
            if kamino_instructions:
                for ki in kamino_instructions:
                    print(f"     - {ki['name']} ({ki['type']})")

        # ==========================================
        # 步骤 3: 提取余额变化
        # ==========================================
        if verbose:
            print("\n💰 步骤 3: 提取余额变化...")

        # SOL 余额变化
        pre_balances = meta.get('preBalances', [])
        post_balances = meta.get('postBalances', [])

        sol_changes = []
        for i, (pre, post) in enumerate(zip(pre_balances, post_balances)):
            if pre != post:
                sol_changes.append({
                    'account': accounts[i] if i < len(accounts) else f'account_{i}',
                    'pre': pre / 1e9,
                    'post': post / 1e9,
                    'change': (post - pre) / 1e9
                })

        # Token 余额变化
        pre_token = meta.get('preTokenBalances', [])
        post_token = meta.get('postTokenBalances', [])

        token_changes = []

        # 构建映射
        pre_map = {}
        for item in pre_token:
            key = (item.get('accountIndex', -1), item.get('mint', ''))
            amt = item.get('uiTokenAmount', {})
            pre_map[key] = {
                'owner': item.get('owner', ''),
                'mint': item.get('mint', ''),
                'amount': float(amt.get('uiAmount', 0) or 0),
                'decimals': amt.get('decimals', 0)
            }

        post_map = {}
        for item in post_token:
            key = (item.get('accountIndex', -1), item.get('mint', ''))
            amt = item.get('uiTokenAmount', {})
            post_map[key] = {
                'owner': item.get('owner', ''),
                'mint': item.get('mint', ''),
                'amount': float(amt.get('uiAmount', 0) or 0),
                'decimals': amt.get('decimals', 0)
            }

        all_keys = set(pre_map.keys()) | set(post_map.keys())
        for key in all_keys:
            pre = pre_map.get(key, {'amount': 0})
            post = post_map.get(key, {'amount': 0})
            change = post.get('amount', 0) - pre.get('amount', 0)

            if abs(change) > 1e-10:
                mint = post.get('mint') or pre.get('mint', '')
                token_changes.append({
                    'owner': post.get('owner') or pre.get('owner', ''),
                    'mint': mint,
                    'symbol': get_token_symbol(mint),
                    'pre': pre.get('amount', 0),
                    'post': post.get('amount', 0),
                    'change': change
                })

        if verbose:
            print(f"   ✓ SOL 余额变化: {len(sol_changes)} 条")
            print(f"   ✓ Token 余额变化: {len(token_changes)} 条")

            for tc in token_changes:
                direction = "+" if tc['change'] > 0 else ""
                print(
                    f"     {tc['owner'][:16]}...: {direction}{tc['change']:.6f} {tc['symbol']}")

        # ==========================================
        # 步骤 4: 提取清算数据
        # ==========================================
        if verbose:
            print("\n📊 步骤 4: 提取清算数据...")

        # 从 Token 变化中识别债务和抵押品
        debt_amount = 0
        debt_token = ''
        collateral_amount = 0
        collateral_token = ''
        liquidator = ''

        # 首先找到清算人（支付 USDC/USDT 的人）
        for tc in token_changes:
            if tc['change'] < 0 and tc['symbol'] in ['USDC', 'USDT']:
                debt_amount = abs(tc['change'])
                debt_token = tc['symbol']
                liquidator = tc['owner']
                break

        # 然后找清算人收到的抵押品（SOL 类代币的正变化）
        if liquidator:
            for tc in token_changes:
                if tc['owner'] == liquidator and tc['change'] > 0:
                    if tc['symbol'] in ['SOL', 'mSOL', 'JitoSOL', 'stSOL', 'bSOL']:
                        collateral_amount = tc['change']
                        collateral_token = tc['symbol']
                        break

        # 如果没有从 Token 变化中找到抵押品，从所有 SOL 正变化中找最大的
        if not collateral_token:
            for tc in token_changes:
                if tc['change'] > 0 and tc['symbol'] in ['SOL', 'mSOL', 'JitoSOL', 'stSOL', 'bSOL']:
                    if tc['change'] > collateral_amount:
                        collateral_amount = tc['change']
                        collateral_token = tc['symbol']

        # 交易费
        tx_fee = meta.get('fee', 0) / 1e9

        # 优先费估算（基础费约 5000 lamports）
        base_fee = 5000 / 1e9
        priority_fee = max(0, tx_fee - base_fee)

        # 协议费 - 检测是否已经从抵押品中扣除
        # 从 Token 变化中找被清算方收到的小额 SOL（这是协议费返还给被清算方的部分）
        detected_protocol_fee = 0
        obligation_owner = ''  # 被清算方

        for tc in token_changes:
            # 找被清算方（SOL 余额减少的人，且不是清算人）
            if tc['change'] < -0.01 and tc['symbol'] == 'SOL' and tc['owner'] != liquidator:
                obligation_owner = tc['owner']
                break

        # 找被清算方收到的小额 SOL（协议费返还）
        if obligation_owner:
            for tc in token_changes:
                if tc['owner'] == obligation_owner and 0 < tc['change'] < 0.01 and tc['symbol'] == 'SOL':
                    detected_protocol_fee = tc['change']
                    break

        # 如果用户手动指定协议费，使用手动值，但需要判断抵押品是否已扣除
        # 如果清算人收到的抵押品 + 协议费 ≈ 被清算方损失的抵押品，则抵押品已扣除协议费
        prot_fee = protocol_fee if protocol_fee is not None else detected_protocol_fee

        # 检查抵押品是否已经扣除了协议费
        # 如果 collateral_amount + detected_protocol_fee ≈ 被清算方损失的 SOL，则已扣除
        total_collateral = 0
        for tc in token_changes:
            if tc['owner'] == obligation_owner and tc['change'] < 0 and tc['symbol'] in ['SOL', 'mSOL', 'JitoSOL']:
                total_collateral = abs(tc['change'])
                break

        # 判断：如果 清算人收到 + 协议费 ≈ 总抵押品，说明协议费已在抵押品中扣除
        protocol_fee_already_deducted = False
        if total_collateral > 0 and detected_protocol_fee > 0:
            if abs(collateral_amount + detected_protocol_fee - total_collateral) < 0.0001:
                protocol_fee_already_deducted = True

        if verbose:
            print(
                f"   ✓ 清算人: {liquidator[:32]}..." if liquidator else "   ⚠ 未识别清算人")
            if obligation_owner:
                print(f"   ✓ 被清算方: {obligation_owner[:32]}...")
            print(f"   ✓ 代偿债务: {debt_amount:.6f} {debt_token}")
            print(f"   ✓ 总抵押品: {total_collateral:.9f} SOL")
            print(f"   ✓ 清算人收到: {collateral_amount:.9f} {collateral_token}")
            print(
                f"   ✓ 协议费: {prot_fee:.9f} SOL {'(已从抵押品扣除)' if protocol_fee_already_deducted else ''}")
            print(f"   ✓ 交易费: {tx_fee:.9f} SOL")
            print(f"   ✓ 优先费: {priority_fee:.9f} SOL")

        # ==========================================
        # 步骤 5: 计算利润
        # ==========================================
        if verbose:
            print("\n💵 步骤 5: 计算利润...")

        # 确定 SOL 价格
        tx_timestamp = tx_raw.get('blockTime', 0)
        price_source = ''

        if price is None:
            # 优先使用历史价格（交易发生时的价格）
            if tx_timestamp:
                if verbose:
                    print(f"   正在获取交易时刻的历史 SOL 价格...")
                price = get_historical_sol_price(tx_timestamp)
                if price:
                    price_source = '(历史价格 - Binance)'

            # 如果获取历史价格失败，使用当前价格
            if price is None:
                if verbose:
                    print("   无法获取历史价格，正在获取当前 SOL 价格...")
                price = get_sol_price()
                price_source = '(当前价格)'

            if price is None:
                raise ValueError("无法获取 SOL 价格，请使用 --sol-price 参数指定")
        else:
            price_source = '(手动指定)'

        if verbose:
            print(f"   ✓ SOL 价格: ${price:.2f} {price_source}")

        # 核心计算
        # 如果协议费已经从清算人收到的抵押品中扣除，就不再减协议费
        if protocol_fee_already_deducted:
            # 使用总抵押品进行计算，然后扣除协议费和优先费
            net_collateral = total_collateral - prot_fee - priority_fee
        else:
            # 协议费还没扣除，需要从抵押品中扣除
            net_collateral = collateral_amount - prot_fee - priority_fee

        collateral_value_usd = net_collateral * price
        net_profit_usd = collateral_value_usd - debt_amount
        profit_rate = (net_profit_usd / debt_amount *
                       100) if debt_amount > 0 else 0

        # ==========================================
        # 输出结果
        # ==========================================
        result = {
            'signature': signature,
            'slot': tx_raw.get('slot'),
            'timestamp': tx_raw.get('blockTime'),
            'datetime': datetime.fromtimestamp(tx_raw.get('blockTime', 0)).isoformat(),
            'is_liquidation': is_liquidation,

            'liquidator': liquidator,
            'obligation_owner': obligation_owner,
            'debt_amount': debt_amount,
            'debt_token': debt_token,
            'total_collateral': total_collateral,  # 总抵押品（扣除协议费前）
            'collateral_amount': collateral_amount,  # 清算人实际收到的抵押品
            'collateral_token': collateral_token,

            'protocol_fee': prot_fee,
            'protocol_fee_already_deducted': protocol_fee_already_deducted,
            'priority_fee': priority_fee,
            'transaction_fee': tx_fee,

            'sol_price': price,
            'net_collateral': net_collateral,
            'collateral_value_usd': collateral_value_usd,
            'net_profit_usd': net_profit_usd,
            'profit_rate': profit_rate,
            'is_profitable': net_profit_usd > 0,

            'kamino_instructions': kamino_instructions,
            'sol_changes': sol_changes,
            'token_changes': token_changes
        }

        if verbose:
            self._print_profit_report(result)

        return result

    def calculate_manual(
        self,
        collateral_amount: float,
        protocol_fee: float,
        priority_fee: float,
        debt_amount: float,
        sol_price: float,
        verbose: bool = True
    ) -> Dict[str, Any]:
        """
        手动计算清算利润

        Args:
            collateral_amount: 清算所得 (SOL)
            protocol_fee: 协议费 (SOL)
            priority_fee: 优先费 (SOL)
            debt_amount: 代偿债务 (USDC)
            sol_price: SOL 价格 (USD)
            verbose: 是否打印详细信息

        Returns:
            计算结果
        """
        # 核心计算
        net_collateral = collateral_amount - protocol_fee - priority_fee
        collateral_value_usd = net_collateral * sol_price
        net_profit_usd = collateral_value_usd - debt_amount
        profit_rate = (net_profit_usd / debt_amount *
                       100) if debt_amount > 0 else 0

        result = {
            'signature': 'manual_calculation',
            'datetime': datetime.now().isoformat(),
            'is_liquidation': True,

            'collateral_amount': collateral_amount,
            'collateral_token': 'SOL',
            'debt_amount': debt_amount,
            'debt_token': 'USDC',

            'protocol_fee': protocol_fee,
            'priority_fee': priority_fee,

            'sol_price': sol_price,
            'net_collateral': net_collateral,
            'collateral_value_usd': collateral_value_usd,
            'net_profit_usd': net_profit_usd,
            'profit_rate': profit_rate,
            'is_profitable': net_profit_usd > 0
        }

        if verbose:
            self._print_profit_report(result)

        return result

    def _print_profit_report(self, result: Dict[str, Any]) -> None:
        """打印利润报告"""
        print("\n" + "=" * 70)
        print("📊 清算利润计算报告")
        print("=" * 70)

        # 根据协议费是否已扣除显示不同的计算过程
        total_coll = result.get(
            'total_collateral', result['collateral_amount'])

        print(f"""
📐 计算公式
   净利润 = (净SOL × SOL价格) - 代偿债务USDC
   净SOL  = 总抵押品 - 协议费 - 优先费

📍 Step 1: 计算净 SOL
   总抵押品:  {total_coll:.9f} {result.get('collateral_token', 'SOL')}
   - 协议费:  {result['protocol_fee']:.9f} SOL
   - 优先费:  {result['priority_fee']:.9f} SOL
   ─────────────────────────────────
   净 SOL =   {result['net_collateral']:.9f} SOL

📍 Step 2: 换算成美元
   {result['net_collateral']:.9f} SOL × ${result['sol_price']:.2f} = ${result['collateral_value_usd']:.4f}

📍 Step 3: 计算净利润
   总收入 (USD):    ${result['collateral_value_usd']:.4f}
   - 代偿成本:      ${result['debt_amount']:.6f} {result.get('debt_token', 'USDC')}
   ─────────────────────────────────""")

        if result['is_profitable']:
            print(
                f"""   ✅ 净利润 =     ${result['net_profit_usd']:.4f} USD  (+{result['profit_rate']:.4f}%)""")
        else:
            print(
                f"""   ❌ 净亏损 =     ${abs(result['net_profit_usd']):.4f} USD  ({result['profit_rate']:.4f}%)""")

        print("\n" + "=" * 70)


# ============================================================================
# 主函数
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Kamino 清算分析器 - 一站式分析工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 分析交易
  python kamino_liquidation_analyzer.py 5gcWCkHxdYKJCHxrADX8KKkRN6cfwh4XkUDpH4QJdKtiatZdCGeceAFHdwDK7TqxSt2JBBykFu5c6gxcAZEN9ZFg
  
  # 指定 SOL 价格和协议费
  python kamino_liquidation_analyzer.py <signature> --sol-price 204.92 --protocol-fee 0.0013
  
  # 手动计算
  python kamino_liquidation_analyzer.py --manual \\
      --collateral 0.05479 \\
      --protocol-fee 0.0013 \\
      --priority-fee 0.001317 \\
      --debt 10.642 \\
      --sol-price 204.92
        """
    )

    parser.add_argument(
        'signature',
        nargs='?',
        help='交易签名'
    )
    parser.add_argument(
        '--sol-price',
        type=float,
        help='SOL 价格 (USD)'
    )
    parser.add_argument(
        '--protocol-fee',
        type=float,
        help='协议费 (SOL)'
    )
    parser.add_argument(
        '--output', '-o',
        help='输出文件路径'
    )

    # 手动计算模式
    parser.add_argument(
        '--manual',
        action='store_true',
        help='手动计算模式'
    )
    parser.add_argument(
        '--collateral',
        type=float,
        help='清算所得 (SOL)'
    )
    parser.add_argument(
        '--priority-fee',
        type=float,
        help='优先费 (SOL)'
    )
    parser.add_argument(
        '--debt',
        type=float,
        help='代偿债务 (USDC)'
    )

    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='安静模式'
    )

    args = parser.parse_args()

    try:
        analyzer = KaminoLiquidationAnalyzer(sol_price=args.sol_price)

        if args.manual:
            # 手动计算模式
            if not all([args.collateral, args.protocol_fee is not None,
                       args.priority_fee is not None, args.debt, args.sol_price]):
                print("手动计算模式需要以下参数:")
                print(
                    "  --collateral, --protocol-fee, --priority-fee, --debt, --sol-price")
                sys.exit(1)

            result = analyzer.calculate_manual(
                collateral_amount=args.collateral,
                protocol_fee=args.protocol_fee,
                priority_fee=args.priority_fee,
                debt_amount=args.debt,
                sol_price=args.sol_price,
                verbose=not args.quiet
            )
        else:
            # 交易分析模式
            if not args.signature:
                parser.print_help()
                sys.exit(1)

            result = analyzer.analyze_transaction(
                args.signature,
                sol_price=args.sol_price,
                protocol_fee=args.protocol_fee,
                verbose=not args.quiet
            )

        # 保存结果
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            if not args.quiet:
                print(f"\n✓ 结果已保存到: {args.output}")

        if args.quiet:
            print(f"净利润: ${result['net_profit_usd']:.4f} USD")

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
