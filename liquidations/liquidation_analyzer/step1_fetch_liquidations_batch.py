#!/usr/bin/env python3
"""
步骤 1 (批量版): 使用批量 RPC 请求快速获取清算交易

功能：
1. 获取 Kamino 交易签名
2. 排除失败交易（err 不为 None）
3. 使用批量 RPC 请求一次获取多个交易详情（大幅提速）
4. 快速检测是否为清算交易
5. 只保存清算交易的原始数据

使用方法：
    python step1_fetch_liquidations_batch.py --limit 5000
    python step1_fetch_liquidations_batch.py --limit 10000 --batch-size 100
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
from concurrent.futures import ThreadPoolExecutor, as_completed


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


def has_liquidation_instruction(tx: Dict) -> bool:
    """检查交易是否包含清算指令"""
    if not tx:
        return False

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
# Helius 批量客户端
# ============================================================================

class HeliusBatchClient:
    """支持批量请求的 Helius 客户端"""

    def __init__(self, batch_size: int = 50):
        self.api_key = get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        self.batch_size = batch_size

    def _make_rpc_request(self, payload: Any, retries: int = 5, timeout: int = 60) -> Any:
        """发送 RPC 请求（支持单个或批量），带退避重试"""
        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}

        for attempt in range(retries):
            try:
                request = urllib.request.Request(
                    self.rpc_url, data=data, headers=headers, method='POST'
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return result
            except urllib.error.HTTPError as e:
                if e.code == 429:  # Too Many Requests
                    wait_time = 2 ** attempt  # 指数退避: 1, 2, 4, 8, 16 秒
                    if attempt < retries - 1:
                        time.sleep(wait_time)
                        continue
                raise
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(1)
                else:
                    raise

    def get_signatures_for_address(self, address: str, limit: int = 1000, before: Optional[str] = None) -> List[Dict]:
        """获取地址的交易签名"""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": min(limit, 1000)}]
        }
        if before:
            payload["params"][1]["before"] = before

        result = self._make_rpc_request(payload)
        if 'error' in result:
            raise Exception(f"RPC 错误: {result['error']}")
        return result.get('result', [])

    def get_transactions_batch(self, signatures: List[str]) -> List[Optional[Dict]]:
        """
        批量获取交易详情

        Args:
            signatures: 交易签名列表

        Returns:
            交易数据列表（与签名顺序对应，失败的为 None）
        """
        if not signatures:
            return []

        # 构建批量请求
        batch_payload = []
        for i, sig in enumerate(signatures):
            batch_payload.append({
                "jsonrpc": "2.0",
                "id": i,
                "method": "getTransaction",
                "params": [
                    sig,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                ]
            })

        # 发送批量请求
        results = self._make_rpc_request(batch_payload, timeout=120)

        # 处理结果
        tx_map = {}
        if isinstance(results, list):
            for res in results:
                idx = res.get('id', -1)
                if 'result' in res:
                    tx_map[idx] = res['result']
                else:
                    tx_map[idx] = None

        # 按原始顺序返回
        return [tx_map.get(i) for i in range(len(signatures))]


# ============================================================================
# 主程序
# ============================================================================

def fetch_liquidations_batch(limit: int = 5000, batch_size: int = 50, output_dir: str = "./data"):
    """批量获取清算交易"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"步骤 1 (批量版): 快速获取清算交易")
    print("=" * 70)
    print(f"\n目标: 扫描 {limit} 条签名，只保存清算交易")
    print(f"批量大小: {batch_size} 条/请求")
    print(f"清算 Discriminators: {len(LIQUIDATION_DISCRIMINATORS)} 种")

    client = HeliusBatchClient(batch_size=batch_size)

    # 统计
    stats = {
        'total_signatures': 0,
        'failed_signatures': 0,
        'success_signatures': 0,
        'liquidation_count': 0,
        'non_liquidation_count': 0,
        'fetch_failed': 0,
        'batch_requests': 0,
    }

    liquidation_txs = []
    before = None
    start_time = time.time()

    # ========================================
    # 1. 获取所有签名
    # ========================================
    print(f"\n📥 获取签名...")

    all_signatures = []
    success_sig_infos = []

    while len(all_signatures) < limit:
        batch_limit = min(1000, limit - len(all_signatures))
        sigs = client.get_signatures_for_address(
            KAMINO_LENDING_PROGRAM_ID, limit=batch_limit, before=before
        )

        if not sigs:
            break

        all_signatures.extend(sigs)
        before = sigs[-1]['signature']

        # 过滤成功交易
        for sig_info in sigs:
            if sig_info.get('err') is None:
                success_sig_infos.append(sig_info)
                stats['success_signatures'] += 1
            else:
                stats['failed_signatures'] += 1

        print(
            f"   签名: {len(all_signatures)} | 成功: {len(success_sig_infos)} | 失败: {stats['failed_signatures']}")

    stats['total_signatures'] = len(all_signatures)
    print(f"\n   ✓ 总签名: {stats['total_signatures']}")
    print(f"   ✓ 成功签名: {len(success_sig_infos)} (将获取详情)")
    print(f"   ✓ 失败签名: {stats['failed_signatures']} (已跳过)")

    # ========================================
    # 2. 批量获取交易详情
    # ========================================
    print(f"\n📥 批量获取交易详情...")

    total_batches = (len(success_sig_infos) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(success_sig_infos), batch_size):
        batch_end = min(batch_idx + batch_size, len(success_sig_infos))
        batch_sig_infos = success_sig_infos[batch_idx:batch_end]
        batch_signatures = [s['signature'] for s in batch_sig_infos]

        current_batch = batch_idx // batch_size + 1

        try:
            # 批量获取
            txs = client.get_transactions_batch(batch_signatures)
            stats['batch_requests'] += 1

            # 处理结果
            for i, tx in enumerate(txs):
                sig_info = batch_sig_infos[i]

                if tx:
                    if has_liquidation_instruction(tx):
                        stats['liquidation_count'] += 1
                        liquidation_txs.append({
                            'signature': sig_info['signature'],
                            'slot': sig_info.get('slot'),
                            'blockTime': sig_info.get('blockTime'),
                            'transaction': tx
                        })
                    else:
                        stats['non_liquidation_count'] += 1
                else:
                    stats['fetch_failed'] += 1

            # 进度
            elapsed = time.time() - start_time
            progress = batch_end / len(success_sig_infos) * 100
            print(f"   批次 {current_batch}/{total_batches} ({progress:.0f}%) | "
                  f"清算: {stats['liquidation_count']} | "
                  f"耗时: {elapsed:.1f}s")

        except Exception as e:
            print(f"   ⚠ 批次 {current_batch} 失败: {e}")
            stats['fetch_failed'] += len(batch_signatures)

        # 速率限制: Helius 免费版 10 req/sec
        # batch_size=10 时，每秒 1 批刚好 10 请求
        wait_time = max(1.0, batch_size / 10)  # 确保不超过 10 req/sec
        time.sleep(wait_time)

    elapsed_total = time.time() - start_time

    # ========================================
    # 3. 保存清算交易
    # ========================================
    print(f"\n📁 保存清算交易...")

    output_file = output_path / f"liquidations_raw_{timestamp}.json"

    save_data = {
        'metadata': {
            'timestamp': timestamp,
            'batch_size': batch_size,
            'elapsed_seconds': elapsed_total,
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
    # 4. 报告
    # ========================================
    print("\n" + "=" * 70)
    print("📊 统计报告")
    print("=" * 70)

    liq_rate = stats['liquidation_count'] / stats['success_signatures'] * \
        100 if stats['success_signatures'] > 0 else 0

    # 计算速度对比
    single_request_estimate = stats['success_signatures'] * 0.5  # 假设单个请求 0.5 秒
    speedup = single_request_estimate / elapsed_total if elapsed_total > 0 else 0

    print(f"""
📌 签名统计:
   总签名数: {stats['total_signatures']}
   成功交易: {stats['success_signatures']}
   失败交易: {stats['failed_signatures']} (已跳过)

📌 清算统计:
   清算交易: {stats['liquidation_count']} ({liq_rate:.2f}%)
   非清算交易: {stats['non_liquidation_count']}

📌 性能统计:
   批量请求数: {stats['batch_requests']}
   批量大小: {batch_size}
   总耗时: {elapsed_total:.1f} 秒
   速度: {stats['success_signatures'] / elapsed_total:.1f} 条/秒
   预估加速: {speedup:.1f}x (相比逐个请求)

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

    parser = argparse.ArgumentParser(description='批量获取 Kamino 清算交易')
    parser.add_argument('--limit', type=int, default=5000,
                        help='扫描签名数量 (默认: 5000)')
    parser.add_argument('--batch-size', type=int,
                        default=10, help='每批请求数量 (默认: 10, 适配 Helius 10 req/s 限制)')
    parser.add_argument('--output', '-o', default='./data', help='输出目录')

    args = parser.parse_args()

    try:
        fetch_liquidations_batch(
            limit=args.limit,
            batch_size=args.batch_size,
            output_dir=args.output
        )
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
