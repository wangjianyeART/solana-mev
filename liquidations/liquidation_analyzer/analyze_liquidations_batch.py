#!/usr/bin/env python3
"""
Kamino 清算交易批量分析（优化版）

优化特点：
1. 跳过失败交易（meta.err 不为空），统计失败数量
2. 只解析清算相关的指令
3. 使用价格缓存，同一分钟只获取一次价格
4. 计算每笔清算的成本和利润
5. 输出清算占比和利润统计

使用方法：
    python analyze_liquidations_batch.py --limit 1000
"""

import os
import sys
import json
import time
import hashlib
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

from kamino_decoder import KaminoDecoder, KAMINO_LENDING_PROGRAM_ID, base58_decode


# ============================================================================
# 配置
# ============================================================================

def load_env():
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()


def get_helius_api_key() -> str:
    load_env()
    api_key = os.environ.get('HELIUS_API_KEY')
    if not api_key:
        raise ValueError("未找到 HELIUS_API_KEY")
    return api_key


# ============================================================================
# 清算指令 Discriminators
# ============================================================================

def compute_discriminator(name: str) -> str:
    """计算 Anchor 指令的 discriminator"""
    preimage = f"global:{name}".encode()
    return hashlib.sha256(preimage).digest()[:8].hex()


# 计算清算相关的 discriminators（camelCase 和 snake_case 两种形式）
LIQUIDATION_DISCRIMINATORS = {
    compute_discriminator('liquidateObligationAndRedeemReserveCollateral'),
    compute_discriminator('liquidateObligationAndRedeemReserveCollateralV2'),
    compute_discriminator(
        'liquidate_obligation_and_redeem_reserve_collateral'),
    compute_discriminator(
        'liquidate_obligation_and_redeem_reserve_collateral_v2'),
}


# ============================================================================
# 已知 Token
# ============================================================================

KNOWN_TOKENS = {
    "So11111111111111111111111111111111111111112": {"symbol": "SOL", "decimals": 9},
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {"symbol": "USDC", "decimals": 6},
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {"symbol": "USDT", "decimals": 6},
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": {"symbol": "mSOL", "decimals": 9},
    "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn": {"symbol": "JitoSOL", "decimals": 9},
    "bSo13r4TkiE4KumL71LsHTPpL2euBYLFx6h9HP3piy1": {"symbol": "bSOL", "decimals": 9},
}


def get_token_symbol(mint: str) -> str:
    return KNOWN_TOKENS.get(mint, {}).get('symbol', mint[:8])


# ============================================================================
# 价格缓存
# ============================================================================

class PriceCache:
    """价格缓存 - 同一分钟只获取一次价格"""

    def __init__(self):
        self._cache: Dict[int, float] = {}
        self._fetch_count = 0
        self._cache_hits = 0

    def get_price(self, timestamp: int) -> Optional[float]:
        """获取指定时间戳的 SOL 价格（带缓存）"""
        minute = (timestamp // 60) * 60

        if minute in self._cache:
            self._cache_hits += 1
            return self._cache[minute]

        price = self._fetch_binance_price(minute)
        if price:
            self._cache[minute] = price
            self._fetch_count += 1

        return price

    def _fetch_binance_price(self, minute_timestamp: int) -> Optional[float]:
        """从 Binance 获取历史价格"""
        try:
            start_time = minute_timestamp * 1000
            url = f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=1m&startTime={start_time}&limit=1"

            request = urllib.request.Request(
                url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                klines = json.loads(response.read().decode('utf-8'))

            if klines:
                return float(klines[0][4])
        except:
            pass
        return None

    def get_stats(self) -> Dict[str, int]:
        return {
            'cache_size': len(self._cache),
            'fetch_count': self._fetch_count,
            'cache_hits': self._cache_hits
        }


# ============================================================================
# Helius 客户端
# ============================================================================

class HeliusClient:
    def __init__(self):
        self.api_key = get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    def _make_rpc_request(self, method: str, params: list, retries: int = 3) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1,
                   "method": method, "params": params}
        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}

        for attempt in range(retries):
            try:
                request = urllib.request.Request(
                    self.rpc_url, data=data, headers=headers, method='POST')
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    if 'error' in result:
                        raise Exception(f"RPC 错误: {result['error']}")
                    return result.get('result')
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(0.5)
                else:
                    raise

    def get_signatures_for_address(self, address: str, limit: int = 1000, before: Optional[str] = None) -> List[Dict]:
        params = [address, {"limit": min(limit, 1000)}]
        if before:
            params[1]["before"] = before
        return self._make_rpc_request("getSignaturesForAddress", params)

    def get_transaction(self, signature: str) -> Optional[Dict]:
        params = [signature, {"encoding": "jsonParsed",
                              "maxSupportedTransactionVersion": 0}]
        return self._make_rpc_request("getTransaction", params)


