#!/usr/bin/env python3
"""
获取 Kamino 历史交易并统计指令类型

功能：
1. 使用 Helius API 获取 Kamino 最近 N 条交易
2. 解析每笔交易的 Kamino 指令
3. 统计指令类型分布

使用方法：
    python fetch_kamino_history.py [--limit 100]
"""

import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from collections import Counter

# 导入解码器
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


def get_helius_api_key() -> str:
    load_env()
    api_key = os.environ.get('HELIUS_API_KEY')
    if not api_key:
        raise ValueError("未找到 HELIUS_API_KEY，请在 .env 文件中配置")
    return api_key


# ============================================================================
# Helius API 客户端
# ============================================================================

class HeliusClient:
    """Helius API 客户端"""

    def __init__(self):
        self.api_key = get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    def _make_rpc_request(self, method: str, params: list) -> Any:
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

    def get_signatures_for_address(
        self,
        address: str,
        limit: int = 100,
        before: Optional[str] = None
    ) -> List[Dict]:
        """获取地址的交易签名列表"""
        params = [
            address,
            {"limit": limit}
        ]
        if before:
            params[1]["before"] = before

        return self._make_rpc_request("getSignaturesForAddress", params)

    def get_transaction(self, signature: str) -> Optional[Dict]:
        """获取交易详情"""
        params = [
            signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
        ]
        return self._make_rpc_request("getTransaction", params)


# ============================================================================
# 主程序
# ============================================================================

def fetch_and_analyze_kamino_history(limit: int = 100, verbose: bool = True):
    """
    获取并分析 Kamino 历史交易

    Args:
        limit: 获取的交易数量
        verbose: 是否打印详细信息
    """
    print("=" * 70)
    print("Kamino 历史交易分析")
    print("=" * 70)

    # 初始化
    client = HeliusClient()
    decoder = KaminoDecoder(
        str(Path(__file__).parent / "kamino_lending_idl.json"))

    # 获取交易签名
    print(f"\n📥 正在获取 Kamino 最近 {limit} 条交易签名...")
    signatures_data = client.get_signatures_for_address(
        KAMINO_LENDING_PROGRAM_ID, limit=limit)

    if not signatures_data:
        print("❌ 未获取到交易签名")
        return

    print(f"   ✓ 获取到 {len(signatures_data)} 条交易签名")

    # 统计变量
    instruction_counter = Counter()
    instruction_type_counter = Counter()
    tx_type_counter = Counter()
    total_kamino_instructions = 0
    successful_txs = 0
    failed_txs = 0
    liquidation_count = 0

    # 解析每笔交易
    print(f"\n📋 正在解析交易...")

    for i, sig_info in enumerate(signatures_data, 1):
        signature = sig_info['signature']

        if verbose and i % 10 == 0:
            print(f"   处理进度: {i}/{len(signatures_data)}")

        try:
            # 获取交易详情
            tx = client.get_transaction(signature)

            if not tx:
                failed_txs += 1
                continue

            successful_txs += 1

            # 解析交易
            meta = tx.get('meta', {})
            transaction = tx.get('transaction', {})
            message = transaction.get('message', {})

            # 检查交易是否成功
            if meta.get('err'):
                tx_type_counter['failed'] += 1
            else:
                tx_type_counter['success'] += 1

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
            is_liquidation = False

            for instr in instructions:
                # 获取 programId
                program_id = instr.get('programId', '')
                if not program_id and 'programIdIndex' in instr:
                    idx = instr['programIdIndex']
                    if idx < len(accounts):
                        program_id = accounts[idx]

                # 只处理 Kamino 指令
                if program_id == KAMINO_LENDING_PROGRAM_ID:
                    total_kamino_instructions += 1

                    # 获取指令数据和账户
                    data = instr.get('data', '')
                    acc_indices = instr.get('accounts', [])
                    instr_accounts = []
                    for idx in acc_indices:
                        if isinstance(idx, int) and idx < len(accounts):
                            instr_accounts.append(accounts[idx])

                    # 解码指令
                    parsed = decoder.decode_instruction_data(
                        data, instr_accounts)

                    if parsed:
                        instruction_counter[parsed.name] += 1
                        instruction_type_counter[parsed.instruction_type] += 1

                        if parsed.instruction_type == 'liquidation':
                            is_liquidation = True
                    else:
                        instruction_counter['unknown'] += 1
                        instruction_type_counter['unknown'] += 1

            if is_liquidation:
                liquidation_count += 1

        except Exception as e:
            failed_txs += 1
            if verbose:
                print(f"   ⚠ 解析交易失败: {signature[:16]}... - {e}")

    # 输出统计结果
    print("\n" + "=" * 70)
    print("📊 统计结果")
    print("=" * 70)

    print(f"\n📌 交易统计:")
    print(f"   获取签名数: {len(signatures_data)}")
    print(f"   成功解析数: {successful_txs}")
    print(f"   解析失败数: {failed_txs}")
    print(f"   清算交易数: {liquidation_count}")

    print(f"\n📌 Kamino 指令统计:")
    print(f"   总指令数: {total_kamino_instructions}")
    print(f"   不同指令类型数: {len(instruction_counter)}")

    print(f"\n📌 指令类型分布 (按类型):")
    for instr_type, count in instruction_type_counter.most_common():
        pct = count / total_kamino_instructions * \
            100 if total_kamino_instructions > 0 else 0
        print(f"   {instr_type:<15} : {count:>5} ({pct:.1f}%)")

    print(f"\n📌 具体指令分布 (Top 20):")
    for name, count in instruction_counter.most_common(20):
        pct = count / total_kamino_instructions * \
            100 if total_kamino_instructions > 0 else 0
        print(f"   {name:<50} : {count:>5} ({pct:.1f}%)")

    if len(instruction_counter) > 20:
        print(f"   ... 还有 {len(instruction_counter) - 20} 种其他指令")

    print("\n" + "=" * 70)

    # 返回统计数据
    return {
        'total_signatures': len(signatures_data),
        'successful_txs': successful_txs,
        'failed_txs': failed_txs,
        'liquidation_count': liquidation_count,
        'total_kamino_instructions': total_kamino_instructions,
        'unique_instruction_count': len(instruction_counter),
        'instruction_counter': dict(instruction_counter),
        'instruction_type_counter': dict(instruction_type_counter)
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='获取并分析 Kamino 历史交易')
    parser.add_argument('--limit', type=int, default=100,
                        help='获取的交易数量 (默认: 100)')
    parser.add_argument('--output', '-o', help='输出 JSON 文件路径')
    parser.add_argument('--quiet', '-q', action='store_true', help='安静模式')

    args = parser.parse_args()

    try:
        result = fetch_and_analyze_kamino_history(
            limit=args.limit,
            verbose=not args.quiet
        )

        if args.output and result:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"\n✓ 结果已保存到: {args.output}")

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
