"""
完整获取 Kamino 7 天数据
- 每 1000 笔保存一个批次文件
- 自动检测清算交易
- 使用 IDL 详细解析清算
- 清算数据单独保存
"""
import asyncio
import json
import os
import base58
import struct
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.signature import Signature
import config


# 清算指令 discriminators
LIQUIDATION_DISCRIMINATORS = {
    "c2a3229ec9239fad": "liquidateObligationAndRedeemReserveCollateral",
    "39276e70d588a669": "liquidateObligationAndRedeemReserveCollateralV2",
}


def calculate_discriminator(instruction_name: str) -> str:
    """计算 Anchor 指令 discriminator"""
    preimage = f"global:{instruction_name}".encode()
    return hashlib.sha256(preimage).digest()[:8].hex()


def check_liquidation(tx: dict) -> tuple[bool, str, str]:
    """
    检查交易是否为清算

    Returns:
        (is_liquidation, liquidation_type, instruction_data)
    """
    # 方法 1: Helius type
    if tx.get('type') == 'LIQUIDATE':
        return True, "LIQUIDATE", ""

    # 方法 2: 检查指令 discriminator
    instructions = tx.get('instructions', [])
    for inst in instructions:
        inst_data = inst.get('data', '')
        if inst_data:
            try:
                data_bytes = base58.b58decode(inst_data)
                if len(data_bytes) >= 8:
                    disc = data_bytes[:8].hex()
                    if disc in LIQUIDATION_DISCRIMINATORS:
                        return True, LIQUIDATION_DISCRIMINATORS[disc], inst_data
            except:
                pass

    return False, "", ""


def decode_liquidation_params(instruction_data: str, liquidation_type: str) -> dict:
    """
    使用 IDL 解码清算参数

    参数结构:
    - liquidityAmount: u64 (8 bytes)
    - minAcceptableReceivedLiquidityAmount: u64 (8 bytes)
    - maxAllowedLtvOverridePercent: u64 (8 bytes)
    """
    try:
        data_bytes = base58.b58decode(instruction_data)

        if len(data_bytes) < 32:  # 8 discriminator + 3*8 params
            return {"error": "数据长度不足"}

        # 跳过 discriminator (前 8 字节)
        params_data = data_bytes[8:]

        # 解析三个 u64 参数
        if len(params_data) >= 24:
            liquidityAmount = struct.unpack('<Q', params_data[0:8])[0]
            minAcceptableAmount = struct.unpack('<Q', params_data[8:16])[0]
            maxLtvOverride = struct.unpack('<Q', params_data[16:24])[0]

            return {
                "liquidityAmount": liquidityAmount,
                "liquidityAmount_readable": f"{liquidityAmount / 1e6:.6f}",  # 假设 6 位小数
                "minAcceptableReceivedLiquidityAmount": minAcceptableAmount,
                "minAcceptableAmount_readable": f"{minAcceptableAmount / 1e6:.6f}",
                "maxAllowedLtvOverridePercent": maxLtvOverride,
                "maxLtvOverride_readable": f"{maxLtvOverride / 100:.2f}%",  # 假设百分比
            }
        else:
            return {"error": "参数数据不足"}

    except Exception as e:
        return {"error": str(e)}


