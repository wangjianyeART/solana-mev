"""
测试版本：使用 Helius API 获取 1 小时数据
验证程序正确性
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


async def test_helius_1hour():
    """测试获取 1 小时数据"""
    print("=" * 100)
    print("Helius API 测试 - 获取 1 小时数据")
    print("=" * 100)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 创建测试文件夹
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    test_folder = f"test_helius_{timestamp}"
    os.makedirs(test_folder, exist_ok=True)

    print(f"测试文件夹: {test_folder}/")

    # 配置
    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    base_url = "https://api.helius.xyz/v0"
    cutoff_time = datetime.now() - timedelta(hours=1)
    cutoff_timestamp = int(cutoff_time.timestamp())

    print(f"\nKamino 地址: {kamino_address}")
    print(f"时间范围: 过去 1 小时")
    print(f"截止时间: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 获取数据
    print(f"\n开始获取交易...")

    all_transactions = []
    liquidations = []
    before_signature = None
    page = 0

    start_time = datetime.now()

    async with aiohttp.ClientSession() as session:
        while True:
            page += 1

            url = f"{base_url}/addresses/{kamino_address}/transactions"
            params = {
                "api-key": config.HELIUS_API_KEY,
                "limit": 100,
            }

            if before_signature:
                params["before"] = before_signature

            try:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        data = await response.json()

                        if not data or not isinstance(data, list):
                            print(f"  第 {page} 页: 无数据")
                            break

                        print(f"  第 {page} 页: {len(data)} 笔交易")

                        # 过滤时间范围
                        valid_txs = []
                        for tx in data:
                            tx_timestamp = tx.get('timestamp', 0)
                            if tx_timestamp >= cutoff_timestamp:
                                valid_txs.append(tx)

                                # 检查清算
                                is_liq, liq_type, inst_data = check_liquidation(tx)
                                if is_liq:
                                    print(f"    🎯 发现清算! 签名: {tx.get('signature', '')[:32]}...")
                                    params_decoded = decode_liquidation_params(inst_data) if inst_data else {}
                                    liquidations.append({
                                        "signature": tx.get('signature'),
                                        "type": liq_type,
                                        "timestamp": datetime.fromtimestamp(tx_timestamp).isoformat(),
                                        "params": params_decoded
                                    })
                            else:
                                # 超出时间范围
                                print(f"  到达时间边界")
                                all_transactions.extend(valid_txs)
                                break

                        if len(valid_txs) < len(data):
                            # 到达边界
                            break

                        all_transactions.extend(valid_txs)

                        if len(data) < 100:
                            print(f"  第 {page} 页: 最后一页")
                            break

                        before_signature = data[-1].get('signature')
                        await asyncio.sleep(0.2)

                    elif response.status == 429:
                        print(f"  速率限制，等待 5 秒...")
                        await asyncio.sleep(5)
                    else:
                        print(f"  请求失败: {response.status}")
                        error_text = await response.text()
                        print(f"  错误: {error_text[:200]}")
                        break

            except Exception as e:
                print(f"  请求错误: {e}")
                break

    duration = (datetime.now() - start_time).total_seconds()

    # 统计
    print(f"\n" + "=" * 100)
    print("测试结果")
    print("=" * 100)

    print(f"\n获取统计:")
    print(f"  总交易数: {len(all_transactions)}")
    print(f"  清算数量: {len(liquidations)}")
    print(f"  查询页数: {page}")
    print(f"  耗时: {duration:.1f} 秒")
    print(f"  速度: {len(all_transactions)/duration:.1f} 笔/秒" if duration > 0 else "  速度: N/A")

    # 交易类型统计
    type_counts = defaultdict(int)
    for tx in all_transactions:
        tx_type = tx.get('type', 'UNKNOWN')
        type_counts[tx_type] += 1

    print(f"\n交易类型分布:")
    sorted_types = sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
    for tx_type, count in sorted_types:
        percentage = (count / len(all_transactions)) * 100 if all_transactions else 0
        print(f"  {tx_type:<45} {count:>4} ({percentage:>5.2f}%)")

    # 清算详情
    if liquidations:
        print(f"\n清算详情:")
        for i, liq in enumerate(liquidations, 1):
            print(f"  {i}. {liq['timestamp']}")
            print(f"     签名: {liq['signature']}")
            print(f"     类型: {liq['type']}")
            if liq['params']:
                print(f"     参数: {liq['params']}")

    # 时间分布
    if all_transactions:
        timestamps = [tx.get('timestamp', 0) for tx in all_transactions]
        earliest = min(timestamps)
        latest = max(timestamps)
        span_minutes = (latest - earliest) / 60

        print(f"\n时间分布:")
        print(f"  最早: {datetime.fromtimestamp(earliest).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  最新: {datetime.fromtimestamp(latest).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  跨度: {span_minutes:.1f} 分钟")

    # 保存测试结果
    test_result = {
        "test_time": datetime.now().isoformat(),
        "duration_seconds": duration,
        "total_transactions": len(all_transactions),
        "total_liquidations": len(liquidations),
        "type_counts": dict(type_counts),
        "liquidations": liquidations,
        "sample_transactions": all_transactions[:5]  # 保存前 5 笔
    }

    result_file = os.path.join(test_folder, "test_result.json")
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(test_result, f, indent=2, ensure_ascii=False)

    print(f"\n测试结果已保存: {result_file}")

    # Credits 估算
    credits_used = page
    print(f"\nCredits 使用:")
    print(f"  本次测试: ~{credits_used} credits")
    print(f"  7 天预估: ~{credits_used * 168} credits")
    print(f"  1 年预估: ~{credits_used * 8760} credits")

    print("\n" + "=" * 100)
    print("✅ 测试完成!")
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(test_helius_1hour())
