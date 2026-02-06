"""
使用 Helius API 搜索清算（更快更稳定）
找到第一笔就停止
"""
import asyncio
import json
import base58
from datetime import datetime
from helius_client import HeliusClient
import config


# 清算 discriminators
LIQUIDATION_DISCRIMINATORS = {
    "c2a3229ec9239fad": "liquidateObligationAndRedeemReserveCollateral",
    "39276e70d588a669": "liquidateObligationAndRedeemReserveCollateralV2",
}


def check_for_liquidation(tx: dict) -> tuple[bool, str, str]:
    """
    检查交易是否为清算

    Returns:
        (is_liquidation, liquidation_type, instruction_data)
    """
    # 方法 1: 检查 Helius 的 type 字段
    if tx.get('type') == 'LIQUIDATE':
        return True, "LIQUIDATE (Helius)", ""

    # 方法 2: 检查指令 discriminator
    instructions = tx.get('instructions', [])
    for inst in instructions:
        inst_data = inst.get('data', '')

        if inst_data:
            try:
                data_bytes = base58.b58decode(inst_data)
                if len(data_bytes) >= 8:
                    discriminator = data_bytes[:8].hex()

                    if discriminator in LIQUIDATION_DISCRIMINATORS:
                        return True, LIQUIDATION_DISCRIMINATORS[discriminator], inst_data
            except:
                pass

    # 方法 3: 检查日志
    logs = tx.get('logs', [])
    for log in logs:
        if 'liquidat' in log.lower():
            return True, "Liquidation (from logs)", ""

    return False, "", ""


async def search_all_protocols(hours: int = 168):
    """
    搜索所有协议的清算
    """
    print("=" * 120)
    print("多协议清算搜索")
    print("=" * 120)
    print(f"时间范围: 过去 {hours} 小时 ({hours/24:.1f} 天)")
    print(f"搜索协议: {', '.join(config.LENDING_PROTOCOLS.keys())}")
    print("=" * 120)

    async with HeliusClient(config.HELIUS_API_KEY) as client:

        for protocol_name, protocol_address in config.LENDING_PROTOCOLS.items():
            print(f"\n\n{'='*120}")
            print(f"搜索协议: {protocol_name}")
            print(f"{'='*120}")
            print(f"程序地址: {protocol_address}")

            try:
                # 获取交易
                print(f"\n获取过去 {hours/24:.1f} 天的交易...")
                transactions = await client.get_transactions_in_time_range(
                    address=protocol_address,
                    hours=hours
                )

                print(f"✓ 获取到 {len(transactions)} 笔交易")
                print(f"\n逐个检查...")

                # 检查每笔交易
                for i, tx in enumerate(transactions):
                    if (i + 1) % 100 == 0:
                        print(f"  进度: {i+1}/{len(transactions)} ({(i+1)/len(transactions)*100:.1f}%)")

                    is_liq, liq_type, inst_data = check_for_liquidation(tx)

                    if is_liq:
                        # 找到清算！
                        print("\n" + "🎯" * 40)
                        print("找到清算交易！")
                        print("🎯" * 40)

                        liquidation_data = {
                            "found": True,
                            "protocol": protocol_name,
                            "protocol_address": protocol_address,
                            "signature": tx.get('signature'),
                            "timestamp": datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat(),
                            "liquidation_type": liq_type,
                            "instruction_data": inst_data,
                            "transaction": tx,
                        }

                        print(f"\n协议: {protocol_name}")
                        print(f"签名: {tx.get('signature')}")
                        print(f"时间: {datetime.fromtimestamp(tx.get('timestamp', 0))}")
                        print(f"类型: {liq_type}")
                        print(f"在第 {i+1} 笔交易中找到")

                        # 显示交易摘要
                        print(f"\n交易摘要:")
                        print(f"  费用: {tx.get('fee', 0) / 1e9:.6f} SOL")
                        print(f"  费用支付者: {tx.get('feePayer', 'N/A')[:20]}...")

                        token_transfers = tx.get('tokenTransfers', [])
                        if token_transfers:
                            print(f"  代币转账: {len(token_transfers)} 笔")
                            for j, transfer in enumerate(token_transfers[:3], 1):
                                print(f"    {j}. {transfer.get('tokenAmount', 0)} {transfer.get('mint', 'N/A')[:8]}...")

                        # 保存
                        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"liquidation_{protocol_name}_{timestamp_str}.json"

                        with open(filename, 'w', encoding='utf-8') as f:
                            json.dump(liquidation_data, f, indent=2, ensure_ascii=False)

                        print(f"\n✓ 清算数据已保存到: {filename}")

                        return liquidation_data

                print(f"\n{protocol_name}: 未找到清算")

            except Exception as e:
                print(f"\n❌ {protocol_name} 搜索失败: {e}")
                import traceback
                traceback.print_exc()
                continue

    return None


async def main():
    """主函数"""
    print("\n" + "=" * 120)
    print("Solana 借贷协议清算搜索")
    print("=" * 120)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"数据源: Helius API")
    print("=" * 120)

    # 搜索清算（7天范围）
    liquidation = await search_all_protocols(hours=168)

    print("\n\n" + "=" * 120)

    if liquidation:
        print("✓ 成功找到清算交易！")
    else:
        print("❌ 未找到清算交易")
        print("\n这说明:")
        print("  - 过去 7 天内这些协议可能确实没有清算发生")
        print("  - 或者清算非常罕见")
        print("  - 建议在市场波动大时查询")

    print("=" * 120)
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 120)


if __name__ == "__main__":
    asyncio.run(main())
