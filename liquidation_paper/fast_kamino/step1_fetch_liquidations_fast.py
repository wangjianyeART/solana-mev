#!/usr/bin/env python3
"""
步骤 1 (快速版): 并发获取清算交易，严格遵守速率限制

功能：
1. 获取 Kamino 交易签名
2. 排除失败交易（err 不为 None）
3. 使用并发请求加速，但严格控制 8 req/sec
4. 快速检测是否为清算交易
5. 只保存清算交易的原始数据

使用方法：
    python step1_fetch_liquidations_fast.py --limit 5000
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
import threading


# ============================================================================
# 配置
# ============================================================================

KAMINO_LENDING_PROGRAM_ID = "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD"
MAX_REQUESTS_PER_SECOND = 8  # 留点余量，避免触发 429

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
    account_keys = message.get('accountKeys', [])
    accounts = []
    for acc in account_keys:
        if isinstance(acc, dict):
            accounts.append(acc.get('pubkey', ''))
        else:
            accounts.append(acc)

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
# 速率限制器
# ============================================================================

class RateLimiter:
    """令牌桶速率限制器"""

    def __init__(self, rate: float):
        self.rate = rate  # 每秒允许的请求数
        self.tokens = rate
        self.last_time = time.time()
        self.lock = threading.Lock()

    def acquire(self):
        """获取一个令牌，如果没有则等待"""
        with self.lock:
            now = time.time()
            elapsed = now - self.last_time
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_time = now

            if self.tokens < 1:
                wait_time = (1 - self.tokens) / self.rate
                time.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= 1


# ============================================================================
# Helius 客户端（带速率限制）
# ============================================================================

class HeliusRateLimitedClient:
    """带速率限制的 Helius 客户端"""

    def __init__(self, requests_per_second: int = MAX_REQUESTS_PER_SECOND):
        self.api_key = get_helius_api_key()
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        self.rate_limiter = RateLimiter(requests_per_second)

    def _make_rpc_request(self, method: str, params: list, retries: int = 3) -> Any:
        """发送单个 RPC 请求"""
        self.rate_limiter.acquire()

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
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)
                    continue
                raise
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

def fetch_liquidations_fast(limit: int = 5000, workers: int = 5, output_dir: str = "./data"):
    """快速获取清算交易"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"步骤 1 (快速版): 并发获取清算交易")
    print("=" * 70)
    print(f"\n目标: 扫描 {limit} 条签名，只保存清算交易")
    print(f"速率限制: {MAX_REQUESTS_PER_SECOND} req/sec")
    print(f"并发线程: {workers}")
    print(f"清算 Discriminators: {len(LIQUIDATION_DISCRIMINATORS)} 种")

    client = HeliusRateLimitedClient()

    # 统计（线程安全）
    stats_lock = threading.Lock()
    stats = {
        'total_signatures': 0,
        'failed_signatures': 0,
        'success_signatures': 0,
        'liquidation_count': 0,
        'non_liquidation_count': 0,
        'fetch_failed': 0,
    }

    liquidation_txs = []
    liquidation_lock = threading.Lock()

    start_time = time.time()

    # ========================================
    # 1. 获取所有签名
    # ========================================
    print(f"\n📥 获取签名...")

    all_signatures = []
    success_sig_infos = []
    before = None

    while len(all_signatures) < limit:
        batch_limit = min(1000, limit - len(all_signatures))
        sigs = client.get_signatures_for_address(
            KAMINO_LENDING_PROGRAM_ID, limit=batch_limit, before=before
        )

        if not sigs:
            break

        all_signatures.extend(sigs)
        before = sigs[-1]['signature']

        for sig_info in sigs:
            if sig_info.get('err') is None:
                success_sig_infos.append(sig_info)
            else:
                stats['failed_signatures'] += 1

        print(
            f"   签名: {len(all_signatures)} | 成功: {len(success_sig_infos)} | 失败: {stats['failed_signatures']}")

    stats['total_signatures'] = len(all_signatures)
    stats['success_signatures'] = len(success_sig_infos)

    print(f"\n   ✓ 总签名: {stats['total_signatures']}")
    print(f"   ✓ 成功签名: {len(success_sig_infos)} (将获取详情)")
    print(f"   ✓ 失败签名: {stats['failed_signatures']} (已跳过)")

    # ========================================
    # 2. 并发获取交易详情
    # ========================================
    print(f"\n📥 并发获取交易详情...")

    processed = [0]  # 使用列表来在闭包中修改

    def process_signature(sig_info):
        """处理单个签名"""
        signature = sig_info['signature']

        try:
            tx = client.get_transaction(signature)

            if tx:
                if has_liquidation_instruction(tx):
                    with liquidation_lock:
                        liquidation_txs.append({
                            'signature': signature,
                            'slot': sig_info.get('slot'),
                            'blockTime': sig_info.get('blockTime'),
                            'transaction': tx
                        })
                    with stats_lock:
                        stats['liquidation_count'] += 1
                    return 'liquidation'
                else:
                    with stats_lock:
                        stats['non_liquidation_count'] += 1
                    return 'non_liquidation'
            else:
                with stats_lock:
                    stats['fetch_failed'] += 1
                return 'failed'
        except Exception as e:
            with stats_lock:
                stats['fetch_failed'] += 1
            return 'failed'

    # 使用线程池并发处理
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_signature, sig_info): sig_info
                   for sig_info in success_sig_infos}

        for future in as_completed(futures):
            processed[0] += 1

            if processed[0] % 50 == 0 or processed[0] == len(success_sig_infos):
                elapsed = time.time() - start_time
                rate = processed[0] / elapsed if elapsed > 0 else 0
                print(f"   进度: {processed[0]}/{len(success_sig_infos)} ({processed[0]*100//len(success_sig_infos)}%) | "
                      f"清算: {stats['liquidation_count']} | "
                      f"速度: {rate:.1f} tx/s")

    elapsed_total = time.time() - start_time

    # ========================================
    # 3. 保存清算交易
    # ========================================
    print(f"\n📁 保存清算交易...")

    output_file = output_path / f"liquidations_raw_{timestamp}.json"

    save_data = {
        'metadata': {
            'timestamp': timestamp,
            'elapsed_seconds': elapsed_total,
            'requests_per_second': MAX_REQUESTS_PER_SECOND,
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
    tx_rate = stats['success_signatures'] / \
        elapsed_total if elapsed_total > 0 else 0

    print(f"""
📌 签名统计:
   总签名数: {stats['total_signatures']}
   成功交易: {stats['success_signatures']}
   失败交易: {stats['failed_signatures']} (已跳过)

📌 清算统计:
   清算交易: {stats['liquidation_count']} ({liq_rate:.2f}%)
   非清算交易: {stats['non_liquidation_count']}

📌 性能统计:
   总耗时: {elapsed_total:.1f} 秒
   处理速度: {tx_rate:.1f} 条/秒
   速率限制: {MAX_REQUESTS_PER_SECOND} req/sec

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

    parser = argparse.ArgumentParser(description='快速获取 Kamino 清算交易')
    parser.add_argument('--limit', type=int, default=5000,
                        help='扫描签名数量 (默认: 5000)')
    parser.add_argument('--workers', type=int, default=5, help='并发线程数 (默认: 5)')
    parser.add_argument('--output', '-o', default='./data', help='输出目录')

    args = parser.parse_args()

    try:
        fetch_liquidations_fast(
            limit=args.limit,
            workers=args.workers,
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
