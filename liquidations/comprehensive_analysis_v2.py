#!/usr/bin/env python3
"""
全面分析 Kamino 数据 v2：
1. 统计所有交易类型
2. 通过 RPC 查询日志来识别清算交易
3. 使用 IDL 进行深度分析
"""

import json
import requests
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
import time

def load_kamino_idl():
    """加载 Kamino Lend IDL"""
    idl_path = Path(__file__).parent / "kamino_lending.json"
    with open(idl_path, 'r') as f:
        return json.load(f)

def get_transaction_from_rpc(signature, rpc_url="https://api.mainnet-beta.solana.com"):
    """从 Solana RPC 获取交易详情"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0
            }
        ]
    }

    try:
        response = requests.post(rpc_url, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()

        if 'error' in result:
            return None

        return result.get('result')
    except Exception as e:
        return None

def is_liquidation_from_logs(log_messages):
    """从日志消息中判断是否为清算交易"""
    if not log_messages:
        return False

    for log in log_messages:
        log_lower = log.lower()
        if 'liquidate' in log_lower and 'obligation' in log_lower:
            return True

    return False

def extract_liquidation_details_from_logs(log_messages, meta):
    """从日志中提取清算详细信息"""
    details = {
        'has_liquidation': False,
        'liquidation_amount': None,
        'collateral_withdrawn': None,
        'protocol_fee': None,
        'sol_price': None,
        'usdc_price': None,
        'borrowed_value': None,
        'collateral_value': None,
        'ltv': None,
        'liquidation_bonus': None,
        'close_factor': None,
        'liquidation_instruction': None
    }

    for log in log_messages:
        # 检查清算指令
        if "Instruction: LiquidateObligation" in log:
            details['has_liquidation'] = True
            details['liquidation_instruction'] = log.split("Instruction:")[1].strip()

        # 提取清算金额和抵押品
        if "pnl: Liquidator repaid" in log:
            try:
                parts = log.split("repaid")[1].split("and withdrew")[0].strip()
                details['liquidation_amount'] = int(parts)

                parts = log.split("withdrew")[1].split("collateral with fees")[0].strip()
                details['collateral_withdrawn'] = int(parts)

                parts = log.split("with fees")[1].strip()
                details['protocol_fee'] = int(parts)
            except:
                pass

        # 提取价格
        if "Token: SOL Price:" in log:
            try:
                details['sol_price'] = float(log.split("Price:")[1].strip())
            except:
                pass
        elif "Token: USDC Price:" in log:
            try:
                details['usdc_price'] = float(log.split("Price:")[1].strip())
            except:
                pass

        # 提取债务和抵押品价值
        if "Obligation is eligible for liquidation" in log and "borrowed value" in log:
            try:
                # Parse: "borrowed value (scaled): 53.2031, unhealthy borrow value (scaled): 52.9438, LTV: 75%/75%"
                parts = log.split("borrowed value (scaled):")[1]
                details['borrowed_value'] = float(parts.split(",")[0].strip())

                if "LTV:" in log:
                    ltv_part = log.split("LTV:")[1].split(",")[0].strip()
                    details['ltv'] = ltv_part
            except:
                pass

        # 提取清算奖励
        if "liquidation bonus:" in log and "bps" in log:
            try:
                bonus = log.split("liquidation bonus:")[1].split("bps")[0].strip()
                details['liquidation_bonus'] = int(bonus)
            except:
                pass

        # 提取关闭因子
        if "liquidation_close_factor_pct:" in log:
            try:
                factor = log.split("liquidation_close_factor_pct:")[1].split(",")[0].strip()
                details['close_factor'] = int(factor)
            except:
                pass

    return details

def analyze_liquidation_transaction(tx, tx_from_rpc, idl):
    """深度分析单笔清算交易"""
    meta = tx_from_rpc.get('meta', {}) if tx_from_rpc else {}
    log_messages = meta.get('logMessages', [])

    analysis = {
        'signature': tx.get('signature', ''),
        'slot': tx.get('slot', 0),
        'timestamp': tx.get('timestamp', 0),
        'datetime': datetime.fromtimestamp(tx.get('timestamp', 0)).isoformat() if tx.get('timestamp') else None,
        'type': tx.get('type', ''),
        'description': tx.get('description', ''),
        'source': tx.get('source', ''),
        'fee': tx.get('fee', 0),
        'fee_payer': tx.get('feePayer', ''),

        # 代币转账
        'token_transfers': [],
        'native_transfers': [],

        # 账户余额变化
        'account_changes': [],

        # 清算详情
        'liquidation_details': None,

        # 利润分析
        'profit_analysis': None
    }

    # 提取代币转账
    for transfer in tx.get('tokenTransfers', []):
        analysis['token_transfers'].append({
            'from': transfer.get('fromUserAccount', ''),
            'to': transfer.get('toUserAccount', ''),
            'amount': transfer.get('tokenAmount', 0),
            'mint': transfer.get('mint', ''),
            'token_standard': transfer.get('tokenStandard', '')
        })

    # 提取原生转账
    for transfer in tx.get('nativeTransfers', []):
        analysis['native_transfers'].append({
            'from': transfer.get('fromUserAccount', ''),
            'to': transfer.get('toUserAccount', ''),
            'amount': transfer.get('amount', 0)
        })

    # 提取账户余额变化
    for account in tx.get('accountData', []):
        native_change = account.get('nativeBalanceChange', 0)
        token_changes = account.get('tokenBalanceChanges', [])

        if native_change != 0 or token_changes:
            analysis['account_changes'].append({
                'account': account.get('account', ''),
                'native_balance_change': native_change,
                'token_balance_changes': [
                    {
                        'mint': tc.get('mint', ''),
                        'amount': tc.get('rawTokenAmount', {}).get('tokenAmount', '0'),
                        'decimals': tc.get('rawTokenAmount', {}).get('decimals', 0)
                    }
                    for tc in token_changes
                ]
            })

    # 提取清算详情
    if log_messages:
        liquidation_details = extract_liquidation_details_from_logs(log_messages, meta)
        if liquidation_details['has_liquidation']:
            analysis['liquidation_details'] = liquidation_details

            # 计算利润
            if all([
                liquidation_details['liquidation_amount'],
                liquidation_details['collateral_withdrawn'],
                liquidation_details['protocol_fee'],
                liquidation_details['sol_price'],
                liquidation_details['usdc_price']
            ]):
                usdc_paid = liquidation_details['liquidation_amount'] / 1e6
                sol_received = liquidation_details['collateral_withdrawn'] / 1e9
                protocol_fee_sol = liquidation_details['protocol_fee'] / 1e9
                tx_fee_sol = tx.get('fee', 0) / 1e9

                usdc_value = usdc_paid * liquidation_details['usdc_price']
                sol_value = sol_received * liquidation_details['sol_price']
                protocol_fee_value = protocol_fee_sol * liquidation_details['sol_price']
                tx_fee_value = tx_fee_sol * liquidation_details['sol_price']

                gross_profit = sol_value - usdc_value
                net_profit = gross_profit - protocol_fee_value - tx_fee_value

                analysis['profit_analysis'] = {
                    'usdc_paid': usdc_paid,
                    'usdc_value_usd': usdc_value,
                    'sol_received': sol_received,
                    'sol_value_usd': sol_value,
                    'protocol_fee_sol': protocol_fee_sol,
                    'protocol_fee_usd': protocol_fee_value,
                    'tx_fee_sol': tx_fee_sol,
                    'tx_fee_usd': tx_fee_value,
                    'gross_profit_usd': gross_profit,
                    'net_profit_usd': net_profit
                }

    return analysis

def analyze_transactions_batch(transactions, idl, rpc_url="https://api.mainnet-beta.solana.com"):
    """
    批量分析交易，识别清算交易
    """
    stats = {
        'total_checked': len(transactions),
        'liquidations_found': 0,
        'rpc_errors': 0,
        'liquidation_transactions': []
    }

    for i, tx in enumerate(transactions, 1):
        signature = tx.get('signature', '')

        if i % 100 == 0:
            print(f"  已检查 {i}/{len(transactions)} 笔交易, 发现清算: {stats['liquidations_found']}")

        # 查询 RPC 获取日志
        tx_from_rpc = get_transaction_from_rpc(signature, rpc_url)

        if not tx_from_rpc:
            stats['rpc_errors'] += 1
            time.sleep(0.1)  # 避免 RPC 限流
            continue

        meta = tx_from_rpc.get('meta', {})
        log_messages = meta.get('logMessages', [])

        # 检查是否为清算
        if is_liquidation_from_logs(log_messages):
            stats['liquidations_found'] += 1
            liquidation_analysis = analyze_liquidation_transaction(tx, tx_from_rpc, idl)
            stats['liquidation_transactions'].append(liquidation_analysis)
            print(f"  ✓ 发现清算: {signature[:20]}...")

        time.sleep(0.1)  # 避免 RPC 限流

    return stats

def analyze_all_transactions(folder_path, idl, check_liquidations=True, sample_size=None):
    """
    分析所有交易
    """
    folder = Path(folder_path)
    json_files = sorted(folder.glob("kamino_batch_*.json"))

    # 统计数据
    stats = {
        'total_files': len(json_files),
        'total_transactions': 0,
        'transaction_types': Counter(),
        'sources': Counter(),
        'unique_signatures': set(),
        'liquidation_transactions': []
    }

    # 按类型的交易示例
    type_examples = defaultdict(list)

    # 候选清算交易（基于类型推断）
    candidate_liquidations = []

    print(f"开始分析 {len(json_files)} 个文件...")
    print("=" * 80)

    for i, json_file in enumerate(json_files, 1):
        try:
            print(f"[{i}/{len(json_files)}] 处理 {json_file.name}...", end=" ")

            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'transactions' not in data:
                print("跳过（无交易数据）")
                continue

            file_tx_count = 0

            for tx in data['transactions']:
                # 基本统计
                tx_type = tx.get('type', 'UNKNOWN')
                tx_source = tx.get('source', 'UNKNOWN')
                tx_signature = tx.get('signature', '')

                stats['transaction_types'][tx_type] += 1
                stats['sources'][tx_source] += 1
                stats['unique_signatures'].add(tx_signature)
                stats['total_transactions'] += 1
                file_tx_count += 1

                # 收集示例
                if len(type_examples[tx_type]) < 3:
                    type_examples[tx_type].append({
                        'signature': tx_signature,
                        'timestamp': tx.get('timestamp', 0),
                        'description': tx.get('description', ''),
                        'fee': tx.get('fee', 0),
                        'token_transfers_count': len(tx.get('tokenTransfers', []))
                    })

                # 识别候选清算交易
                if any(keyword in tx_type.upper() for keyword in [
                    'WITHDRAW_OBLIGATION_COLLATERAL',
                    'LIQUIDATE',
                    'REPAY_OBLIGATION',
                    'BORROW_OBLIGATION'
                ]):
                    candidate_liquidations.append(tx)

            print(f"完成 (交易: {file_tx_count})")

        except Exception as e:
            print(f"错误: {e}")
            continue

    print("=" * 80)
    print(f"基础统计完成！")
    print(f"总交易数: {stats['total_transactions']:,}")
    print(f"候选清算交易: {len(candidate_liquidations):,}")

    # 检查候选清算交易
    if check_liquidations and candidate_liquidations:
        print(f"\n开始检查 {len(candidate_liquidations)} 笔候选清算交易...")
        print("=" * 80)

        # 如果数量太多，只检查样本
        to_check = candidate_liquidations
        if sample_size and len(candidate_liquidations) > sample_size:
            import random
            to_check = random.sample(candidate_liquidations, sample_size)
            print(f"随机抽样 {sample_size} 笔进行检查...")

        liquidation_stats = analyze_transactions_batch(to_check, idl)

        print("=" * 80)
        print(f"清算检查完成！")
        print(f"检查了 {liquidation_stats['total_checked']} 笔交易")
        print(f"发现清算: {liquidation_stats['liquidations_found']} 笔")
        print(f"RPC 错误: {liquidation_stats['rpc_errors']} 笔")

        stats['liquidation_transactions'] = liquidation_stats['liquidation_transactions']

    # 转换 unique_signatures
    stats['unique_transactions'] = len(stats['unique_signatures'])
    stats['unique_signatures'] = None

    return stats, type_examples, candidate_liquidations

def save_results(stats, type_examples, output_dir):
    """保存分析结果"""
    output_dir = Path(output_dir)

    # 1. 保存完整统计结果
    statistics = {
        'analysis_time': datetime.now().isoformat(),
        'summary': {
            'total_files': stats['total_files'],
            'total_transactions': stats['total_transactions'],
            'unique_transactions': stats['unique_transactions'],
            'total_liquidations': len(stats['liquidation_transactions']),
            'unique_types': len(stats['transaction_types']),
            'unique_sources': len(stats['sources'])
        },
        'transaction_types': {
            tx_type: {
                'count': count,
                'percentage': (count / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0,
                'examples': type_examples.get(tx_type, [])
            }
            for tx_type, count in stats['transaction_types'].most_common()
        },
        'sources': dict(stats['sources'])
    }

    stats_file = output_dir / "kamino_analysis_statistics.json"
    with open(stats_file, 'w', encoding='utf-8') as f:
        json.dump(statistics, f, indent=2, ensure_ascii=False)
    print(f"\n✓ 统计结果已保存: {stats_file}")

    # 2. 保存清算交易
    if stats['liquidation_transactions']:
        liquidations_file = output_dir / "kamino_liquidation_transactions.json"
        liquidations_data = {
            'analysis_time': datetime.now().isoformat(),
            'total_liquidations': len(stats['liquidation_transactions']),
            'liquidations': stats['liquidation_transactions']
        }

        with open(liquidations_file, 'w', encoding='utf-8') as f:
            json.dump(liquidations_data, f, indent=2, ensure_ascii=False)
        print(f"✓ 清算交易已保存: {liquidations_file}")

        return stats_file, liquidations_file

    print("✗ 未发现清算交易")
    return stats_file, None

def print_summary(stats, type_examples):
    """打印统计摘要"""
    print("\n" + "=" * 80)
    print("统计摘要")
    print("=" * 80)

    print(f"\n总体统计:")
    print(f"  文件数: {stats['total_files']}")
    print(f"  总交易数: {stats['total_transactions']:,}")
    print(f"  唯一交易数: {stats['unique_transactions']:,}")
    print(f"  交易类型数: {len(stats['transaction_types'])}")
    print(f"  清算交易数: {len(stats['liquidation_transactions']):,}")

    print(f"\n交易类型分布 (All):")
    print(f"{'类型':<65} {'数量':>12} {'占比':>8}")
    print("-" * 85)

    for tx_type, count in stats['transaction_types'].most_common():
        percentage = (count / stats['total_transactions'] * 100) if stats['total_transactions'] > 0 else 0
        print(f"{tx_type:<65} {count:>12,} {percentage:>7.2f}%")

    if stats['liquidation_transactions']:
        print(f"\n清算交易详情:")
        for liq in stats['liquidation_transactions']:
            print(f"\n  签名: {liq['signature']}")
            print(f"  类型: {liq['type']}")
            print(f"  时间: {liq['datetime']}")

            if liq.get('profit_analysis'):
                profit = liq['profit_analysis']
                print(f"  支付: {profit['usdc_paid']:.6f} USDC (${profit['usdc_value_usd']:.2f})")
                print(f"  获得: {profit['sol_received']:.9f} SOL (${profit['sol_value_usd']:.2f})")
                print(f"  净利润: ${profit['net_profit_usd']:.4f}")

def main():
    """主函数"""
    print("=" * 80)
    print("Kamino 数据全面分析工具 v2")
    print("=" * 80)

    # 加载 IDL
    print("\n加载 Kamino Lend IDL...")
    idl = load_kamino_idl()
    print(f"✓ IDL 加载成功: {idl.get('name', 'unknown')} v{idl.get('version', 'unknown')}")

    # 分析所有交易
    folder_path = "liquidations/kamino_data_7d_20260129_215323/"
    output_dir = "liquidations/"

    print("\n" + "=" * 80)
    # check_liquidations=True 表示通过 RPC 检查候选清算交易
    # sample_size=100 表示如果候选太多，只检查 100 笔样本
    stats, type_examples, candidates = analyze_all_transactions(
        folder_path, idl,
        check_liquidations=True,
        sample_size=200  # 最多检查 200 笔
    )

    # 打印摘要
    print_summary(stats, type_examples)

    # 保存结果
    print("\n" + "=" * 80)
    print("保存结果...")
    print("=" * 80)

    stats_file, liquidations_file = save_results(stats, type_examples, output_dir)

    print("\n" + "=" * 80)
    print("分析完成！")
    print("=" * 80)

if __name__ == "__main__":
    main()
