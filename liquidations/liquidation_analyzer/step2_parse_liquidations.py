#!/usr/bin/env python3
"""
步骤 2: 解析清算交易

功能：
1. 读取步骤 1 保存的原始交易数据
2. 只解析包含清算指令的交易
3. 提取清算相关的详细信息
4. 保存解析后的数据到本地

使用方法：
    python step2_parse_liquidations.py <原始数据文件>
    python step2_parse_liquidations.py ./data/kamino_raw_20260130_xxx.json
"""

import os
import sys
import json
import hashlib
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

from kamino_decoder import base58_decode, KAMINO_LENDING_PROGRAM_ID


# ============================================================================
# 清算指令 Discriminators
# ============================================================================

def compute_discriminator(name: str) -> str:
    """计算 Anchor 指令的 discriminator"""
    preimage = f"global:{name}".encode()
    return hashlib.sha256(preimage).digest()[:8].hex()


# 清算相关指令的 discriminators（camelCase 和 snake_case）
LIQUIDATION_DISCRIMINATORS = {
    compute_discriminator('liquidateObligationAndRedeemReserveCollateral'),
    compute_discriminator('liquidateObligationAndRedeemReserveCollateralV2'),
    compute_discriminator('liquidate_obligation_and_redeem_reserve_collateral'),
    compute_discriminator('liquidate_obligation_and_redeem_reserve_collateral_v2'),
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
    "7dHbWXmci3dT8UFYWYZweBLXgycu7Y3iL6trKn1Y7ARj": {"symbol": "stSOL", "decimals": 9},
}

def get_token_symbol(mint: str) -> str:
    return KNOWN_TOKENS.get(mint, {}).get('symbol', mint[:8] + '...')


# ============================================================================
# 价格缓存
# ============================================================================

class PriceCache:
    """价格缓存 - 同一分钟只获取一次"""
    
    def __init__(self):
        self._cache: Dict[int, float] = {}
        self._fetch_count = 0
        self._cache_hits = 0
    
    def get_price(self, timestamp: int) -> Optional[float]:
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
        try:
            start_time = minute_timestamp * 1000
            url = f"https://api.binance.com/api/v3/klines?symbol=SOLUSDT&interval=1m&startTime={start_time}&limit=1"
            request = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(request, timeout=10) as response:
                klines = json.loads(response.read().decode('utf-8'))
            if klines:
                return float(klines[0][4])  # 收盘价
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
# 清算数据结构
# ============================================================================

@dataclass
class LiquidationData:
    """清算交易数据"""
    signature: str
    slot: int
    blockTime: int
    datetime_str: str
    
    # 清算参与方
    liquidator: str
    obligation_owner: str
    
    # 清算金额
    debt_amount: float
    debt_token: str
    total_collateral: float
    received_collateral: float
    collateral_token: str
    
    # 费用
    protocol_fee: float
    priority_fee: float
    transaction_fee: float
    
    # 利润计算
    sol_price: float
    net_collateral: float
    collateral_value_usd: float
    net_profit_usd: float
    profit_rate: float
    is_profitable: bool


# ============================================================================
# 解析器
# ============================================================================

