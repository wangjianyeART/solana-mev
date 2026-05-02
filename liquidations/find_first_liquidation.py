"""
扩大范围搜索清算，找到第一笔就立即停止并保存
"""
import asyncio
import json
import base58
from datetime import datetime, timedelta
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.signature import Signature
import config


# Kamino Lending 清算指令的 discriminators
LIQUIDATION_DISCRIMINATORS = [
    "c2a3229ec9239fad",  # liquidateObligationAndRedeemReserveCollateral
    "39276e70d588a669",  # liquidateObligationAndRedeemReserveCollateralV2
]


def is_liquidation_instruction(instruction_data_b58: str) -> tuple[bool, str]:
    """
    检查指令数据是否为清算指令

    Returns:
        (is_liquidation, instruction_type)
    """
    try:
        data_bytes = base58.b58decode(instruction_data_b58)

        if len(data_bytes) < 8:
            return False, ""

        discriminator = data_bytes[:8].hex()

        if discriminator == LIQUIDATION_DISCRIMINATORS[0]:
            return True, "liquidateObligationAndRedeemReserveCollateral"
        elif discriminator == LIQUIDATION_DISCRIMINATORS[1]:
            return True, "liquidateObligationAndRedeemReserveCollateralV2"

        return False, ""

    except:
        return False, ""


async def search_for_first_liquidation(
    protocol_name: str,
    protocol_address: str,
    max_hours: int = 72,
    max_transactions: int = 5000
):
    """
    搜索第一笔清算交易

    Args:
        protocol_name: 协议名称
        protocol_address: 程序地址
        max_hours: 最大搜索时间范围（小时）
        max_transactions: 最大搜索交易数

    Returns:
        清算交易数据或 None
    """

    print("=" * 120)
    print(f"搜索 {protocol_name} 的清算交易")
    print("=" * 120)
    print(f"程序地址: {protocol_address}")
    print(f"最大搜索范围: {max_hours} 小时")
    print(f"最大搜索交易数: {max_transactions}")
    print(f"清算 Discriminators:")
    for disc in LIQUIDATION_DISCRIMINATORS:
        print(f"  - {disc}")
    print("=" * 120)

    async with AsyncClient(config.SOLANA_RPC_URL) as client:
        # 1. 获取交易签名
        print("\n步骤 1: 获取交易签名列表...")
        pubkey = Pubkey.from_string(protocol_address)

        cutoff_time = datetime.now() - timedelta(hours=max_hours)
        cutoff_timestamp = int(cutoff_time.timestamp())

        all_signatures = []
        before_sig = None
        page = 0

        while len(all_signatures) < max_transactions:
            page += 1
            print(f"  查询第 {page} 页签名...")

            try:
                options = {"limit": 100}
                if before_sig:
                    options["before"] = before_sig

                response = await client.get_signatures_for_address(pubkey, **options)

                if not response.value:
                    print(f"  没有更多签名")
                    break

                valid_sigs = []
                reached_limit = False

                for sig_info in response.value:
                    if sig_info.block_time and sig_info.block_time >= cutoff_timestamp:
                        valid_sigs.append({
                            "signature": str(sig_info.signature),
                            "blockTime": sig_info.block_time,
                        })
                    else:
                        reached_limit = True
                        break

                all_signatures.extend(valid_sigs)
                print(f"  当前总数: {len(all_signatures)} 笔")

                if reached_limit or len(response.value) < 100:
                    break

                before_sig = Signature.from_string(response.value[-1].signature.__str__())
                await asyncio.sleep(0.1)

            except Exception as e:
                print(f"  获取签名失败: {e}")
                break

        print(f"\n✓ 共获取 {len(all_signatures)} 笔签名")

        # 2. 逐个检查交易是否为清算
        print(f"\n步骤 2: 检查交易指令...")
        print(f"正在搜索清算交易（找到第一笔就停止）...\n")

        for i, sig_info in enumerate(all_signatures):
            if (i + 1) % 50 == 0:
                print(f"  进度: {i + 1}/{len(all_signatures)} ({(i+1)/len(all_signatures)*100:.1f}%)")

            signature = sig_info["signature"]

            try:
                sig_obj = Signature.from_string(signature)
                response = await client.get_transaction(
                    sig_obj,
                    encoding="jsonParsed",
                    max_supported_transaction_version=0,
                )

                if not response.value:
                    continue

                # 检查指令
                tx = response.value.transaction
                if not tx or not tx.transaction or not tx.transaction.message:
                    continue

                instructions = tx.transaction.message.instructions

                # 检查每个指令
                for inst in instructions:
                    # 获取指令数据
                    inst_data = None

                    # 处理不同的指令格式
                    if hasattr(inst, 'data'):
                        inst_data = inst.data
                    elif isinstance(inst, dict):
                        inst_data = inst.get('data')

                    if inst_data:
                        is_liq, liq_type = is_liquidation_instruction(inst_data)

                        if is_liq:
                            # 找到清算！
                            print("\n" + "=" * 120)
                            print("🎯 找到清算交易！")
                            print("=" * 120)

                            liquidation_data = {
                                "found": True,
                                "protocol": protocol_name,
                                "signature": signature,
                                "blockTime": sig_info["blockTime"],
                                "timestamp": datetime.fromtimestamp(sig_info["blockTime"]).isoformat(),
                                "liquidation_type": liq_type,
                                "transaction_data": {
                                    "signature": signature,
                                    "blockTime": sig_info["blockTime"],
                                    "slot": tx.slot if hasattr(tx, 'slot') else None,
                                    "meta": str(tx.meta) if hasattr(tx, 'meta') else None,
                                    "instructions": [str(inst) for inst in instructions],
                                }
                            }

                            # 尝试获取更详细的信息
                            if hasattr(tx, 'meta') and tx.meta:
                                meta = tx.meta
                                if hasattr(meta, 'log_messages'):
                                    liquidation_data["logs"] = [str(log) for log in meta.log_messages]

                            print(f"\n签名: {signature}")
                            print(f"时间: {datetime.fromtimestamp(sig_info['blockTime'])}")
                            print(f"类型: {liq_type}")
                            print(f"在第 {i+1} 笔交易中找到")

                            return liquidation_data

                await asyncio.sleep(0.05)

            except Exception as e:
                if (i + 1) % 100 == 0:
                    print(f"  处理交易 {i+1} 时出错: {e}")
                continue

        print(f"\n未找到清算交易 (搜索了 {len(all_signatures)} 笔交易)")
        return None


