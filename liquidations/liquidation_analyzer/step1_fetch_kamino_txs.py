#!/usr/bin/env python3
"""
步骤 1: 获取 Kamino 交易

功能：
1. 获取 Kamino 最近 N 条交易签名
2. 在签名阶段直接排除失败交易（err 不为 None）
3. 只获取成功交易的详情
4. 保存原始交易数据到本地

使用方法：
    python step1_fetch_kamino_txs.py --limit 1000
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


# ============================================================================
# 配置
# ============================================================================

KAMINO_LENDING_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"


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
        raise ValueError("未找到 HELIUS_API_KEY，请在 .env 文件中配置")
    return api_key


# ============================================================================
# Helius 客户端
# ============================================================================

class HeliusClient:
    def __init__(self):
        self.api_key = get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
    
    def _make_rpc_request(self, method: str, params: list, retries: int = 3) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
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
                    time.sleep(0.5)
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

def fetch_kamino_transactions(limit: int = 1000, output_dir: str = "./data"):
    """获取 Kamino 交易并保存"""
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print("=" * 70)
    print(f"步骤 1: 获取 Kamino 交易 (目标: {limit} 条)")
    print("=" * 70)
    
    client = HeliusClient()
    
    # ========================================
    # 1. 获取签名并过滤失败交易
    # ========================================
    print(f"\n📥 获取交易签名...")
    
    all_signatures = []
    success_signatures = []
    failed_count = 0
    before = None
    
    while len(all_signatures) < limit:
        batch_size = min(1000, limit - len(all_signatures))
        sigs = client.get_signatures_for_address(
            KAMINO_LENDING_PROGRAM_ID, limit=batch_size, before=before
        )
        
        if not sigs:
            break
        
        all_signatures.extend(sigs)
        before = sigs[-1]['signature']
        
        # 过滤：err 为 None 的才是成功交易
        for sig in sigs:
            if sig.get('err') is None:
                success_signatures.append(sig)
            else:
                failed_count += 1
        
        print(f"   已获取 {len(all_signatures)} 条, 成功: {len(success_signatures)}, 失败: {failed_count}")
        
        if len(sigs) < batch_size:
            break
    
    print(f"\n   ✓ 总签名数: {len(all_signatures)}")
    print(f"   ✓ 成功交易: {len(success_signatures)}")
    print(f"   ✓ 失败交易: {failed_count} (已排除)")
    
    # ========================================
    # 2. 获取成功交易的详情
    # ========================================
    print(f"\n📥 获取交易详情 ({len(success_signatures)} 条)...")
    
    raw_transactions = []
    fetch_failed = 0
    
    for i, sig_info in enumerate(success_signatures, 1):
        signature = sig_info['signature']
        
        if i % 20 == 0 or i == len(success_signatures):
            print(f"   处理进度: {i}/{len(success_signatures)} ({i*100//len(success_signatures)}%)")
        
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
                fetch_failed += 1
        except Exception as e:
            fetch_failed += 1
        
        # 速率限制
        if i % 50 == 0:
            time.sleep(0.2)
    
    print(f"\n   ✓ 成功获取: {len(raw_transactions)}")
    if fetch_failed:
        print(f"   ⚠ 获取失败: {fetch_failed}")
    
    # ========================================
    # 3. 保存原始数据
    # ========================================
    print(f"\n📁 保存原始数据...")
    
    output_file = output_path / f"kamino_raw_{timestamp}.json"
    
    save_data = {
        'metadata': {
            'timestamp': timestamp,
            'total_signatures': len(all_signatures),
            'success_signatures': len(success_signatures),
            'failed_signatures': failed_count,
            'fetched_transactions': len(raw_transactions),
            'fetch_failed': fetch_failed,
        },
        'transactions': raw_transactions
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False)
    
    file_size = output_file.stat().st_size / 1024 / 1024
    print(f"   ✓ 已保存: {output_file}")
    print(f"   ✓ 文件大小: {file_size:.2f} MB")
    print(f"   ✓ 交易数量: {len(raw_transactions)}")
    
    print("\n" + "=" * 70)
    print("✓ 步骤 1 完成！")
    print(f"  下一步: python step2_parse_liquidations.py {output_file}")
    print("=" * 70)
    
    return str(output_file)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='获取 Kamino 交易')
    parser.add_argument('--limit', type=int, default=1000, help='获取数量 (默认: 1000)')
    parser.add_argument('--output', '-o', default='./data', help='输出目录')
    
    args = parser.parse_args()
    
    try:
        fetch_kamino_transactions(limit=args.limit, output_dir=args.output)
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
