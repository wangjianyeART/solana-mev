"""
使用 RPC 直接获取交易并解析日志，查找清算事件
"""
import asyncio
import json
from datetime import datetime, timedelta
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey
from solders.signature import Signature
import config


async def fetch_kamino_with_logs():
    """使用 RPC 获取 Kamino 交易并解析日志"""

    kamino_address = config.LENDING_PROTOCOLS["Kamino"]
    print(f"Kamino 程序地址: {kamino_address}")
    print(f"RPC URL: {config.SOLANA_RPC_URL}")
    print(f"查询范围: 过去 1 小时")
    print("=" * 100)

    async with AsyncClient(config.SOLANA_RPC_URL) as client:
        # 1. 获取签名列表
        print("\n步骤 1: 获取交易签名列表...")
        pubkey = Pubkey.from_string(kamino_address)

        cutoff_time = datetime.now() - timedelta(hours=1)
        cutoff_timestamp = int(cutoff_time.timestamp())

        all_signatures = []
        before_sig = None

        # 获取多页签名
        for page in range(5):  # 最多 5 页
            print(f"  查询第 {page + 1} 页签名...")

            options = {"limit": 100}
            if before_sig:
                options["before"] = before_sig

            try:
                response = await client.get_signatures_for_address(pubkey, **options)

                if not response.value:
                    break

                # 过滤时间范围
                valid_sigs = []
                for sig_info in response.value:
                    if sig_info.block_time and sig_info.block_time >= cutoff_timestamp:
                        valid_sigs.append({
                            "signature": str(sig_info.signature),
                            "blockTime": sig_info.block_time,
                            "err": sig_info.err,
                        })
                    else:
                        # 超出时间范围，停止
                        all_signatures.extend(valid_sigs)
                        print(f"  已到达时间边界")
                        break

                if len(valid_sigs) < len(response.value):
                    # 到达时间边界
                    break

                all_signatures.extend(valid_sigs)

                if len(response.value) < 100:
                    break

                before_sig = Signature.from_string(response.value[-1].signature.__str__())
                await asyncio.sleep(0.1)

            except Exception as e:
                print(f"  获取签名失败: {e}")
                break

        print(f"\n找到 {len(all_signatures)} 笔交易签名")

        # 2. 获取交易详情（包含日志）
        print("\n步骤 2: 获取交易详情和日志...")
        liquidation_candidates = []

        for i, sig_info in enumerate(all_signatures):
            if i % 20 == 0:
                print(f"  处理进度: {i}/{len(all_signatures)}...")

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

                # 提取日志
                meta = response.value.transaction.meta
                if not meta:
                    continue

                logs = meta.log_messages if hasattr(meta, 'log_messages') else []

                # 检查日志中是否包含清算关键词
                liquidation_logs = []
                for log in logs:
                    log_str = str(log)
                    if any(keyword in log_str for keyword in config.LIQUIDATION_KEYWORDS):
                        liquidation_logs.append(log_str)

                if liquidation_logs:
                    print(f"\n  ⚠️⚠️⚠️ 发现疑似清算交易!")
                    print(f"  签名: {signature}")
                    print(f"  时间: {datetime.fromtimestamp(sig_info['blockTime'])}")

                    liquidation_candidates.append({
                        "signature": signature,
                        "blockTime": sig_info["blockTime"],
                        "logs": logs,
                        "liquidation_logs": liquidation_logs,
                        "transaction": response.value,
                    })

                await asyncio.sleep(0.05)  # 避免速率限制

            except Exception as e:
                if i % 50 == 0:  # 只打印部分错误，避免刷屏
                    print(f"  获取交易 {signature[:16]}... 失败: {e}")
                continue

        print(f"\n处理完成! 共处理 {len(all_signatures)} 笔交易")

        # 3. 显示结果
        print("\n" + "=" * 100)
        print(f"清算事件搜索结果: 找到 {len(liquidation_candidates)} 笔疑似清算交易")
        print("=" * 100)

        if liquidation_candidates:
            for i, candidate in enumerate(liquidation_candidates, 1):
                print(f"\n清算 #{i}:")
                print(f"签名: {candidate['signature']}")
                print(f"时间: {datetime.fromtimestamp(candidate['blockTime'])}")
                print(f"\n清算相关日志:")
                for log in candidate['liquidation_logs']:
                    print(f"  - {log}")

                # 显示所有日志
                print(f"\n完整日志 ({len(candidate['logs'])} 条):")
                for j, log in enumerate(candidate['logs'], 1):
                    print(f"  {j}. {log}")
                print("-" * 100)

            # 保存结果
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"liquidation_candidates_{timestamp}.json"

            # 准备保存数据（移除不可序列化的对象）
            save_data = []
            for candidate in liquidation_candidates:
                save_data.append({
                    "signature": candidate["signature"],
                    "blockTime": candidate["blockTime"],
                    "logs": candidate["logs"],
                    "liquidation_logs": candidate["liquidation_logs"],
                })

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            print(f"\n✓ 清算数据已保存到: {filename}")
        else:
            print("\n❌ 过去 1 小时内未发现清算交易")
            print("\n建议:")
            print("  1. 增加时间范围（如 --hours 24）")
            print("  2. 检查其他协议（Solend, MarginFi）")
            print("  3. 等待市场波动时再查询")

        return liquidation_candidates


if __name__ == "__main__":
    asyncio.run(fetch_kamino_with_logs())
