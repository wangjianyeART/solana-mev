"""
流式增量获取 Kamino 数据
- 边获取边保存，不占用大量内存
- 每 1000 笔自动保存一个文件
- 智能增量，避免重复
"""
import asyncio
import json
import os
import base58
import struct
import aiohttp
from datetime import datetime, timedelta
from collections import defaultdict
import config


# 清算 discriminators
LIQUIDATION_DISCRIMINATORS = {
    "c2a3229ec9239fad": "liquidateObligationAndRedeemReserveCollateral",
    "39276e70d588a669": "liquidateObligationAndRedeemReserveCollateralV2",
}

METADATA_FILE = "kamino_fetch_metadata.json"


def load_metadata():
    """加载元数据"""
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
    """计算获取范围"""
    now = datetime.now()
    desired_start = now - timedelta(days=days)
    desired_start_ts = int(desired_start.timestamp())
    now_ts = int(now.timestamp())

    if not metadata["fetches"]:
        print(f"📝 首次获取数据")
        return desired_start_ts, now_ts, False

    earliest = metadata.get("earliest_timestamp")
    latest = metadata.get("latest_timestamp")

    if earliest is None:
        return desired_start_ts, now_ts, False

    if desired_start_ts >= earliest:
        print(f"📝 增量获取：只获取最新数据")
        print(f"   已有: {datetime.fromtimestamp(earliest)} 到 {datetime.fromtimestamp(latest)}")
        print(f"   新增: {datetime.fromtimestamp(latest)} 到 现在")
        return latest, now_ts, True
    else:
        print(f"📝 扩展历史数据")
        print(f"   已有: {datetime.fromtimestamp(earliest)} 到 {datetime.fromtimestamp(latest)}")
        print(f"   追加: {datetime.fromtimestamp(desired_start_ts)} 到 {datetime.fromtimestamp(earliest)}")
        return desired_start_ts, earliest, True


def check_liquidation(tx: dict) -> tuple[bool, str, str]:
    """检查清算"""
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


class StreamingSaver:
    """流式保存器 - 边获取边保存"""

    def __init__(self, data_folder, liquidations_folder, batch_size=1000):
        self.data_folder = data_folder
        self.liquidations_folder = liquidations_folder
        self.batch_size = batch_size

        self.current_batch = []
        self.batch_number = 0
        self.total_count = 0
        self.liquidations_count = 0
        self.type_counts = defaultdict(int)
        self.liquidations_list = []

    def add_transaction(self, tx: dict):
        """添加交易（自动保存批次）"""
        self.current_batch.append(tx)
        self.total_count += 1

        # 统计类型
        tx_type = tx.get('type', 'UNKNOWN')
        self.type_counts[tx_type] += 1

        # 检查清算
        is_liq, liq_type, inst_data = check_liquidation(tx)
        if is_liq:
            self.liquidations_count += 1
            self._save_liquidation(tx, liq_type, inst_data)

        # 达到批次大小，保存
        if len(self.current_batch) >= self.batch_size:
            self._save_batch()

    def _save_batch(self):
        """保存当前批次"""
        if not self.current_batch:
            return

        self.batch_number += 1
        batch_filename = f"kamino_batch_{self.batch_number:04d}.json"
        batch_filepath = os.path.join(self.data_folder, batch_filename)

        batch_data = {
            "batch_number": self.batch_number,
            "batch_size": len(self.current_batch),
            "transactions": self.current_batch
        }

        with open(batch_filepath, 'w', encoding='utf-8') as f:
            json.dump(batch_data, f, indent=2, ensure_ascii=False)

        print(f"  💾 批次 {self.batch_number} 已保存: {len(self.current_batch)} 笔")

        # 清空当前批次
        self.current_batch = []

    def _save_liquidation(self, tx: dict, liq_type: str, inst_data: str):
        """保存清算"""
        params = decode_liquidation_params(inst_data) if inst_data else {}

        liquidation_data = {
            "liquidation_number": self.liquidations_count,
            "signature": tx.get('signature'),
            "blockTime": tx.get('timestamp'),
            "timestamp": datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat(),
            "liquidation_type": liq_type,
            "instruction_data": inst_data,
            "decoded_params": params,
            "transaction": tx,
        }

        self.liquidations_list.append(liquidation_data)

        # 立即保存
        liq_filename = f"liquidation_{self.liquidations_count:03d}.json"
        liq_filepath = os.path.join(self.liquidations_folder, liq_filename)
        with open(liq_filepath, 'w', encoding='utf-8') as f:
            json.dump(liquidation_data, f, indent=2, ensure_ascii=False)

        print(f"\n  🎯 清算 #{self.liquidations_count}!")
        print(f"     {tx.get('signature', '')[:32]}...")
        if params.get('liquidityAmount_readable'):
            print(f"     金额: {params['liquidityAmount_readable']}")

    def finalize(self):
        """完成保存（保存剩余数据）"""
        if self.current_batch:
            self._save_batch()

        return {
            "total_transactions": self.total_count,
            "total_batches": self.batch_number,
            "total_liquidations": self.liquidations_count,
            "type_counts": dict(self.type_counts),
            "liquidations": self.liquidations_list
        }


