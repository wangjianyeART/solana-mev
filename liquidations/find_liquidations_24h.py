"""
查询过去 24 小时所有协议的清算事件
"""
import asyncio
import json
from datetime import datetime, timedelta
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.signature import Signature
import config


async def search_liquidations_for_protocol(
    client: AsyncClient,
    protocol_name: str,
    protocol_address: str,
    hours: int = 24,
    max_signatures: int = 500
):
    """搜索指定协议的清算事件"""

    print(f"\n{'='*80}")
    print(f"搜索协议: {protocol_name}")
    print(f"程序地址: {protocol_address}")
    print(f"{'='*80}")

    pubkey = Pubkey.from_string(protocol_address)
    cutoff_time = datetime.now() - timedelta(hours=hours)
    cutoff_timestamp = int(cutoff_time.timestamp())

    # 1. 获取签名
    print(f"获取过去 {hours} 小时的交易签名...")
    all_signatures = []
    before_sig = None

    for page in range(10):  # 最多 10 页
        try:
            options = {"limit": 100}
            if before_sig:
                options["before"] = before_sig

            response = await client.get_signatures_for_address(pubkey, **options)

            if not response.value:
                break

            valid_sigs = []
            for sig_info in response.value:
                if sig_info.block_time and sig_info.block_time >= cutoff_timestamp:
                    valid_sigs.append({
                        "signature": str(sig_info.signature),
                        "blockTime": sig_info.block_time,
                    })
                else:
                    all_signatures.extend(valid_sigs)
                    break

            if len(valid_sigs) < len(response.value):
                break

            all_signatures.extend(valid_sigs)

            if len(all_signatures) >= max_signatures:
                all_signatures = all_signatures[:max_signatures]
                break

            if len(response.value) < 100:
                break

            before_sig = Signature.from_string(response.value[-1].signature.__str__())
            await asyncio.sleep(0.1)

        except Exception as e:
            print(f"获取签名失败: {e}")
            break

    print(f"找到 {len(all_signatures)} 笔交易")

    # 2. 搜索清算
    print(f"搜索清算事件...")
    liquidations = []

    for i, sig_info in enumerate(all_signatures):
        if i % 50 == 0:
            print(f"  进度: {i}/{len(all_signatures)}...")

        try:
            sig_obj = Signature.from_string(sig_info["signature"])
            response = await client.get_transaction(
                sig_obj,
                encoding="jsonParsed",
                max_supported_transaction_version=0,
            )

            if not response.value or not response.value.transaction.meta:
                continue

            meta = response.value.transaction.meta
            logs = meta.log_messages if hasattr(meta, 'log_messages') else []

            # 检查清算关键词
            liquidation_logs = [
                str(log) for log in logs
                if any(kw in str(log) for kw in config.LIQUIDATION_KEYWORDS)
            ]

            if liquidation_logs:
                print(f"\n  🎯 发现清算! 签名: {sig_info['signature'][:32]}...")
                liquidations.append({
                    "signature": sig_info["signature"],
                    "blockTime": sig_info["blockTime"],
                    "logs": [str(log) for log in logs],
                    "liquidation_logs": liquidation_logs,
                })

            await asyncio.sleep(0.05)

        except Exception as e:
            continue

    print(f"\n协议 {protocol_name}: 找到 {len(liquidations)} 笔清算")
    return liquidations


async def main():
    """主函数：搜索所有协议"""

    print("=" * 80)
    print("Solana 借贷协议清算搜索")
    print("时间范围: 过去 24 小时")
    print("=" * 80)

    all_results = {}

    async with AsyncClient(config.SOLANA_RPC_URL) as client:
        for protocol_name, protocol_address in config.LENDING_PROTOCOLS.items():
            try:
                liquidations = await search_liquidations_for_protocol(
                    client,
                    protocol_name,
                    protocol_address,
                    hours=24,
                    max_signatures=500
                )
                all_results[protocol_name] = liquidations
            except Exception as e:
                print(f"\n❌ 协议 {protocol_name} 搜索失败: {e}")
                all_results[protocol_name] = []

    # 汇总结果
    print("\n" + "=" * 80)
    print("搜索结果汇总")
    print("=" * 80)

    total_liquidations = sum(len(liq) for liq in all_results.values())
    print(f"\n总计找到 {total_liquidations} 笔清算交易\n")

    for protocol, liquidations in all_results.items():
        print(f"{protocol}: {len(liquidations)} 笔清算")

        if liquidations:
            for i, liq in enumerate(liquidations, 1):
                print(f"\n  清算 #{i}:")
                print(f"  签名: {liq['signature']}")
                print(f"  时间: {datetime.fromtimestamp(liq['blockTime'])}")
                print(f"  清算日志:")
                for log in liq['liquidation_logs'][:3]:  # 只显示前3条
                    print(f"    - {log}")

    # 保存结果
    if total_liquidations > 0:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"liquidations_24h_{timestamp}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)

        print(f"\n✓ 结果已保存到: {filename}")
    else:
        print("\n💡 建议:")
        print("  - 清算事件确实很少见")
        print("  - 可以尝试查询更长时间（48-72小时）")
        print("  - 或者在市场波动大时查询")
        print("  - 考虑使用 WebSocket 实时监控")


if __name__ == "__main__":
    asyncio.run(main())
