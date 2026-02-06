"""
增量获取 Kamino 数据 - 智能避免重复
- 记录已获取的时间范围
- 自动跳过已有数据
- 只获取新数据
"""
import asyncio
import json
import os
import base58
import struct
import aiohttp
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
import config


# 清算 discriminators
LIQUIDATION_DISCRIMINATORS = {
    "c2a3229ec9239fad": "liquidateObligationAndRedeemReserveCollateral",
    "39276e70d588a669": "liquidateObligationAndRedeemReserveCollateralV2",
}

# 元数据文件
METADATA_FILE = "kamino_fetch_metadata.json"


def load_metadata():
    """加载已获取数据的元数据"""
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            return json.load(f)
    return {
        "fetches": [],
        "total_transactions": 0,
        "earliest_timestamp": None,
        "latest_timestamp": None,
        "data_folders": []
    }


def save_metadata(metadata):
    """保存元数据"""
    with open(METADATA_FILE, 'w') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def calculate_fetch_range(days: int, metadata: dict) -> tuple[int, int, bool]:
    """
    计算需要获取的时间范围

    Returns:
        (start_timestamp, end_timestamp, is_incremental)
    """
    now = datetime.now()
    desired_start = now - timedelta(days=days)
    desired_start_ts = int(desired_start.timestamp())
    now_ts = int(now.timestamp())

    if not metadata["fetches"]:
        # 第一次获取
        print(f"📝 首次获取数据")
        return desired_start_ts, now_ts, False

    # 获取已有的最早时间
    earliest = metadata.get("earliest_timestamp")
    latest = metadata.get("latest_timestamp")

    if earliest is None:
        return desired_start_ts, now_ts, False

    # 检查是否需要增量获取
    if desired_start_ts >= earliest:
        # 所需范围在已有数据内，只需更新到最新
        print(f"📝 增量获取：只获取最新数据")
        print(f"   已有数据: {datetime.fromtimestamp(earliest)} 到 {datetime.fromtimestamp(latest)}")
        print(f"   新数据范围: {datetime.fromtimestamp(latest)} 到 {datetime.fromtimestamp(now_ts)}")
        return latest, now_ts, True
    else:
        # 需要获取更早的数据
        print(f"📝 扩展历史数据")
        print(f"   已有数据: {datetime.fromtimestamp(earliest)} 到 {datetime.fromtimestamp(latest)}")
        print(f"   需要追加: {datetime.fromtimestamp(desired_start_ts)} 到 {datetime.fromtimestamp(earliest)}")
        return desired_start_ts, earliest, True


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


async def fetch_transactions_range(
    api_key: str,
    address: str,
    start_timestamp: int,
    end_timestamp: int
):
    """获取指定时间范围的交易"""
    base_url = "https://api.helius.xyz/v0"

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
                            if start_timestamp <= tx_timestamp <= end_timestamp:
                                valid_txs.append(tx)
                            elif tx_timestamp < start_timestamp:
                                # 已经超出范围，停止
                                all_transactions.extend(valid_txs)
                                return all_transactions

                        all_transactions.extend(valid_txs)

                        if page % 10 == 0:
                            print(f"  已获取 {len(all_transactions)} 笔交易...")

                        if len(data) < 100:
                            break

                        before_signature = data[-1].get('signature')
                        await asyncio.sleep(0.2)

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


