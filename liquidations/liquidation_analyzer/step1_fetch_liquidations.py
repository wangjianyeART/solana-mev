#!/usr/bin/env python3
"""
步骤 1 (优化版): 只获取清算交易

功能：
1. 获取 Kamino 交易签名
2. 排除失败交易（err 不为 None）
3. 获取详情时快速检测是否为清算交易
4. 只保存清算交易的原始数据

使用方法：
    python step1_fetch_liquidations.py --limit 5000
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
from typing import Optional, Dict, Any, List, Set


# ============================================================================
# 配置
# ============================================================================

KAMINO_LENDING_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"

# Base58 字母表
BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def base58_decode(s: str) -> bytes:
    """Base58 解码"""
    num = 0
    for char in s:
        num = num * 58 + BASE58_ALPHABET.index(char)
    result = []
    while num > 0:
        num, rem = divmod(num, 256)
        result.append(rem)
    # 处理前导 '1'
    for char in s:
        if char == '1':
            result.append(0)
        else:
            break
    return bytes(reversed(result))


def compute_discriminator(name: str) -> str:
    """计算 Anchor 指令的 discriminator"""
    preimage = f"global:{name}".encode()
    return hashlib.sha256(preimage).digest()[:8].hex()


# 预计算清算指令的 discriminators
LIQUIDATION_DISCRIMINATORS: Set[str] = {
    compute_discriminator('liquidateObligationAndRedeemReserveCollateral'),
    compute_discriminator('liquidateObligationAndRedeemReserveCollateralV2'),
    compute_discriminator(
        'liquidate_obligation_and_redeem_reserve_collateral'),
    compute_discriminator(
        'liquidate_obligation_and_redeem_reserve_collateral_v2'),
}


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
# 快速清算检测
# ============================================================================

def is_liquidation_instruction(data: str) -> bool:
    """快速检测指令是否为清算指令"""
    if not data or len(data) < 11:  # base58 编码的 8 字节至少需要 11 个字符
        return False
    try:
        data_bytes = base58_decode(data)
        if len(data_bytes) < 8:
            return False
        disc = data_bytes[:8].hex()
        return disc in LIQUIDATION_DISCRIMINATORS
    except:
        return False


def has_liquidation_instruction(tx: Dict) -> bool:
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
    for instr in message.get('instructions', []):
        program_id = instr.get('programId', '')
        if not program_id and 'programIdIndex' in instr:
            idx = instr['programIdIndex']
            if idx < len(accounts):
                program_id = accounts[idx]

        if program_id == KAMINO_LENDING_PROGRAM_ID:
            data = instr.get('data', '')
            if is_liquidation_instruction(data):
                return True

    return False


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
        params = [signature, {"encoding": "jsonParsed",
                              "maxSupportedTransactionVersion": 0}]
        return self._make_rpc_request("getTransaction", params)


# ============================================================================
# 主程序
# ============================================================================

def fetch_liquidations(limit: int = 5000, output_dir: str = "./data"):
    """获取清算交易"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"步骤 1 (优化版): 只获取清算交易")
    print("=" * 70)
    print(f"\n目标: 扫描 {limit} 条签名，只保存清算交易")
    print(f"清算 Discriminators: {len(LIQUIDATION_DISCRIMINATORS)} 种")

    client = HeliusClient()

    # 统计
    stats = {
        'total_signatures': 0,
        'failed_signatures': 0,
        'success_signatures': 0,
        'liquidation_count': 0,
        'non_liquidation_count': 0,
        'fetch_failed': 0,
    }

    liquidation_txs = []
    before = None

    # ========================================
    # 1. 获取并检测清算交易
    # ========================================
    print(f"\n📥 扫描交易...")

    while stats['total_signatures'] < limit:
        # 获取一批签名
        batch_size = min(1000, limit - stats['total_signatures'])
        sigs = client.get_signatures_for_address(
            KAMINO_LENDING_PROGRAM_ID, limit=batch_size, before=before
        )

        if not sigs:
            break

        stats['total_signatures'] += len(sigs)
        before = sigs[-1]['signature']

        # 处理每个签名
        for sig_info in sigs:
            signature = sig_info['signature']

            # 跳过失败交易
            if sig_info.get('err') is not None:
                stats['failed_signatures'] += 1
                continue

            stats['success_signatures'] += 1

            # 获取交易详情
            try:
                tx = client.get_transaction(signature)
                if tx:
                    # 快速检测是否为清算
                    if has_liquidation_instruction(tx):
                        stats['liquidation_count'] += 1
                        liquidation_txs.append({
                            'signature': signature,
                            'slot': sig_info.get('slot'),
                            'blockTime': sig_info.get('blockTime'),
                            'transaction': tx
                        })
                    else:
                        stats['non_liquidation_count'] += 1
                else:
                    stats['fetch_failed'] += 1
            except:
                stats['fetch_failed'] += 1

        # 进度
        print(f"   签名: {stats['total_signatures']} | "
              f"成功: {stats['success_signatures']} | "
              f"失败: {stats['failed_signatures']} | "
              f"清算: {stats['liquidation_count']}")

        # 速率限制
        time.sleep(0.1)

    # ========================================
    # 2. 保存清算交易
    # ========================================
    print(f"\n📁 保存清算交易...")

    output_file = output_path / f"liquidations_raw_{timestamp}.json"

    save_data = {
        'metadata': {
            'timestamp': timestamp,
            **stats
        },
        'transactions': liquidation_txs
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False)

    file_size = output_file.stat().st_size / 1024
    print(f"   ✓ 已保存: {output_file}")
    print(f"   ✓ 文件大小: {file_size:.1f} KB")
    print(f"   ✓ 清算交易数: {len(liquidation_txs)}")

    # ========================================
    # 3. 报告
    # ========================================
    print("\n" + "=" * 70)
    print("📊 统计报告")
    print("=" * 70)

    liq_rate = stats['liquidation_count'] / stats['success_signatures'] * \
        100 if stats['success_signatures'] > 0 else 0

    print(f"""
📌 签名统计:
   总签名数: {stats['total_signatures']}
   成功交易: {stats['success_signatures']}
   失败交易: {stats['failed_signatures']} (已跳过)

📌 清算统计:
   清算交易: {stats['liquidation_count']} ({liq_rate:.2f}%)
   非清算交易: {stats['non_liquidation_count']}

📁 输出文件:
   {output_file}
""")

    print("=" * 70)
    print("✓ 步骤 1 完成！")
    print(f"  下一步: python step2_batch_parse.py {output_file}")
    print("=" * 70)

    return str(output_file), stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description='获取 Kamino 清算交易')
    parser.add_argument('--limit', type=int, default=5000,
                        help='扫描签名数量 (默认: 5000)')
    parser.add_argument('--output', '-o', default='./data', help='输出目录')

    args = parser.parse_args()

    try:
        fetch_liquidations(limit=args.limit, output_dir=args.output)
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