async def fetch_and_save_streaming(
    api_key: str,
    address: str,
    start_timestamp: int,
    end_timestamp: int,
    saver: StreamingSaver
):
    """流式获取并保存"""
    base_url = "https://api.helius.xyz/v0"
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

                        # 逐个处理并保存
                        reached_end = False
                        for tx in data:
                            tx_timestamp = tx.get('timestamp', 0)
                            if start_timestamp <= tx_timestamp <= end_timestamp:
                                saver.add_transaction(tx)
                            elif tx_timestamp < start_timestamp:
                                reached_end = True
                                break

                        if reached_end:
                            break

                        if page % 5 == 0:
                            print(f"  📊 进度: 已处理 {saver.total_count} 笔 | 清算 {saver.liquidations_count}")

                        if len(data) < 100:
                            break

                        before_signature = data[-1].get('signature')
                        await asyncio.sleep(0.2)

                    elif response.status == 429:
                        print(f"  ⏳ 速率限制，等待...")
                        await asyncio.sleep(5)
                    else:
                        print(f"  ❌ 请求失败: {response.status}")
                        break

            except Exception as e:
                print(f"  ⚠️  请求错误: {e}")
                break


async def fetch_kamino_streaming(days: int):
    """主函数：流式获取"""
    print("=" * 120)
    print(f"Kamino 流式增量获取系统")
    print("=" * 120)
    print(f"⏰ 开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📅 范围: 过去 {days} 天")

    # 元数据
    metadata = load_metadata()
    start_ts, end_ts, is_incremental = calculate_fetch_range(days, metadata)

    if start_ts >= end_ts:
        print("\n✅ 数据已最新")
        return

    print(f"⏰ 获取: {datetime.fromtimestamp(start_ts)} 到 {datetime.fromtimestamp(end_ts)}")
    print("=" * 120)

    # 创建文件夹
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_folder = f"kamino_data_{days}d_{timestamp}"
    stats_folder = f"kamino_stats_{days}d_{timestamp}"
    liquidations_folder = f"kamino_liquidations_{timestamp}"

    os.makedirs(data_folder, exist_ok=True)
    os.makedirs(stats_folder, exist_ok=True)
    os.makedirs(liquidations_folder, exist_ok=True)

    print(f"\n📁 文件夹: {data_folder}/")

    # 流式保存器
    saver = StreamingSaver(data_folder, liquidations_folder, batch_size=1000)

    # 开始获取
    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    print(f"\n⏳ 开始流式获取...\n")

    start_time = datetime.now()

    await fetch_and_save_streaming(
        api_key=config.HELIUS_API_KEY,
        address=kamino_address,
        start_timestamp=start_ts,
        end_timestamp=end_ts,
        saver=saver
    )

    # 完成
    result = saver.finalize()
    duration = (datetime.now() - start_time).total_seconds()

    print(f"\n✅ 完成!")

    # 更新元数据
    if result["total_transactions"] > 0:
        # 这里需要从保存的批次文件中获取实际的时间范围
        # 简化处理：使用请求的时间范围
        if metadata["earliest_timestamp"] is None:
            metadata["earliest_timestamp"] = start_ts
            metadata["latest_timestamp"] = end_ts
        else:
            metadata["earliest_timestamp"] = min(metadata["earliest_timestamp"], start_ts)
            metadata["latest_timestamp"] = max(metadata["latest_timestamp"], end_ts)

    fetch_record = {
        "fetch_time": datetime.now().isoformat(),
        "days_requested": days,
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
        "transactions_fetched": result["total_transactions"],
        "liquidations_found": result["total_liquidations"],
        "data_folder": data_folder,
        "duration_seconds": duration,
    }

    metadata["fetches"].append(fetch_record)
    metadata["total_transactions"] += result["total_transactions"]
    metadata["data_folders"].append(data_folder)

    save_metadata(metadata)

    # 保存统计
    statistics = {
        "summary": {
            "fetch_time": datetime.now().isoformat(),
            "days_requested": days,
            "new_transactions": result["total_transactions"],
            "new_liquidations": result["total_liquidations"],
            "batches": result["total_batches"],
            "duration_seconds": duration,
        },
        "cumulative": {
            "total_transactions": metadata["total_transactions"],
            "total_fetches": len(metadata["fetches"]),
        },
        "type_counts": result["type_counts"],
    }

    with open(os.path.join(stats_folder, "statistics.json"), 'w') as f:
        json.dump(statistics, f, indent=2, ensure_ascii=False)

    # 摘要
    print("\n" + "=" * 120)
    print("📋 摘要")
    print("=" * 120)
    print(f"  新增交易: {result['total_transactions']:,}")
    print(f"  批次文件: {result['total_batches']}")
    print(f"  清算数量: {result['total_liquidations']}")
    print(f"  耗时: {duration/60:.1f} 分钟")
    print(f"  速度: {result['total_transactions']/duration:.1f} 笔/秒" if duration > 0 else "")
    print(f"\n  累计: {metadata['total_transactions']:,} 笔")
    print("=" * 120)


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if args.reset and os.path.exists(METADATA_FILE):
        os.remove(METADATA_FILE)
        print("✅ 已重置")

    await fetch_kamino_streaming(days=args.days)


if __name__ == "__main__":
    asyncio.run(main())