async def fetch_kamino_incremental(days: int):
    """增量获取 Kamino 数据"""
    print("=" * 120)
    print(f"Kamino 增量数据获取系统")
    print("=" * 120)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"请求范围: 过去 {days} 天")

    # 加载元数据
    metadata = load_metadata()

    # 计算需要获取的范围
    start_ts, end_ts, is_incremental = calculate_fetch_range(days, metadata)

    if start_ts >= end_ts:
        print("\n✅ 数据已是最新，无需获取")
        print(f"   现有数据: {datetime.fromtimestamp(metadata['earliest_timestamp'])} 到 {datetime.fromtimestamp(metadata['latest_timestamp'])}")
        return

    print(f"⏰ 获取范围: {datetime.fromtimestamp(start_ts)} 到 {datetime.fromtimestamp(end_ts)}")
    print(f"   时间跨度: {(end_ts - start_ts) / 3600:.1f} 小时")
    print("=" * 120)

    # 创建文件夹
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_folder = f"kamino_data_{days}d_{timestamp}"
    stats_folder = f"kamino_stats_{days}d_{timestamp}"
    liquidations_folder = f"kamino_liquidations_{timestamp}"

    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(stats_folder, exist_ok=True)
    os.makedirs(liquidations_folder, exist_ok=True)

    print(f"\n📁 文件夹:")
    print(f"  数据: {data_folder}/")
    print(f"  统计: {stats_folder}/")
    print(f"  清算: {liquidations_folder}/")

    # 获取数据
    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    print(f"\n📍 Kamino: {kamino_address}")
    print(f"\n⏳ 开始获取...")

    start_time = datetime.now()

    all_transactions = await fetch_transactions_range(
        api_key=config.HELIUS_API_KEY,
        address=kamino_address,
        start_timestamp=start_ts,
        end_timestamp=end_ts
    )

    duration = (datetime.now() - start_time).total_seconds()

    print(f"\n✅ 获取完成!")
    print(f"  新增交易: {len(all_transactions)}")
    print(f"  耗时: {duration:.1f} 秒")
    print(f"  速度: {len(all_transactions)/duration:.1f} 笔/秒" if duration > 0 else "")

    # 处理数据
    print(f"\n⚙️  处理数据...")

    liquidations_found = []
    type_counts = defaultdict(int)
    batch_size = 1000
    batch_number = 0

    # 检查是否有已存在的清算文件夹
    existing_liq_count = 0
    if metadata.get("liquidations_folder"):
        existing_liq_folder = metadata["liquidations_folder"]
        if os.path.exists(existing_liq_folder):
            existing_liq_count = len([f for f in os.listdir(existing_liq_folder) if f.endswith('.json')])

    for i in range(0, len(all_transactions), batch_size):
        batch_transactions = all_transactions[i:i + batch_size]
        batch_number += 1

        for tx in batch_transactions:
            tx_type = tx.get('type', 'UNKNOWN')
            type_counts[tx_type] += 1

            is_liq, liq_type, inst_data = check_liquidation(tx)

            if is_liq:
                liq_number = existing_liq_count + len(liquidations_found) + 1
                print(f"\n  🎯 发现清算 #{liq_number}!")
                print(f"     签名: {tx.get('signature', '')[:32]}...")
                print(f"     类型: {liq_type}")

                params = decode_liquidation_params(inst_data) if inst_data else {}

                liquidation_data = {
                    "liquidation_number": liq_number,
                    "signature": tx.get('signature'),
                    "blockTime": tx.get('timestamp'),
                    "timestamp": datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat(),
                    "liquidation_type": liq_type,
                    "instruction_data": inst_data,
                    "decoded_params": params,
                    "transaction": tx,
                }

                liquidations_found.append(liquidation_data)

                liq_filename = f"liquidation_{liq_number:03d}.json"
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

    print(f"\n✅ 数据处理完成!")
    print(f"  批次文件: {batch_number}")
    print(f"  新增清算: {len(liquidations_found)}")

    # 更新元数据
    if all_transactions:
        new_earliest = min(tx.get('timestamp', 0) for tx in all_transactions)
        new_latest = max(tx.get('timestamp', 0) for tx in all_transactions)

        # 更新全局时间范围
        if metadata["earliest_timestamp"] is None:
            metadata["earliest_timestamp"] = new_earliest
            metadata["latest_timestamp"] = new_latest
        else:
            metadata["earliest_timestamp"] = min(metadata["earliest_timestamp"], new_earliest)
            metadata["latest_timestamp"] = max(metadata["latest_timestamp"], new_latest)

    # 记录本次获取
    fetch_record = {
        "fetch_time": datetime.now().isoformat(),
        "days_requested": days,
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
        "transactions_fetched": len(all_transactions),
        "liquidations_found": len(liquidations_found),
        "data_folder": data_folder,
        "stats_folder": stats_folder,
        "liquidations_folder": liquidations_folder,
        "duration_seconds": duration,
    }

    metadata["fetches"].append(fetch_record)
    metadata["total_transactions"] += len(all_transactions)
    metadata["data_folders"].append(data_folder)

    if liquidations_found:
        metadata["liquidations_folder"] = liquidations_folder

    save_metadata(metadata)

    # 保存统计
    statistics = {
        "summary": {
            "fetch_time": datetime.now().isoformat(),
            "days_requested": days,
            "is_incremental": is_incremental,
            "new_transactions": len(all_transactions),
            "new_liquidations": len(liquidations_found),
            "duration_seconds": duration,
        },
        "cumulative": {
            "total_transactions": metadata["total_transactions"],
            "earliest": datetime.fromtimestamp(metadata["earliest_timestamp"]).isoformat() if metadata["earliest_timestamp"] else None,
            "latest": datetime.fromtimestamp(metadata["latest_timestamp"]).isoformat() if metadata["latest_timestamp"] else None,
            "total_fetches": len(metadata["fetches"]),
        },
        "type_counts": dict(type_counts),
        "liquidations": [
            {
                "number": liq["liquidation_number"],
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

    # 摘要
    print("\n" + "=" * 120)
    print("📋 执行摘要")
    print("=" * 120)

    print(f"\n本次获取:")
    print(f"  新增交易: {len(all_transactions):,}")
    print(f"  新增清算: {len(liquidations_found)}")
    print(f"  耗时: {duration/60:.1f} 分钟")
    print(f"  批次文件: {batch_number}")

    print(f"\n累计数据:")
    print(f"  总交易数: {metadata['total_transactions']:,}")
    print(f"  时间范围: {datetime.fromtimestamp(metadata['earliest_timestamp']).strftime('%Y-%m-%d')} 到 {datetime.fromtimestamp(metadata['latest_timestamp']).strftime('%Y-%m-%d')}")
    print(f"  总获取次数: {len(metadata['fetches'])}")
    print(f"  数据文件夹: {len(metadata['data_folders'])} 个")

    print(f"\n💰 Credits:")
    print(f"  本次: ~{len(all_transactions) // 100} credits")

    print("\n" + "=" * 120)
    print("✅ 完成!")
    print("=" * 120)

    print(f"\n💡 提示:")
    print(f"  - 元数据已保存到: {METADATA_FILE}")
    print(f"  - 下次运行将自动跳过已获取的数据")
    print(f"  - 要获取 30 天数据，运行: python {__file__} --days 30")


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="Kamino 增量数据获取")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="时间范围（天）"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="重置元数据，从头开始"
    )

    args = parser.parse_args()

    if args.reset:
        if os.path.exists(METADATA_FILE):
            os.remove(METADATA_FILE)
            print("✅ 元数据已重置")

    try:
        await fetch_kamino_incremental(days=args.days)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
