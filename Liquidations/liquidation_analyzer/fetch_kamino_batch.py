#!/usr/bin/env python3
"""
批量获取 Kamino 历史交易并保存

功能：
1. 获取 Kamino 最近 N 条交易
2. 保存原始交易数据到本地
3. 解析所有交易
4. 保存解析结果到本地
5. 输出统计报告

使用方法：
    python fetch_kamino_batch.py --limit 1000
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from collections import Counter

from kamino_decoder import KaminoDecoder, KAMINO_LENDING_PROGRAM_ID


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
# Helius 客户端
# ============================================================================

class HeliusClient:
    def __init__(self):
        self.api_key = get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
    
    def _make_rpc_request(self, method: str, params: list, retries: int = 3) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        
        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        
        for attempt in range(retries):
            try:
                request = urllib.request.Request(
                    self.rpc_url, data=data, headers=headers, method='POST'
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    if 'error' in result:
                        raise Exception(f"RPC 错误: {result['error']}")
                    return result.get('result')
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(1)
                else:
                    raise
    
    def get_signatures_for_address(self, address: str, limit: int = 1000, before: Optional[str] = None) -> List[Dict]:
        params = [address, {"limit": min(limit, 1000)}]
        if before:
            params[1]["before"] = before
        return self._make_rpc_request("getSignaturesForAddress", params)
    
    def get_transaction(self, signature: str) -> Optional[Dict]:
        params = [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        return self._make_rpc_request("getTransaction", params)


# ============================================================================
# 主程序
# ============================================================================

def fetch_kamino_batch(limit: int = 1000, output_dir: str = "./data"):
    """批量获取并分析 Kamino 交易"""
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print("=" * 70)
    print(f"Kamino 批量交易获取与分析 (目标: {limit} 条)")
    print("=" * 70)
    
    # 初始化
    client = HeliusClient()
    decoder = KaminoDecoder(str(Path(__file__).parent / "kamino_lending_idl.json"))
    
    # ========================================
    # 步骤 1: 获取交易签名
    # ========================================
    print(f"\n📥 步骤 1: 获取交易签名...")
    
    all_signatures = []
    before = None
    
    while len(all_signatures) < limit:
        batch_size = min(1000, limit - len(all_signatures))
        sigs = client.get_signatures_for_address(KAMINO_LENDING_PROGRAM_ID, limit=batch_size, before=before)
        
        if not sigs:
            break
        
        all_signatures.extend(sigs)
        before = sigs[-1]['signature']
        print(f"   已获取 {len(all_signatures)} 条签名...")
        
        if len(sigs) < batch_size:
            break
    
    print(f"   ✓ 共获取 {len(all_signatures)} 条交易签名")
    
    # 保存签名列表
    signatures_file = output_path / f"kamino_signatures_{timestamp}.json"
    with open(signatures_file, 'w') as f:
        json.dump(all_signatures, f, indent=2)
    print(f"   ✓ 签名列表已保存: {signatures_file}")
    
    # ========================================
    # 步骤 2: 获取交易详情并保存原始数据
    # ========================================
    print(f"\n📥 步骤 2: 获取交易详情...")
    
    raw_transactions = []
    failed_signatures = []
    
    for i, sig_info in enumerate(all_signatures, 1):
        signature = sig_info['signature']
        
        if i % 50 == 0 or i == len(all_signatures):
            print(f"   处理进度: {i}/{len(all_signatures)} ({i*100//len(all_signatures)}%)")
        
        try:
            tx = client.get_transaction(signature)
            if tx:
                raw_transactions.append({
                    'signature': signature,
                    'slot': sig_info.get('slot'),
                    'blockTime': sig_info.get('blockTime'),
                    'transaction': tx
                })
            else:
                failed_signatures.append(signature)
        except Exception as e:
            failed_signatures.append(signature)
        
        # 简单的速率限制
        if i % 100 == 0:
            time.sleep(0.5)
    
    print(f"   ✓ 成功获取 {len(raw_transactions)} 条交易")
    if failed_signatures:
        print(f"   ⚠ 失败 {len(failed_signatures)} 条")
    
    # 保存原始交易数据
    raw_file = output_path / f"kamino_raw_{timestamp}.json"
    with open(raw_file, 'w') as f:
        json.dump(raw_transactions, f)
    print(f"   ✓ 原始数据已保存: {raw_file} ({len(raw_transactions)} 条)")
    
    # ========================================
    # 步骤 3: 解析交易
    # ========================================
    print(f"\n📋 步骤 3: 解析交易...")
    
    parsed_transactions = []
    instruction_counter = Counter()
    instruction_type_counter = Counter()
    total_kamino_instructions = 0
    liquidation_count = 0
    unknown_count = 0
    
    for i, raw_tx in enumerate(raw_transactions, 1):
        if i % 100 == 0:
            print(f"   解析进度: {i}/{len(raw_transactions)}")
        
        signature = raw_tx['signature']
        tx = raw_tx['transaction']
        
        meta = tx.get('meta', {})
        transaction = tx.get('transaction', {})
        message = transaction.get('message', {})
        
        # 账户列表
        account_keys = message.get('accountKeys', [])
        accounts = []
        for acc in account_keys:
            if isinstance(acc, dict):
                accounts.append(acc.get('pubkey', ''))
            else:
                accounts.append(acc)
        
        # 解析指令
        instructions = message.get('instructions', [])
        parsed_instructions = []
        is_liquidation = False
        tx_instruction_types = set()
        
        for instr in instructions:
            program_id = instr.get('programId', '')
            if not program_id and 'programIdIndex' in instr:
                idx = instr['programIdIndex']
                if idx < len(accounts):
                    program_id = accounts[idx]
            
            if program_id == KAMINO_LENDING_PROGRAM_ID:
                total_kamino_instructions += 1
                
                data = instr.get('data', '')
                acc_indices = instr.get('accounts', [])
                instr_accounts = []
                for idx in acc_indices:
                    if isinstance(idx, int) and idx < len(accounts):
                        instr_accounts.append(accounts[idx])
                
                parsed = decoder.decode_instruction_data(data, instr_accounts)
                
                if parsed:
                    instruction_counter[parsed.name] += 1
                    instruction_type_counter[parsed.instruction_type] += 1
                    tx_instruction_types.add(parsed.instruction_type)
                    
                    parsed_instructions.append({
                        'name': parsed.name,
                        'type': parsed.instruction_type,
                        'args': parsed.args,
                        'discriminator': parsed.discriminator
                    })
                    
                    if parsed.instruction_type == 'liquidation':
                        is_liquidation = True
                else:
                    unknown_count += 1
                    instruction_counter['unknown'] += 1
                    instruction_type_counter['unknown'] += 1
                    parsed_instructions.append({
                        'name': 'unknown',
                        'type': 'unknown',
                        'raw_data': data[:32] if data else ''
                    })
        
        if is_liquidation:
            liquidation_count += 1
        
        parsed_transactions.append({
            'signature': signature,
            'slot': raw_tx.get('slot'),
            'blockTime': raw_tx.get('blockTime'),
            'datetime': datetime.fromtimestamp(raw_tx.get('blockTime', 0)).isoformat() if raw_tx.get('blockTime') else '',
            'success': not meta.get('err'),
            'fee': meta.get('fee', 0),
            'is_liquidation': is_liquidation,
            'instruction_types': list(tx_instruction_types),
            'kamino_instructions': parsed_instructions,
            'kamino_instruction_count': len(parsed_instructions)
        })
    
    # 保存解析结果
    parsed_file = output_path / f"kamino_parsed_{timestamp}.json"
    with open(parsed_file, 'w') as f:
        json.dump(parsed_transactions, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 解析结果已保存: {parsed_file}")
    
    # ========================================
    # 步骤 4: 统计分析
    # ========================================
    print(f"\n📊 步骤 4: 统计分析...")
    
    stats = {
        'timestamp': timestamp,
        'total_signatures': len(all_signatures),
        'successful_fetches': len(raw_transactions),
        'failed_fetches': len(failed_signatures),
        'total_kamino_instructions': total_kamino_instructions,
        'unique_instruction_names': len(instruction_counter),
        'unique_instruction_types': len(instruction_type_counter),
        'liquidation_transactions': liquidation_count,
        'unknown_instructions': unknown_count,
        'instruction_counter': dict(instruction_counter.most_common()),
        'instruction_type_counter': dict(instruction_type_counter.most_common())
    }
    
    # 保存统计结果
    stats_file = output_path / f"kamino_stats_{timestamp}.json"
    with open(stats_file, 'w') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 统计结果已保存: {stats_file}")
    
    # ========================================
    # 输出统计报告
    # ========================================
    print("\n" + "=" * 70)
    print("📊 统计报告")
    print("=" * 70)
    
    print(f"""