async def main():
    """主函数"""

    print("\n" + "=" * 120)
    print("Kamino Lending 清算搜索程序")
    print("=" * 120)
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标: 找到第一笔清算交易就停止")
    print("=" * 120)

    # 搜索 Kamino
    kamino_address = config.LENDING_PROTOCOLS["Kamino"]

    liquidation = await search_for_first_liquidation(
        protocol_name="Kamino",
        protocol_address=kamino_address,
        max_hours=168,  # 7 天
        max_transactions=10000
    )

    if liquidation:
        # 保存清算数据
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"first_liquidation_found_{timestamp}.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(liquidation, f, indent=2, ensure_ascii=False)

        print(f"\n" + "=" * 120)
        print("清算数据已保存")
        print("=" * 120)
        print(f"文件: {filename}")
        print(f"\n清算信息摘要:")
        print(f"  协议: {liquidation['protocol']}")
        print(f"  签名: {liquidation['signature']}")
        print(f"  时间: {liquidation['timestamp']}")
        print(f"  类型: {liquidation['liquidation_type']}")

        if 'logs' in liquidation:
            print(f"\n程序日志:")
            for log in liquidation['logs'][:10]:
                print(f"  {log}")

        print("\n✓ 成功找到并保存清算交易!")

    else:
        print("\n" + "=" * 120)
        print("未找到清算交易")
        print("=" * 120)
        print("建议:")
        print("  1. 扩大搜索范围（增加 max_hours）")
        print("  2. 查询其他协议")
        print("  3. 等待市场波动时再搜索")

    print("\n" + "=" * 120)
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 120)


if __name__ == "__main__":
    asyncio.run(main())
