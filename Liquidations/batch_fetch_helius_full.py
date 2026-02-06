"""
使用 Helius API 快速获取完整数据
- 支持 7 天或 1 年数据
- 每 1000 笔保存一个批次
- 自动检测和解析清算
- 速度快 10-20 倍
"""
import asyncio
import json
import os
import base58
import struct
import hashlib
import aiohttp
from datetime import datetime, timedelta
from collections import defaultdict
import config


# 清算 discriminators
LIQUIDATION_DISCRIMINATORS = {
    "c2a3229ec9239fad": "liquidateObligationAndRedeemReserveCollateral",
    "39276e70d588a669": "liquidateObligationAndRedeemReserveCollateralV2",
}


def check_liquidation(tx: dict) -> tuple[bool, str, str]:
    """检查是否为清算交易"""
    if tx.get('type') == 'LIQUIDATE':
        return True, "LIQUIDATE", ""

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


def decode_liquidation_params(instruction_data: str) -> dict:
    """解码清算参数"""
    try:
        data_bytes = base58.b58decode(instruction_data)
        if len(data_bytes) < 32:
            return {"error": "数据长度不足"}

        params_data = data_bytes[8:]
        if len(params_data) >= 24:
            liquidityAmount = struct.unpack('<Q', params_data[0:8])[0]
            minAcceptableAmount = struct.unpack('<Q', params_data[8:16])[0]
            maxLtvOverride = struct.unpack('<Q', params_data[16:24])[0]

            return {
                "liquidityAmount": liquidityAmount,
                "liquidityAmount_readable": f"{liquidityAmount / 1e6:.6f}",
                "minAcceptableReceivedLiquidityAmount": minAcceptableAmount,
                "minAcceptableAmount_readable": f"{minAcceptableAmount / 1e6:.6f}",
                "maxAllowedLtvOverridePercent": maxLtvOverride,
                "maxLtvOverride_readable": f"{maxLtvOverride / 100:.2f}%",
            }
    except Exception as e:
        return {"error": str(e)}

    return {"error": "解析失败"}


async def fetch_helius_transactions(
    api_key: str,
    address: str,
    hours: int = 168,
    max_transactions: int = None
):
    """
    使用 Helius API 获取交易

    Args:
        api_key: Helius API key
        address: 程序地址
        hours: 时间范围（小时）
        max_transactions: 最大交易数（None = 不限制）
    """
    base_url = "https://api.helius.xyz/v0"
    cutoff_time = datetime.now() - timedelta(hours=hours)
    cutoff_timestamp = int(cutoff_time.timestamp())

    all_transactions = []
    before_signature = None
    page = 0

    async with aiohttp.ClientSession() as session:
        while True:
            page += 1

            url = f"{base_url}/addresses/{address}/transactions"
            params = {
                "api-key": api_key,
                "limit": 100,
            }

            if before_signature:
                params["before"] = before_signature

            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        data = await response.json()

                        if not data or not isinstance(data, list):
                            break

                        # 过滤时间范围
                        valid_txs = []
                        for tx in data:
                            tx_timestamp = tx.get('timestamp', 0)
                            if tx_timestamp >= cutoff_timestamp:
                                valid_txs.append(tx)
                            else:
                                # 到达时间边界
                                all_transactions.extend(valid_txs)
                                return all_transactions

                        all_transactions.extend(valid_txs)

                        if page % 10 == 0:
                            print(f"  已获取 {len(all_transactions)} 笔交易...")

                        # 检查是否到达最大数量
                        if max_transactions and len(all_transactions) >= max_transactions:
                            return all_transactions[:max_transactions]

                        if len(data) < 100:
                            break

                        before_signature = data[-1].get('signature')
                        await asyncio.sleep(0.2)  # 速率限制

                    elif response.status == 429:
                        print(f"  速率限制，等待...")
                        await asyncio.sleep(5)
                    else:
                        print(f"  请求失败: {response.status}")
                        break

            except Exception as e:
                print(f"  请求错误: {e}")
                break

    return all_transactions