📌 数据获取:
   目标数量: {limit}
   获取签名: {len(all_signatures)}
   成功获取交易: {len(raw_transactions)}
   获取失败: {len(failed_signatures)}

📌 Kamino 指令统计:
   总指令数: {total_kamino_instructions}
   不同指令名称数: {len(instruction_counter)}
   未知指令数: {unknown_count}
   清算交易数: {liquidation_count}
""")
    
    print("📌 指令类型分布:")
    for instr_type, count in instruction_type_counter.most_common():
        pct = count / total_kamino_instructions * 100 if total_kamino_instructions > 0 else 0
        print(f"   {instr_type:<15} : {count:>6} ({pct:>5.1f}%)")
    
    print("\n📌 具体指令分布 (Top 25):")
    for name, count in instruction_counter.most_common(25):
        pct = count / total_kamino_instructions * 100 if total_kamino_instructions > 0 else 0
        print(f"   {name:<55} : {count:>6} ({pct:>5.1f}%)")
    
    if len(instruction_counter) > 25:
        print(f"   ... 还有 {len(instruction_counter) - 25} 种其他指令")
    
    print(f"""
📁 输出文件:
   签名列表: {signatures_file}
   原始数据: {raw_file}
   解析结果: {parsed_file}
   统计结果: {stats_file}
""")
    print("=" * 70)
    
    return stats


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='批量获取 Kamino 交易')
    parser.add_argument('--limit', type=int, default=1000, help='获取数量 (默认: 1000)')
    parser.add_argument('--output', '-o', default='./data', help='输出目录')
    
    args = parser.parse_args()
    
    try:
        fetch_kamino_batch(limit=args.limit, output_dir=args.output)
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