# ============================================================================
# 清算数据
# ============================================================================

@dataclass
class LiquidationResult:
    """清算分析结果"""
    signature: str
    slot: int
    blockTime: int
    datetime_str: str

    liquidator: str
    obligation_owner: str
    debt_amount: float
    debt_token: str
    total_collateral: float
    received_collateral: float
    collateral_token: str

    protocol_fee: float
    priority_fee: float
    transaction_fee: float

    sol_price: float
    net_collateral: float
    collateral_value_usd: float
    net_profit_usd: float
    profit_rate: float
    is_profitable: bool


# ============================================================================
# 清算分析器
# ============================================================================

class LiquidationAnalyzer:
    """清算交易分析器"""

    def __init__(self):
        self.price_cache = PriceCache()

    def is_liquidation_instruction(self, data: str) -> bool:
        """快速检测是否为清算指令"""
        if not data or len(data) < 11:
            return False

        try:
            data_bytes = base58_decode(data)
            if len(data_bytes) < 8:
                return False
            discriminator = data_bytes[:8].hex()
            return discriminator in LIQUIDATION_DISCRIMINATORS
        except:
            return False

    def has_liquidation_instruction(self, tx: Dict, accounts: List[str]) -> bool:
        """检查交易是否包含清算指令"""
        message = tx.get('transaction', {}).get('message', {})
        instructions = message.get('instructions', [])

        for instr in instructions:
            program_id = instr.get('programId', '')
            if not program_id and 'programIdIndex' in instr:
                idx = instr['programIdIndex']
                if idx < len(accounts):
                    program_id = accounts[idx]

            if program_id == KAMINO_LENDING_PROGRAM_ID:
                data = instr.get('data', '')
                if self.is_liquidation_instruction(data):
                    return True

        return False

    def analyze_liquidation(self, tx: Dict, signature: str, slot: int, block_time: int) -> Optional[LiquidationResult]:
        """分析清算交易并计算利润"""

        meta = tx.get('meta', {})
        transaction = tx.get('transaction', {})
        message = transaction.get('message', {})

        account_keys = message.get('accountKeys', [])
        accounts = []
        for acc in account_keys:
            if isinstance(acc, dict):
                accounts.append(acc.get('pubkey', ''))
            else:
                accounts.append(acc)

        pre_token = meta.get('preTokenBalances', [])
        post_token = meta.get('postTokenBalances', [])

        token_changes = self._parse_token_changes(
            pre_token, post_token, accounts)

        # 提取清算数据
        debt_amount = 0.0
        debt_token = ''
        received_collateral = 0.0
        total_collateral = 0.0
        collateral_token = ''
        liquidator = ''
        obligation_owner = ''

        # 找清算人（支付 USDC/USDT 的人）
        for tc in token_changes:
            if tc['change'] < 0 and tc['symbol'] in ['USDC', 'USDT']:
                debt_amount = abs(tc['change'])
                debt_token = tc['symbol']
                liquidator = tc['owner']
                break

        # 找被清算方（SOL 类代币减少的人，且不是清算人）
        for tc in token_changes:
            if tc['change'] < -0.01 and tc['symbol'] in ['SOL', 'mSOL', 'JitoSOL', 'stSOL', 'bSOL']:
                if tc['owner'] != liquidator:
                    obligation_owner = tc['owner']
                    total_collateral = abs(tc['change'])
                    collateral_token = tc['symbol']
                    break

        # 找清算人收到的抵押品
        if liquidator:
            for tc in token_changes:
                if tc['owner'] == liquidator and tc['change'] > 0:
                    if tc['symbol'] in ['SOL', 'mSOL', 'JitoSOL', 'stSOL', 'bSOL']:
                        received_collateral = tc['change']
                        if not collateral_token:
                            collateral_token = tc['symbol']
                        break

        if debt_amount == 0 or (total_collateral == 0 and received_collateral == 0):
            return None

        # 找协议费
        protocol_fee = 0.0
        if obligation_owner:
            for tc in token_changes:
                if tc['owner'] == obligation_owner:
                    if 0 < tc['change'] < 0.01 and tc['symbol'] in ['SOL', 'mSOL', 'JitoSOL']:
                        protocol_fee = tc['change']
                        break

        # 费用
        tx_fee = meta.get('fee', 0) / 1e9
        base_fee = 5000 / 1e9
        priority_fee = max(0, tx_fee - base_fee)

        calc_collateral = total_collateral if total_collateral > 0 else (
            received_collateral + protocol_fee)

        # 获取价格
        sol_price = self.price_cache.get_price(block_time) or 0

        # 计算利润
        net_collateral = calc_collateral - protocol_fee - priority_fee
        collateral_value_usd = net_collateral * sol_price
        net_profit_usd = collateral_value_usd - debt_amount
        profit_rate = (net_profit_usd / debt_amount *
                       100) if debt_amount > 0 else 0

        return LiquidationResult(
            signature=signature, slot=slot, blockTime=block_time,
            datetime_str=datetime.fromtimestamp(
                block_time).isoformat() if block_time else '',
            liquidator=liquidator, obligation_owner=obligation_owner,
            debt_amount=debt_amount, debt_token=debt_token,
            total_collateral=total_collateral, received_collateral=received_collateral,
            collateral_token=collateral_token,
            protocol_fee=protocol_fee, priority_fee=priority_fee, transaction_fee=tx_fee,
            sol_price=sol_price, net_collateral=net_collateral,
            collateral_value_usd=collateral_value_usd, net_profit_usd=net_profit_usd,
            profit_rate=profit_rate, is_profitable=net_profit_usd > 0
        )

    def _parse_token_changes(self, pre_token: List, post_token: List, accounts: List) -> List[Dict]:
        """解析 Token 余额变化"""
        pre_map = {}
        for item in pre_token:
            key = (item.get('accountIndex', -1), item.get('mint', ''))
            amt = item.get('uiTokenAmount', {})
            pre_map[key] = {
                'owner': item.get('owner', ''),
                'mint': item.get('mint', ''),
                'amount': float(amt.get('uiAmount', 0) or 0),
            }

        post_map = {}
        for item in post_token:
            key = (item.get('accountIndex', -1), item.get('mint', ''))
            amt = item.get('uiTokenAmount', {})
            post_map[key] = {
                'owner': item.get('owner', ''),
                'mint': item.get('mint', ''),
                'amount': float(amt.get('uiAmount', 0) or 0),
            }

        changes = []
        all_keys = set(pre_map.keys()) | set(post_map.keys())

        for key in all_keys:
            pre = pre_map.get(key, {'amount': 0, 'owner': '', 'mint': ''})
            post = post_map.get(key, {'amount': 0, 'owner': '', 'mint': ''})
            change = post.get('amount', 0) - pre.get('amount', 0)

            if abs(change) > 1e-10:
                mint = post.get('mint') or pre.get('mint', '')
                changes.append({
                    'owner': post.get('owner') or pre.get('owner', ''),
                    'mint': mint,
                    'symbol': get_token_symbol(mint),
                    'change': change
                })

        return changes