async def batch_fetch_kamino_helius(days: int = 7):
    """
    使用 Helius API 批量获取 Kamino 数据

    Args:
        days: 天数（7 或 365）
    """
    print("=" * 120)
    print(f"Kamino 数据获取程序 - Helius API 版本 ⚡")
    print("=" * 120)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"时间范围: {days} 天")
    print(f"数据源: Helius Enhanced API")
    print(f"清算检测: 已启用（IDL 解析）")
    print("=" * 120)

    # 创建文件夹
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_folder = f"kamino_data_{days}d_{timestamp}"
    stats_folder = f"kamino_stats_{days}d_{timestamp}"
    liquidations_folder = f"kamino_liquidations_{timestamp}"

    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(stats_folder, exist_ok=True)
    os.makedirs(liquidations_folder, exist_ok=True)

    print(f"\n创建文件夹:")
    print(f"  📁 数据: {data_folder}/")
    print(f"  📁 统计: {stats_folder}/")
    print(f"  📁 清算: {liquidations_folder}/")

    # 获取数据
    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    print(f"\n📍 Kamino 地址: {kamino_address}")
    print(f"\n⏳ 开始获取交易数据...")

    start_time = datetime.now()

    # 获取所有交易
    all_transactions = await fetch_helius_transactions(
        api_key=config.HELIUS_API_KEY,
        address=kamino_address,
        hours=days * 24,
    )

    fetch_duration = (datetime.now() - start_time).total_seconds()
    print(f"\n✅ 获取完成！")
    print(f"  总交易数: {len(all_transactions)}")
    print(f"  耗时: {fetch_duration:.1f} 秒 ({fetch_duration/60:.1f} 分钟)")
    print(f"  速度: {len(all_transactions)/fetch_duration:.1f} 笔/秒")

    # 处理和保存数据
    print(f"\n⚙️  处理数据...")

    liquidations_found = []
    type_counts = defaultdict(int)
    batch_size = 1000
    batch_number = 0

    for i in range(0, len(all_transactions), batch_size):
        batch_transactions = all_transactions[i:i + batch_size]
        batch_number += 1

        # 检查清算
        for tx in batch_transactions:
            tx_type = tx.get('type', 'UNKNOWN')
            type_counts[tx_type] += 1

            is_liq, liq_type, inst_data = check_liquidation(tx)

            if is_liq:
                print(f"\n  🎯 发现清算 #{len(liquidations_found) + 1}!")
                print(f"     签名: {tx.get('signature', '')[:32]}...")
                print(f"     类型: {liq_type}")
                print(f"     时间: {datetime.fromtimestamp(tx.get('timestamp', 0))}")

                # 解析参数
                params = decode_liquidation_params(inst_data) if inst_data else {}

                liquidation_data = {
                    "liquidation_number": len(liquidations_found) + 1,
                    "signature": tx.get('signature'),
                    "blockTime": tx.get('timestamp'),
                    "timestamp": datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat(),
                    "liquidation_type": liq_type,
                    "instruction_data": inst_data,
                    "decoded_params": params,
                    "transaction": tx,
                }

                liquidations_found.append(liquidation_data)

                # 保存清算
                liq_filename = f"liquidation_{len(liquidations_found):03d}.json"
                liq_filepath = os.path.join(liquidations_folder, liq_filename)
                with open(liq_filepath, 'w', encoding='utf-8') as f:
                    json.dump(liquidation_data, f, indent=2, ensure_ascii=False)

                if params and 'liquidityAmount_readable' in params:
                    print(f"     金额: {params['liquidityAmount_readable']}")

        # 保存批次
        batch_filename = f"kamino_batch_{batch_number:04d}.json"
        batch_filepath = os.path.join(data_folder, batch_filename)

        with open(batch_filepath, 'w', encoding='utf-8') as f:
            json.dump({
                "batch_number": batch_number,
                "batch_size": len(batch_transactions),
                "transactions": batch_transactions
            }, f, indent=2, ensure_ascii=False)

        if batch_number % 10 == 0:
            print(f"  ✓ 已保存批次 {batch_number}/{(len(all_transactions) + batch_size - 1) // batch_size}")

    print(f"\n✅ 数据处理完成!")
    print(f"  批次文件: {batch_number} 个")
    print(f"  清算交易: {len(liquidations_found)} 笔")

    # 保存统计
    print(f"\n📊 保存统计数据...")

    if all_transactions:
        earliest = min(tx.get('timestamp', 0) for tx in all_transactions)
        latest = max(tx.get('timestamp', 0) for tx in all_transactions)
        time_span_hours = (latest - earliest) / 3600
    else:
        earliest = latest = 0
        time_span_hours = 0

    statistics = {
        "summary": {
            "total_transactions": len(all_transactions),
            "total_liquidations": len(liquidations_found),
            "time_range_days": days,
            "actual_time_span_hours": round(time_span_hours, 2),
            "fetch_time": datetime.now().isoformat(),
            "fetch_duration_seconds": round(fetch_duration, 2),
            "total_batches": batch_number,
            "earliest_transaction": datetime.fromtimestamp(earliest).isoformat() if earliest else None,
            "latest_transaction": datetime.fromtimestamp(latest).isoformat() if latest else None,
        },
        "type_counts": dict(type_counts),
        "type_percentages": {
            tx_type: round((count / len(all_transactions)) * 100, 2)
            for tx_type, count in type_counts.items()
        } if all_transactions else {},
        "liquidations_summary": [
            {
                "number": liq["liquidation_number"],
                "signature": liq["signature"],
                "timestamp": liq["timestamp"],
                "type": liq["liquidation_type"],
                "params": liq["decoded_params"]
            }
            for liq in liquidations_found
        ],
        "credits_used": batch_number * 10,  # 估算
    }

    stats_file = os.path.join(stats_folder, "statistics.json")
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(statistics, f, indent=2, ensure_ascii=False)

    # 生成摘要
    print("\n" + "=" * 120)
    print("📋 执行摘要")
    print("=" * 120)

    print(f"\n⏱️  执行时间:")
    print(f"  总耗时: {fetch_duration/60:.1f} 分钟")
    print(f"  获取速度: {len(all_transactions)/fetch_duration:.1f} 笔/秒")

    print(f"\n📊 数据统计:")
    print(f"  总交易数: {len(all_transactions):,}")
    print(f"  实际时间跨度: {time_span_hours:.1f} 小时 ({time_span_hours/24:.1f} 天)")
    print(f"  批次文件: {batch_number}")
    print(f"  清算交易: {len(liquidations_found)} 笔 {'🎯' if liquidations_found else ''}")

    print(f"\n💰 Credits 消耗:")
    print(f"  预计使用: ~{len(all_transactions) // 100:,} credits")

    print(f"\n📁 输出文件:")
    print(f"  数据: {data_folder}/ ({batch_number} 个文件)")
    print(f"  统计: {stats_folder}/statistics.json")
    print(f"  清算: {liquidations_folder}/ ({len(liquidations_found)} 个文件)")

    if liquidations_found:
        print(f"\n🎯 清算详情:")
        for liq in liquidations_found[:5]:  # 显示前 5 个
            print(f"  {liq['liquidation_number']}. {liq['timestamp']}")
            print(f"     {liq['signature'][:32]}...")
            if liq['decoded_params'].get('liquidityAmount_readable'):
                print(f"     金额: {liq['decoded_params']['liquidityAmount_readable']}")

    print(f"\n📈 交易类型分布 (前 10):")
    sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
    for i, (tx_type, count) in enumerate(sorted_types[:10], 1):
        percentage = (count / len(all_transactions)) * 100 if all_transactions else 0
        print(f"  {i:2d}. {tx_type:<45} {count:>6,} ({percentage:>5.2f}%)")

    print("\n" + "=" * 120)
    print("✅ 完成!")
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 120)


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="Kamino 数据批量获取（Helius API）")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        choices=[7, 30, 90, 365],
        help="时间范围：7, 30, 90, 或 365 天"
    )

    args = parser.parse_args()

    try:
        await batch_fetch_kamino_helius(days=args.days)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