class LiquidationParser:
    """清算交易解析器"""
    
    def __init__(self):
        self.price_cache = PriceCache()
    
    def is_liquidation_instruction(self, data: str) -> bool:
        """检测是否为清算指令"""
        if not data or len(data) < 11:
            return False
        try:
            data_bytes = base58_decode(data)
            if len(data_bytes) < 8:
                return False
            disc = data_bytes[:8].hex()
            return disc in LIQUIDATION_DISCRIMINATORS
        except:
            return False
    
    def has_liquidation(self, tx: Dict) -> bool:
        """检查交易是否包含清算指令"""
        message = tx.get('transaction', {}).get('message', {})
        
        # 获取账户列表
        account_keys = message.get('accountKeys', [])
        accounts = []
        for acc in account_keys:
            if isinstance(acc, dict):
                accounts.append(acc.get('pubkey', ''))
            else:
                accounts.append(acc)
        
        # 检查指令
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
    
    def parse_liquidation(self, raw_tx: Dict) -> Optional[LiquidationData]:
        """解析清算交易"""
        signature = raw_tx['signature']
        slot = raw_tx.get('slot', 0)
        block_time = raw_tx.get('blockTime', 0)
        tx = raw_tx['transaction']
        
        meta = tx.get('meta', {})
        message = tx.get('transaction', {}).get('message', {})
        
        # 获取账户列表
        account_keys = message.get('accountKeys', [])
        accounts = []
        for acc in account_keys:
            if isinstance(acc, dict):
                accounts.append(acc.get('pubkey', ''))
            else:
                accounts.append(acc)
        
        # 解析 Token 余额变化
        pre_token = meta.get('preTokenBalances', [])
        post_token = meta.get('postTokenBalances', [])
        token_changes = self._parse_token_changes(pre_token, post_token)
        
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
        
        # 找被清算方（SOL 类减少的人，且不是清算人）
        sol_tokens = ['SOL', 'mSOL', 'JitoSOL', 'stSOL', 'bSOL']
        for tc in token_changes:
            if tc['change'] < -0.001 and tc['symbol'] in sol_tokens:
                if tc['owner'] != liquidator:
                    obligation_owner = tc['owner']
                    total_collateral = abs(tc['change'])
                    collateral_token = tc['symbol']
                    break
        
        # 找清算人收到的抵押品
        if liquidator:
            for tc in token_changes:
                if tc['owner'] == liquidator and tc['change'] > 0 and tc['symbol'] in sol_tokens:
                    received_collateral = tc['change']
                    if not collateral_token:
                        collateral_token = tc['symbol']
                    break
        
        # 如果没有关键数据，返回 None
        if debt_amount == 0 or (total_collateral == 0 and received_collateral == 0):
            return None
        
        # 找协议费（被清算方收到的小额返还）
        protocol_fee = 0.0
        if obligation_owner:
            for tc in token_changes:
                if tc['owner'] == obligation_owner and 0 < tc['change'] < 0.01 and tc['symbol'] in sol_tokens:
                    protocol_fee = tc['change']
                    break
        
        # 费用计算
        tx_fee = meta.get('fee', 0) / 1e9
        base_fee = 5000 / 1e9
        priority_fee = max(0, tx_fee - base_fee)
        
        # 计算用的抵押品数量
        calc_collateral = total_collateral if total_collateral > 0 else (received_collateral + protocol_fee)
        
        # 获取 SOL 价格
        sol_price = self.price_cache.get_price(block_time) if block_time else None
        sol_price = sol_price or 0
        
        # 计算利润
        net_collateral = calc_collateral - protocol_fee - priority_fee
        collateral_value_usd = net_collateral * sol_price
        net_profit_usd = collateral_value_usd - debt_amount
        profit_rate = (net_profit_usd / debt_amount * 100) if debt_amount > 0 else 0
        
        return LiquidationData(
            signature=signature,
            slot=slot,
            blockTime=block_time,
            datetime_str=datetime.fromtimestamp(block_time).isoformat() if block_time else '',
            liquidator=liquidator,
            obligation_owner=obligation_owner,
            debt_amount=debt_amount,
            debt_token=debt_token,
            total_collateral=total_collateral,
            received_collateral=received_collateral,
            collateral_token=collateral_token,
            protocol_fee=protocol_fee,
            priority_fee=priority_fee,
            transaction_fee=tx_fee,
            sol_price=sol_price,
            net_collateral=net_collateral,
            collateral_value_usd=collateral_value_usd,
            net_profit_usd=net_profit_usd,
            profit_rate=profit_rate,
            is_profitable=net_profit_usd > 0
        )
    
    def _parse_token_changes(self, pre_token: List, post_token: List) -> List[Dict]:
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

