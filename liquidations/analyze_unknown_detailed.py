"""
详细分析 UNKNOWN 类型的交易
"""
import json
import glob
from datetime import datetime
from collections import defaultdict


def analyze_unknown_transactions():
    """详细分析 UNKNOWN 交易"""

    # 找到最新的分类文件
    json_files = glob.glob("kamino_transactions_categorized_*.json")
    if not json_files:
        print("错误: 未找到分类数据文件")
        return

    latest_file = max(json_files)
    print(f"分析文件: {latest_file}\n")

    # 读取数据
    with open(latest_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    unknown_txs = data['transactions_by_type'].get('UNKNOWN', [])
    print("=" * 120)
    print(f"UNKNOWN 交易详细分析 (共 {len(unknown_txs)} 笔)")
    print("=" * 120)

    if not unknown_txs:
        print("没有 UNKNOWN 交易")
        return

    # 分析 1: 按程序 ID 分组
    print("\n分析 1: 涉及的程序")
    print("-" * 120)

    program_counts = defaultdict(int)
    for tx in unknown_txs:
        instructions = tx.get('instructions', [])
        for inst in instructions:
            program_id = inst.get('programId', 'Unknown')
            program_counts[program_id] += 1

    print(f"\n涉及的程序 (共 {len(program_counts)} 个):\n")
    sorted_programs = sorted(program_counts.items(), key=lambda x: x[1], reverse=True)
    for program_id, count in sorted_programs:
        print(f"  {program_id:<50} {count:>4} 次")

    # 分析 2: 代币转账模式
    print("\n\n分析 2: 代币转账模式")
    print("-" * 120)

    transfer_patterns = defaultdict(int)
    tokens_involved = defaultdict(int)

    for tx in unknown_txs:
        token_transfers = tx.get('tokenTransfers', [])
        num_transfers = len(token_transfers)
        transfer_patterns[num_transfers] += 1

        for transfer in token_transfers:
            mint = transfer.get('mint', 'Unknown')
            tokens_involved[mint] += 1

    print(f"\n转账次数分布:\n")
    for num_transfers in sorted(transfer_patterns.keys()):
        count = transfer_patterns[num_transfers]
        print(f"  {num_transfers} 次转账: {count} 笔交易")

    print(f"\n涉及的代币 (前 10):\n")
    sorted_tokens = sorted(tokens_involved.items(), key=lambda x: x[1], reverse=True)[:10]

    # 常见代币映射
    token_names = {
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
        "So11111111111111111111111111111111111111112": "SOL",
        "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So": "mSOL",
    }

    for mint, count in sorted_tokens:
        token_name = token_names.get(mint, f"{mint[:8]}...")
        print(f"  {token_name:<15} {count:>4} 次")

    # 分析 3: 查看示例交易
    print("\n\n分析 3: 详细查看前 5 笔 UNKNOWN 交易")
    print("-" * 120)

    for i, tx in enumerate(unknown_txs[:5], 1):
        print(f"\n{'='*120}")
        print(f"UNKNOWN 交易 #{i}")
        print(f"{'='*120}")

        print(f"\n基本信息:")
        print(f"  签名: {tx.get('signature', 'N/A')}")
        print(f"  时间: {datetime.fromtimestamp(tx.get('timestamp', 0)).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  费用: {tx.get('fee', 0) / 1e9:.6f} SOL")
        print(f"  费用支付者: {tx.get('feePayer', 'N/A')}")

        # 指令分析
        instructions = tx.get('instructions', [])
        print(f"\n指令列表 ({len(instructions)} 个):")

        for j, inst in enumerate(instructions, 1):
            program_id = inst.get('programId', 'N/A')
            data = inst.get('data', '')

            # 简化显示程序 ID
            if program_id == "ComputeBudget111111111111111111111111111111":
                program_name = "ComputeBudget"
            elif program_id == "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD":
                program_name = "Kamino Lending"
            elif program_id == "KvauGMspG5k6rtzrqqn7WNn3oZdyKqLKwK2XWQ8FLjd":
                program_name = "Kamino Program"
            elif program_id == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
                program_name = "Token Program"
            else:
                program_name = program_id[:20] + "..."

            print(f"  {j}. {program_name}")
            if data and len(data) <= 30:
                print(f"     Data: {data}")

        # 代币转账
        token_transfers = tx.get('tokenTransfers', [])
        if token_transfers:
            print(f"\n代币转账 ({len(token_transfers)} 笔):")
            for j, transfer in enumerate(token_transfers, 1):
                mint = transfer.get('mint', 'Unknown')
                token_name = token_names.get(mint, f"{mint[:8]}...")
                amount = transfer.get('tokenAmount', 0)
                from_acc = transfer.get('fromUserAccount', 'N/A')
                to_acc = transfer.get('toUserAccount', 'N/A')

                print(f"  {j}. {amount:>15.6f} {token_name}")
                print(f"     从: {from_acc[:20]}...")
                print(f"     到: {to_acc[:20]}...")

        # 账户余额变化
        account_data = tx.get('accountData', [])
        token_balance_changes = []

        for acc in account_data:
            token_changes = acc.get('tokenBalanceChanges', [])
            for change in token_changes:
                token_balance_changes.append({
                    'account': acc.get('account', 'N/A')[:20] + '...',
                    'mint': change.get('mint', 'N/A'),
                    'amount': change.get('rawTokenAmount', {}).get('tokenAmount', '0'),
                    'decimals': change.get('rawTokenAmount', {}).get('decimals', 0),
                })

        if token_balance_changes:
            print(f"\n代币余额变化:")
            for j, change in enumerate(token_balance_changes[:5], 1):  # 只显示前5个
                mint = change['mint']
                token_name = token_names.get(mint, f"{mint[:8]}...")
                amount = int(change['amount']) / (10 ** change['decimals'])

                print(f"  {j}. {change['account']}: {amount:+.6f} {token_name}")

    # 分析 4: 寻找共同模式
    print("\n\n分析 4: 共同模式识别")
    print("-" * 120)

    # 查找相似的指令数据
    instruction_data_patterns = defaultdict(list)

    for tx in unknown_txs:
        instructions = tx.get('instructions', [])
        for inst in instructions:
            if inst.get('programId') == "KvauGMspG5k6rtzrqqn7WNn3oZdyKqLKwK2XWQ8FLjd":
                data = inst.get('data', '')
                if data:
                    instruction_data_patterns[data].append(tx.get('signature', 'N/A')[:16])

    print(f"\nKamino Program 指令数据模式 (前 5 种):\n")
    sorted_patterns = sorted(instruction_data_patterns.items(), key=lambda x: len(x[1]), reverse=True)[:5]

    for pattern, signatures in sorted_patterns:
        print(f"  指令数据: {pattern}")
        print(f"  出现次数: {len(signatures)}")
        print(f"  示例签名: {signatures[0]}...")
        print()

    # 保存详细分析结果
    print("\n" + "=" * 120)
    print("保存详细分析结果")
    print("=" * 120)

    analysis_result = {
        "total_unknown_transactions": len(unknown_txs),
        "programs_involved": dict(program_counts),
        "transfer_patterns": dict(transfer_patterns),
        "tokens_involved": dict(tokens_involved),
        "instruction_patterns": {
            pattern: len(sigs) for pattern, sigs in instruction_data_patterns.items()
        },
        "sample_transactions": unknown_txs[:10]  # 保存前10笔
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"unknown_transactions_analysis_{timestamp}.json"

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(analysis_result, f, indent=2, ensure_ascii=False)

    print(f"\n✓ 详细分析已保存到: {output_file}")


if __name__ == "__main__":
    analyze_unknown_transactions()