# ============================================================================
# 主程序
# ============================================================================

def analyze_liquidations_batch(limit: int = 1000, output_dir: str = "./data"):
    """批量获取并分析 Kamino 清算交易"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"Kamino 清算交易分析（优化版）- 目标: {limit} 条")
    print("=" * 70)

    client = HeliusClient()
    analyzer = LiquidationAnalyzer()

    # ========================================
    # 步骤 1: 获取交易签名并过滤失败交易
    # ========================================
    print(f"\n📥 步骤 1: 获取交易签名...")

    all_signatures = []
    success_signatures = []
    tx_failed_count = 0
    before = None

    while len(all_signatures) < limit:
        batch_size = min(1000, limit - len(all_signatures))
        sigs = client.get_signatures_for_address(
            KAMINO_LENDING_PROGRAM_ID, limit=batch_size, before=before)

        if not sigs:
            break

        all_signatures.extend(sigs)
        before = sigs[-1]['signature']

        # 立即过滤：err 不为 None 就是失败交易，直接跳过
        for sig in sigs:
            if sig.get('err') is None:
                success_signatures.append(sig)
            else:
                tx_failed_count += 1

        print(
            f"   已获取 {len(all_signatures)} 条, 成功: {len(success_signatures)}, 跳过失败: {tx_failed_count}")

        if len(sigs) < batch_size:
            break

    print(f"   ✓ 总签名数: {len(all_signatures)}")
    print(f"   ✓ 成功交易: {len(success_signatures)} (只获取这些的详情)")
    print(f"   ✓ 失败交易: {tx_failed_count} (已跳过，不获取详情)")

    # ========================================
    # 步骤 2: 只获取成功交易的详情并分析清算
    # ========================================
    print(f"\n📋 步骤 2: 获取并分析清算交易...")

    liquidation_results: List[LiquidationResult] = []
    non_liquidation_count = 0
    fetch_failed_count = 0
    parse_failed_count = 0
    tx_success_count = len(success_signatures)

    for i, sig_info in enumerate(success_signatures, 1):
        signature = sig_info['signature']
        slot = sig_info.get('slot', 0)
        block_time = sig_info.get('blockTime', 0)

        if i % 20 == 0 or i == len(success_signatures):
            print(
                f"   处理进度: {i}/{len(success_signatures)} ({i*100//len(success_signatures)}%) | 清算: {len(liquidation_results)}")

        try:
            tx = client.get_transaction(signature)

            if not tx:
                fetch_failed_count += 1
                continue

            # 获取账户列表
            message = tx.get('transaction', {}).get('message', {})
            account_keys = message.get('accountKeys', [])
            accounts = []
            for acc in account_keys:
                if isinstance(acc, dict):
                    accounts.append(acc.get('pubkey', ''))
                else:
                    accounts.append(acc)

            # 快速检测是否为清算交易
            if not analyzer.has_liquidation_instruction(tx, accounts):
                non_liquidation_count += 1
                continue

            # 分析清算交易
            result = analyzer.analyze_liquidation(
                tx, signature, slot, block_time)

            if result:
                liquidation_results.append(result)
            else:
                parse_failed_count += 1

        except Exception as e:
            fetch_failed_count += 1

        # 速率限制
        if i % 100 == 0:
            time.sleep(0.3)

    print(f"\n   ✓ 成功交易: {tx_success_count}")
    print(f"   ✓ 失败交易: {tx_failed_count} (meta.err 不为空，已跳过)")
    print(f"   ✓ 清算交易: {len(liquidation_results)}")
    print(f"   ✓ 非清算交易: {non_liquidation_count}")
    print(f"   ✓ 获取失败: {fetch_failed_count}")
    print(f"   ✓ 解析失败: {parse_failed_count}")

    # ========================================
    # 步骤 3: 保存清算数据
    # ========================================
    print(f"\n📁 步骤 3: 保存数据...")

    liquidations_file = output_path / f"liquidations_{timestamp}.json"
    liquidations_data = [asdict(r) for r in liquidation_results]
    with open(liquidations_file, 'w') as f:
        json.dump(liquidations_data, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 清算数据: {liquidations_file}")

    # ========================================
    # 步骤 4: 统计分析
    # ========================================
    print(f"\n📊 步骤 4: 统计分析...")

    total_profit = sum(r.net_profit_usd for r in liquidation_results)
    profitable_count = sum(1 for r in liquidation_results if r.is_profitable)

    if liquidation_results:
        avg_profit = total_profit / len(liquidation_results)
        max_profit = max(r.net_profit_usd for r in liquidation_results)
        min_profit = min(r.net_profit_usd for r in liquidation_results)
        max_profit_tx = max(liquidation_results,
                            key=lambda r: r.net_profit_usd)
        min_profit_tx = min(liquidation_results,
                            key=lambda r: r.net_profit_usd)
        total_debt = sum(r.debt_amount for r in liquidation_results)
        total_collateral_value = sum(
            r.collateral_value_usd for r in liquidation_results)
    else:
        avg_profit = max_profit = min_profit = 0
        max_profit_tx = min_profit_tx = None
        total_debt = total_collateral_value = 0

    price_stats = analyzer.price_cache.get_stats()

    liquidation_ratio = len(liquidation_results) / \
        tx_success_count * 100 if tx_success_count else 0

    stats = {
        'timestamp': timestamp,
        'total_signatures': len(all_signatures),
        'tx_success': tx_success_count,
        'tx_failed': tx_failed_count,
        'liquidation_transactions': len(liquidation_results),
        'non_liquidation_transactions': non_liquidation_count,
        'fetch_failed': fetch_failed_count,
        'parse_failed': parse_failed_count,
        'liquidation_ratio_pct': liquidation_ratio,

        'profit_stats': {
            'total_profit_usd': total_profit,
            'avg_profit_usd': avg_profit,
            'max_profit_usd': max_profit,
            'min_profit_usd': min_profit,
            'profitable_count': profitable_count,
            'unprofitable_count': len(liquidation_results) - profitable_count,
            'profitable_ratio_pct': profitable_count / len(liquidation_results) * 100 if liquidation_results else 0,
            'total_debt_usd': total_debt,
            'total_collateral_value_usd': total_collateral_value,
        },

        'price_cache_stats': price_stats,
    }

    stats_file = output_path / f"liquidation_stats_{timestamp}.json"
    with open(stats_file, 'w') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 统计结果: {stats_file}")

    # ========================================
    # 输出报告
    # ========================================
    print("\n" + "=" * 70)
    print("📊 清算分析报告")
    print("=" * 70)

    print(f"""