async def fetch_kamino_full_7days():
    """
    获取完整 7 天数据
    """
    print("=" * 120)
    print("Kamino 完整 7 天数据获取程序（带清算检测）")
    print("=" * 120)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"时间范围: 完整 7 天")
    print(f"清算检测: 已启用（自动 IDL 解析）")
    print("=" * 120)

    # 创建文件夹
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_folder = f"kamino_data_7d_{timestamp}"
    stats_folder = f"kamino_stats_7d_{timestamp}"
    liquidations_folder = f"kamino_liquidations_{timestamp}"

    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(stats_folder, exist_ok=True)
    os.makedirs(liquidations_folder, exist_ok=True)

    print(f"\n创建文件夹:")
    print(f"  数据文件夹: {data_folder}/")
    print(f"  统计文件夹: {stats_folder}/")
    print(f"  清算文件夹: {liquidations_folder}/")

    # 获取数据
    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    print(f"\nKamino 程序地址: {kamino_address}")

    async with AsyncClient(config.SOLANA_RPC_URL) as client:
        print("\n步骤 1: 获取交易签名列表...")

        pubkey = Pubkey.from_string(kamino_address)
        cutoff_time = datetime.now() - timedelta(days=7)
        cutoff_timestamp = int(cutoff_time.timestamp())

        all_signatures = []
        before_sig = None
        page = 0

        # 获取所有签名（不限制页数）
        while True:
            page += 1
            if page % 10 == 0:
                print(f"  已获取 {len(all_signatures)} 个签名...")

            try:
                options = {"limit": 100}
                if before_sig:
                    options["before"] = before_sig

                response = await client.get_signatures_for_address(pubkey, **options)

                if not response.value:
                    break

                valid_sigs = []
                reached_cutoff = False

                for sig_info in response.value:
                    if sig_info.block_time and sig_info.block_time >= cutoff_timestamp:
                        valid_sigs.append({
                            "signature": str(sig_info.signature),
                            "blockTime": sig_info.block_time,
                        })
                    else:
                        reached_cutoff = True
                        break

                all_signatures.extend(valid_sigs)

                if reached_cutoff or len(response.value) < 100:
                    break

                before_sig = Signature.from_string(response.value[-1].signature.__str__())
                await asyncio.sleep(0.1)

            except Exception as e:
                print(f"  获取签名出错: {e}")
                break

        total_signatures = len(all_signatures)
        print(f"\n✓ 共获取 {total_signatures} 个交易签名")
        print(f"  预计需要约 {total_signatures // 1000 + 1} 个批次文件")

        # 步骤 2: 获取交易详情并检测清算
        print(f"\n步骤 2: 获取交易详情（实时检测清算）...")

        all_transactions = []
        liquidations_found = []
        type_counts = defaultdict(int)
        batch_size = 1000
        current_batch = []
        batch_number = 0

        for i, sig_info in enumerate(all_signatures):
            if (i + 1) % 100 == 0:
                progress = (i + 1) / total_signatures * 100
                print(f"  进度: {i+1}/{total_signatures} ({progress:.1f}%) | 清算: {len(liquidations_found)}")

            signature = sig_info["signature"]

            try:
                sig_obj = Signature.from_string(signature)
                response = await client.get_transaction(
                    sig_obj,
                    encoding="json",
                    max_supported_transaction_version=0,
                )

                if not response.value:
                    continue

                # 构建交易数据（简化版本）
                tx_data = {
                    "signature": signature,
                    "blockTime": sig_info["blockTime"],
                    "timestamp": sig_info["blockTime"],
                    "slot": response.value.slot if hasattr(response.value, 'slot') else None,
                }

                # 提取指令
                if hasattr(response.value.transaction, 'transaction') and \
                   hasattr(response.value.transaction.transaction, 'message'):
                    message = response.value.transaction.transaction.message
                    if hasattr(message, 'instructions'):
                        tx_data['instructions'] = [
                            {
                                'programId': str(inst.program_id) if hasattr(inst, 'program_id') else '',
                                'data': inst.data if hasattr(inst, 'data') else ''
                            }
                            for inst in message.instructions
                        ]

                # 检查清算
                is_liq, liq_type, inst_data = check_liquidation(tx_data)

                if is_liq:
                    print(f"\n  🎯 发现清算 #{len(liquidations_found) + 1}!")
                    print(f"     签名: {signature[:32]}...")
                    print(f"     类型: {liq_type}")

                    # 解析清算参数
                    params = decode_liquidation_params(inst_data, liq_type) if inst_data else {}

                    liquidation_data = {
                        "liquidation_number": len(liquidations_found) + 1,
                        "signature": signature,
                        "blockTime": sig_info["blockTime"],
                        "timestamp": datetime.fromtimestamp(sig_info["blockTime"]).isoformat(),
                        "liquidation_type": liq_type,
                        "instruction_data": inst_data,
                        "decoded_params": params,
                        "transaction": tx_data,
                    }

                    liquidations_found.append(liquidation_data)

                    # 立即保存清算数据
                    liq_filename = f"liquidation_{len(liquidations_found):03d}.json"
                    liq_filepath = os.path.join(liquidations_folder, liq_filename)
                    with open(liq_filepath, 'w', encoding='utf-8') as f:
                        json.dump(liquidation_data, f, indent=2, ensure_ascii=False)

                    print(f"     已保存: {liq_filename}")

                    if params and 'liquidityAmount_readable' in params:
                        print(f"     清算金额: {params['liquidityAmount_readable']}")

                # 添加到批次
                tx_data['type'] = 'LIQUIDATE' if is_liq else 'NORMAL'
                type_counts[tx_data['type']] += 1
                current_batch.append(tx_data)

                # 保存批次
                if len(current_batch) >= batch_size:
                    batch_number += 1
                    batch_filename = f"kamino_batch_{batch_number:03d}.json"
                    batch_filepath = os.path.join(data_folder, batch_filename)

                    with open(batch_filepath, 'w', encoding='utf-8') as f:
                        json.dump({
                            "batch_number": batch_number,
                            "transactions": current_batch
                        }, f, indent=2, ensure_ascii=False)

                    print(f"  ✓ 批次 {batch_number} 已保存 ({len(current_batch)} 笔)")
                    all_transactions.extend(current_batch)
                    current_batch = []

                await asyncio.sleep(0.05)

            except Exception as e:
                if (i + 1) % 500 == 0:
                    print(f"  处理交易 {i+1} 出错: {e}")
                continue

        # 保存最后一批
        if current_batch:
            batch_number += 1
            batch_filename = f"kamino_batch_{batch_number:03d}.json"
            batch_filepath = os.path.join(data_folder, batch_filename)

            with open(batch_filepath, 'w', encoding='utf-8') as f:
                json.dump({
                    "batch_number": batch_number,
                    "transactions": current_batch
                }, f, indent=2, ensure_ascii=False)

            all_transactions.extend(current_batch)
            print(f"  ✓ 最后批次 {batch_number} 已保存 ({len(current_batch)} 笔)")

        print(f"\n✓ 共保存 {len(all_transactions)} 笔交易到 {batch_number} 个批次文件")

        # 保存统计
        print(f"\n步骤 3: 保存统计数据...")

        statistics = {
            "summary": {
                "total_transactions": len(all_transactions),
                "total_liquidations": len(liquidations_found),
                "time_range_days": 7,
                "fetch_time": datetime.now().isoformat(),
                "total_batches": batch_number,
            },
            "type_counts": dict(type_counts),
            "liquidations": [
                {
                    "signature": liq["signature"],
                    "timestamp": liq["timestamp"],
                    "type": liq["liquidation_type"],
                    "params": liq["decoded_params"]
                }
                for liq in liquidations_found
            ]
        }

        stats_file = os.path.join(stats_folder, "statistics.json")
        with open(stats_file, 'w', encoding='utf-8') as f:
            json.dump(statistics, f, indent=2, ensure_ascii=False)

        print(f"  ✓ 统计已保存: {stats_file}")

        # 摘要
        print("\n" + "=" * 120)
        print("执行摘要")
        print("=" * 120)
        print(f"\n总交易数: {len(all_transactions)}")
        print(f"清算交易数: {len(liquidations_found)} 笔")
        print(f"批次文件: {batch_number} 个")
        print(f"\n文件夹:")
        print(f"  数据: {data_folder}/")
        print(f"  统计: {stats_folder}/")
        print(f"  清算: {liquidations_folder}/")

        if liquidations_found:
            print(f"\n清算详情:")
            for liq in liquidations_found:
                print(f"  {liq['liquidation_number']}. {liq['timestamp']}")
                print(f"     签名: {liq['signature']}")
                print(f"     类型: {liq['liquidation_type']}")
                if liq['decoded_params']:
                    print(f"     参数: {liq['decoded_params']}")

        print("\n" + "=" * 120)
        print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 120)


async def main():
    """主函数"""
    try:
        await fetch_kamino_full_7days()
    except KeyboardInterrupt:
        print("\n\n用户中断")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
