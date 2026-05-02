#!/usr/bin/env python3
"""
步骤 2 (批量): 使用 02_parse_transaction.py 的解析器批量解析交易

使用方法：
    python step2_batch_parse.py <原始数据文件>
    python step2_batch_parse.py ./data/kamino_raw_20260130_xxx.json
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

# 导入 02_parse_transaction.py 的解析器
from kamino_decoder import KAMINO_LENDING_PROGRAM_ID


def convert_raw_tx_format(raw_tx: Dict) -> Dict[str, Any]:
    """
    将 step1 的原始交易格式转换为 02_parse_transaction.py 期望的格式
    """
    signature = raw_tx['signature']
    slot = raw_tx.get('slot', 0)
    block_time = raw_tx.get('blockTime', 0)

    inner_tx = raw_tx['transaction']
    meta = inner_tx.get('meta', {})
    message = inner_tx.get('transaction', {}).get('message', {})

    # 获取账户列表
    account_keys = message.get('accountKeys', [])
    accounts = []
    for acc in account_keys:
        if isinstance(acc, dict):
            accounts.append(acc.get('pubkey', ''))
        else:
            accounts.append(acc)

    # 转换指令格式
    instructions = []
    for instr in message.get('instructions', []):
        program_id = instr.get('programId', '')
        if not program_id and 'programIdIndex' in instr:
            idx = instr['programIdIndex']
            if idx < len(accounts):
                program_id = accounts[idx]

        # 解析账户索引为实际地址
        instr_accounts = []
        for acc_idx in instr.get('accounts', []):
            if isinstance(acc_idx, int) and acc_idx < len(accounts):
                instr_accounts.append(accounts[acc_idx])
            elif isinstance(acc_idx, str):
                instr_accounts.append(acc_idx)

        instructions.append({
            'programId': program_id,
            'data': instr.get('data', ''),
            'accounts': instr_accounts
        })

    return {
        'signature': signature,
        'slot': slot,
        'blockTime': block_time,
        'datetime': datetime.fromtimestamp(block_time).isoformat() if block_time else '',
        'success': meta.get('err') is None,
        'fee': meta.get('fee', 0),
        'instructions': instructions,
        'accounts': accounts,
        'preBalances': meta.get('preBalances', []),
        'postBalances': meta.get('postBalances', []),
        'preTokenBalances': meta.get('preTokenBalances', []),
        'postTokenBalances': meta.get('postTokenBalances', []),
        'logMessages': meta.get('logMessages', []),
    }


def batch_parse(input_file: str, output_dir: str = None):
    """批量解析交易"""

    # 动态导入，确保在正确的目录下
    script_dir = Path(__file__).parent
    sys.path.insert(0, str(script_dir))

    from importlib import import_module
    parse_module = import_module('02_parse_transaction')
    LiquidationParser = parse_module.LiquidationParser

    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"文件不存在: {input_file}")

    if output_dir:
        output_path = Path(output_dir)
    else:
        output_path = input_path.parent
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 70)
    print("步骤 2 (批量): 使用 02_parse_transaction.py 解析交易")
    print("=" * 70)
    print(f"\n输入文件: {input_file}")

    # 读取原始数据
    print(f"\n📂 读取原始数据...")
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    metadata = data.get('metadata', {})
    raw_transactions = data.get('transactions', [])

    print(f"   ✓ 交易数量: {len(raw_transactions)}")
    print(f"   ✓ 原始统计: 总签名 {metadata.get('total_signatures', 'N/A')}, "
          f"成功 {metadata.get('success_signatures', 'N/A')}, "
          f"失败 {metadata.get('failed_signatures', 'N/A')}")

    # 创建解析器
    parser = LiquidationParser()

    # 批量解析
    print(f"\n📋 解析交易...")

    all_parsed = []
    liquidation_parsed = []
    non_liquidation_count = 0
    parse_error_count = 0

    for i, raw_tx in enumerate(raw_transactions, 1):
        if i % 50 == 0 or i == len(raw_transactions):
            print(
                f"   处理进度: {i}/{len(raw_transactions)} ({i*100//len(raw_transactions)}%) | 清算: {len(liquidation_parsed)}")

        try:
            # 转换格式
            tx_data = convert_raw_tx_format(raw_tx)

            # 使用 02_parse_transaction.py 的解析器
            result = parser.parse_transaction(tx_data)

            all_parsed.append(result)

            if result.get('is_liquidation'):
                liquidation_parsed.append(result)
            else:
                non_liquidation_count += 1

        except Exception as e:
            parse_error_count += 1

    print(f"\n   ✓ 解析完成: {len(all_parsed)}")
    print(f"   ✓ 清算交易: {len(liquidation_parsed)}")
    print(f"   ✓ 非清算交易: {non_liquidation_count}")
    if parse_error_count:
        print(f"   ⚠ 解析错误: {parse_error_count}")

    # 保存解析结果
    print(f"\n📁 保存解析结果...")

    # 保存所有解析后的数据
    all_parsed_file = output_path / f"all_parsed_{timestamp}.json"
    with open(all_parsed_file, 'w', encoding='utf-8') as f:
        json.dump(all_parsed, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 所有解析数据: {all_parsed_file}")

    # 只保存清算交易
    liquidations_file = output_path / f"liquidations_only_{timestamp}.json"
    with open(liquidations_file, 'w', encoding='utf-8') as f:
        json.dump(liquidation_parsed, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 清算数据: {liquidations_file}")

    # 统计
    stats = {
        'timestamp': timestamp,
        'input_file': str(input_file),
        'total_transactions': len(raw_transactions),
        'parsed_successfully': len(all_parsed),
        'liquidation_count': len(liquidation_parsed),
        'non_liquidation_count': non_liquidation_count,
        'parse_error_count': parse_error_count,
        'liquidation_ratio_pct': len(liquidation_parsed) / len(raw_transactions) * 100 if raw_transactions else 0,
    }

    stats_file = output_path / f"parse_stats_{timestamp}.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"   ✓ 统计数据: {stats_file}")

    # 报告
    print("\n" + "=" * 70)
    print("📊 解析报告")
    print("=" * 70)
    print(f"""
📌 交易统计:
   总交易数: {len(raw_transactions)}
   清算交易: {len(liquidation_parsed)} ({stats['liquidation_ratio_pct']:.2f}%)
   非清算交易: {non_liquidation_count}
""")

    if liquidation_parsed:
        print("📌 清算交易列表:")
        for liq in liquidation_parsed[:10]:  # 只显示前 10 个
            details = liq.get('liquidation_details', {})
            print(f"   - {liq['signature'][:40]}...")
            print(
                f"     代偿: {details.get('debt_amount', 0):.2f} {details.get('debt_token', 'N/A')}")
            print(
                f"     获得: {details.get('collateral_amount', 0):.6f} {details.get('collateral_token', 'N/A')}")

        if len(liquidation_parsed) > 10:
            print(f"   ... 还有 {len(liquidation_parsed) - 10} 笔")

    print(f"""
📁 输出文件:
   所有解析: {all_parsed_file}
   清算数据: {liquidations_file}
   统计数据: {stats_file}
""")
    print("=" * 70)

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description='批量解析 Kamino 交易')
    parser.add_argument('input_file', help='原始交易数据文件 (step1 的输出)')
    parser.add_argument('--output', '-o', help='输出目录 (默认: 与输入文件相同)')

    args = parser.parse_args()

    try:
        batch_parse(args.input_file, args.output)
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