📌 交易统计:
   总签名数: {len(all_signatures)}
   成功交易: {tx_success_count}
   失败交易: {tx_failed_count} (已跳过)
   
   清算交易: {len(liquidation_results)}
   非清算交易: {non_liquidation_count}
   清算占比: {liquidation_ratio:.2f}%
""")

    if liquidation_results:
        print(f"""📌 利润统计:
   总利润: ${total_profit:.2f} USD
   平均利润: ${avg_profit:.4f} USD
   最高利润: ${max_profit:.4f} USD
   最低利润: ${min_profit:.4f} USD
   
   盈利交易: {profitable_count} ({stats['profit_stats']['profitable_ratio_pct']:.1f}%)
   亏损交易: {len(liquidation_results) - profitable_count}
   
   总代偿债务: ${total_debt:.2f} USD
   总抵押品价值: ${total_collateral_value:.2f} USD
""")

        if max_profit_tx:
            print(f"""📌 最高利润交易:
   签名: {max_profit_tx.signature[:32]}...
   利润: ${max_profit_tx.net_profit_usd:.4f} USD
   代偿: ${max_profit_tx.debt_amount:.2f} {max_profit_tx.debt_token}
   获得: {max_profit_tx.total_collateral:.6f} {max_profit_tx.collateral_token}
""")

    print(f"""📌 性能统计:
   价格缓存大小: {price_stats['cache_size']}
   价格获取次数: {price_stats['fetch_count']}
   缓存命中次数: {price_stats['cache_hits']}

📁 输出文件:
   清算数据: {liquidations_file}
   统计结果: {stats_file}
""")
    print("=" * 70)

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Kamino 清算交易批量分析（优化版）')
    parser.add_argument('--limit', type=int, default=1000,
                        help='获取数量 (默认: 1000)')
    parser.add_argument('--output', '-o', default='./data', help='输出目录')

    args = parser.parse_args()

    try:
        analyze_liquidations_batch(limit=args.limit, output_dir=args.output)
    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