def parse_liquidations(input_file: str, output_dir: Optional[str] = None):
    """解析清算交易"""
    
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"文件不存在: {input_file}")
    
    # 输出目录
    if output_dir:
        output_path = Path(output_dir)
    else:
        output_path = input_path.parent
    output_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print("=" * 70)
    print("步骤 2: 解析清算交易")
    print("=" * 70)
    print(f"\n输入文件: {input_file}")
    
    # ========================================
    # 1. 读取原始数据
    # ========================================
    print(f"\n📂 读取原始数据...")
    
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    metadata = data.get('metadata', {})
    transactions = data.get('transactions', [])
    
    print(f"   ✓ 交易数量: {len(transactions)}")
    print(f"   ✓ 原始统计: 总签名 {metadata.get('total_signatures', 'N/A')}, "
          f"成功 {metadata.get('success_signatures', 'N/A')}, "
          f"失败 {metadata.get('failed_signatures', 'N/A')}")
    
    # ========================================
    # 2. 筛选并解析清算交易
    # ========================================
    print(f"\n📋 解析清算交易...")
    
    parser = LiquidationParser()
    
    liquidation_count = 0
    non_liquidation_count = 0
    parse_failed_count = 0
    liquidation_results: List[LiquidationData] = []
    
    for i, raw_tx in enumerate(transactions, 1):
        if i % 50 == 0 or i == len(transactions):
            print(f"   处理进度: {i}/{len(transactions)} ({i*100//len(transactions)}%) | 清算: {liquidation_count}")
        
        tx = raw_tx.get('transaction', {})
        
        # 检查是否包含清算指令
        if not parser.has_liquidation(tx):
            non_liquidation_count += 1
            continue
        
        liquidation_count += 1
        
        # 解析清算详情
        result = parser.parse_liquidation(raw_tx)
        if result:
            liquidation_results.append(result)
        else:
            parse_failed_count += 1
    
    print(f"\n   ✓ 清算交易: {liquidation_count}")
    print(f"   ✓ 非清算交易: {non_liquidation_count}")
    print(f"   ✓ 解析成功: {len(liquidation_results)}")
    print(f"   ✓ 解析失败: {parse_failed_count}")
    
    # ========================================
    # 3. 统计利润
    # ========================================
    if liquidation_results:
        total_profit = sum(r.net_profit_usd for r in liquidation_results)
        avg_profit = total_profit / len(liquidation_results)
        max_profit = max(r.net_profit_usd for r in liquidation_results)
        min_profit = min(r.net_profit_usd for r in liquidation_results)
        profitable_count = sum(1 for r in liquidation_results if r.is_profitable)
        total_debt = sum(r.debt_amount for r in liquidation_results)
        total_collateral_value = sum(r.collateral_value_usd for r in liquidation_results)
    else:
        total_profit = avg_profit = max_profit = min_profit = 0
        profitable_count = 0
        total_debt = total_collateral_value = 0
    
    # ========================================
    # 4. 保存解析结果
    # ========================================
    print(f"\n📁 保存解析结果...")
    
    # 清算数据
    liquidations_file = output_path / f"liquidations_parsed_{timestamp}.json"
    liquidations_data = [asdict(r) for r in liquidation_results]
    with open(liquidations_file, 'w', encoding='utf-8') as f:
        json.dump(liquidations_data, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 清算数据: {liquidations_file}")
    
    # 统计数据
    price_stats = parser.price_cache.get_stats()
    
    stats = {
        'timestamp': timestamp,
        'input_file': str(input_file),
        'total_transactions': len(transactions),
        'liquidation_transactions': liquidation_count,
        'non_liquidation_transactions': non_liquidation_count,
        'parsed_successfully': len(liquidation_results),
        'parse_failed': parse_failed_count,
        'liquidation_ratio_pct': liquidation_count / len(transactions) * 100 if transactions else 0,
        
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
    
    stats_file = output_path / f"liquidations_stats_{timestamp}.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 统计数据: {stats_file}")
    
    # ========================================
    # 5. 输出报告
    # ========================================
    print("\n" + "=" * 70)
    print("📊 清算分析报告")
    print("=" * 70)
    
    print(f"""
📌 交易统计:
   总交易数: {len(transactions)}
   清算交易: {liquidation_count} ({stats['liquidation_ratio_pct']:.2f}%)
   非清算交易: {non_liquidation_count}
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
        
        # 显示最高利润交易
        if liquidation_results:
            best = max(liquidation_results, key=lambda r: r.net_profit_usd)
            print(f"""📌 最高利润交易:
   签名: {best.signature[:40]}...
   利润: ${best.net_profit_usd:.4f} USD
   代偿: ${best.debt_amount:.2f} {best.debt_token}
   获得: {best.total_collateral:.6f} {best.collateral_token}
   SOL价格: ${best.sol_price:.2f}
""")
    
    print(f"""📌 价格缓存统计:
   缓存大小: {price_stats['cache_size']}
   获取次数: {price_stats['fetch_count']}
   命中次数: {price_stats['cache_hits']}

📁 输出文件:
   清算数据: {liquidations_file}
   统计数据: {stats_file}
""")
    print("=" * 70)
    
    return stats


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='解析 Kamino 清算交易')
    parser.add_argument('input_file', help='原始交易数据文件 (步骤1的输出)')
    parser.add_argument('--output', '-o', help='输出目录 (默认: 与输入文件相同)')
    
    args = parser.parse_args()
    
    try:
        parse_liquidations(args.input_file, args.output)
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
