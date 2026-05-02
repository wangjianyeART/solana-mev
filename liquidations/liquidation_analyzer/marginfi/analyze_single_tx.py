#!/usr/bin/env python3
"""
单个交易分析器
获取、解析并分析单个 MarginFi 清算交易

使用方法:
    python analyze_single_tx.py <signature>
"""

from step3_analyze import analyze_single_liquidation, fetch_sol_price, KNOWN_TOKENS, STABLECOINS, SOL_LIKE_TOKENS
from step2_batch_parse import BatchLiquidationParser
import sys
import json
import os
import urllib.request
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# 加载 .env
load_dotenv(Path(__file__).parent / '.env')

# 导入已有的解析器


def fetch_transaction(signature: str, api_key: str) -> dict:
    """
    使用 Helius API 获取交易详情
    """
    url = f"https://api.helius.xyz/v0/transactions/?api-key={api_key}"

    payload = json.dumps({
        "transactions": [signature]
    }).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method='POST'
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode())
        if result and len(result) > 0:
            return result[0]

    return None


def fetch_raw_transaction(signature: str, api_key: str) -> dict:
    """
    使用 Helius RPC 获取原始交易数据
    """
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"

    payload = json.dumps({
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
    }).encode('utf-8')

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method='POST'
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        result = json.loads(response.read().decode())
        return result.get('result', {})


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_single_tx.py <signature>")
        print("\n示例:")
        print("  python analyze_single_tx.py 2mCYQttX6KZtxgR3g5tt1kNgFXL3UQJuQibw5FErJsvNud8ztA1ibZjJ2UVFwf73CT5uUsGaZkCxwJoGQQd1sPsF")
        sys.exit(1)

    signature = sys.argv[1]
    api_key = os.getenv('HELIUS_API_KEY')

    if not api_key:
        print("错误: 未找到 HELIUS_API_KEY 环境变量")
        sys.exit(1)

    print("=" * 70)
    print("MarginFi 单交易分析器")
    print("=" * 70)
    print(f"\n签名: {signature}")

    # 步骤 1: 获取原始交易数据
    print("\n【步骤 1】获取交易数据...")

    try:
        raw_tx = fetch_raw_transaction(signature, api_key)
        if not raw_tx:
            print("错误: 无法获取交易数据")
            sys.exit(1)

        print(f"  ✓ 已获取交易数据")
        print(f"  Slot: {raw_tx.get('slot', 'N/A')}")

        block_time = raw_tx.get('blockTime', 0)
        if block_time:
            dt_str = datetime.fromtimestamp(
                block_time).strftime('%Y-%m-%d %H:%M:%S')
            print(f"  时间: {dt_str}")

        # 检查交易状态
        meta = raw_tx.get('meta', {})
        err = meta.get('err')
        print(f"  状态: {'成功' if err is None else f'失败 - {err}'}")
        print(f"  费用: {meta.get('fee', 0) / 1e9:.6f} SOL")

    except Exception as e:
        print(f"错误: 获取交易失败 - {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 步骤 2: 解析交易
    print("\n【步骤 2】解析交易...")

    try:
        # 构造步骤1格式的数据
        tx_data = {
            'signature': signature,
            'slot': raw_tx.get('slot', 0),
            'blockTime': raw_tx.get('blockTime', 0),
            'transaction': raw_tx
        }

        parser = BatchLiquidationParser()
        parsed = parser.parse_raw_transaction(tx_data)

        print(f"  ✓ 解析完成")
        print(f"  是 MarginFi 交易: {'是' if parsed['is_marginfi_tx'] else '否'}")
        print(f"  是清算交易: {'是' if parsed['is_liquidation'] else '否'}")
        print(f"  MarginFi 指令数: {len(parsed['marginfi_instructions'])}")

        # 显示 MarginFi 指令
        if parsed['marginfi_instructions']:
            print("\n  MarginFi 指令列表:")
            for i, instr in enumerate(parsed['marginfi_instructions']):
                print(f"    {i+1}. {instr['name']} [{instr['type']}]")
                if instr.get('args'):
                    for key, val in instr['args'].items():
                        print(f"       - {key}: {val}")

    except Exception as e:
        print(f"错误: 解析交易失败 - {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 步骤 3: 分析利润
    print("\n【步骤 3】分析利润...")

    try:
        sol_price = fetch_sol_price()
        print(f"  当前 SOL 价格: ${sol_price:.2f}")

        analysis = analyze_single_liquidation(parsed, sol_price)

        print("\n" + "=" * 70)
        print("分析结果")
        print("=" * 70)

        print(f"\n【基础信息】")
        print(f"  签名: {analysis.signature}")
        print(f"  时间: {analysis.datetime}")
        print(f"  Slot: {analysis.slot}")
        print(f"  状态: {'成功' if analysis.success else '失败'}")

        print(f"\n【参与方】")
        print(f"  清算人: {analysis.liquidator}")
        print(f"  被清算人: {analysis.liquidatee}")

        print(f"\n【闪电贷】")
        print(f"  使用闪电贷: {'是' if analysis.uses_flash_loan else '否'}")
        if analysis.uses_flash_loan:
            print(
                f"  闪电贷金额: {analysis.flash_loan_amount} {analysis.flash_loan_token}")
            print(f"  闪电贷费用: ${analysis.flash_loan_fee:.4f}")

        print(f"\n【债务和抵押品】")
        print(f"  债务代币: {analysis.debt_token}")
        print(f"  债务金额: {analysis.debt_amount:.6f}")
        print(f"  抵押品代币: {analysis.collateral_token}")
        print(f"  抵押品获得: {analysis.collateral_received:.6f}")

        print(f"\n【成本】")
        print(
            f"  Gas 费用: {analysis.gas_fee_sol:.6f} SOL (${analysis.gas_fee_usd:.4f})")
        print(
            f"  Priority Fee: {analysis.priority_fee_lamports} lamports ({analysis.priority_fee_sol:.6f} SOL)")
        print(f"  Priority Fee USD: ${analysis.priority_fee_usd:.4f}")
        print(
            f"  SOL 价格来源: {analysis.sol_price_source} (${analysis.sol_price_used:.2f})")
        if analysis.protocol_fee > 0:
            print(
                f"  协议费用: ${analysis.protocol_fee:.4f} {analysis.protocol_fee_token}")

        print(f"\n【清算人代币变化】")
        if analysis.liquidator_gains:
            for token, amount in analysis.liquidator_gains.items():
                direction = "+" if amount > 0 else ""
                print(f"  {direction}{amount:.6f} {token}")
        else:
            print("  无代币变化记录")

        print(f"\n【利润计算】")
        print(f"  毛利润: ${analysis.gross_profit_usd:.4f}")
        print(f"  净利润: ${analysis.net_profit_usd:.4f}")

        if analysis.log_data.get('estimated_profit'):
            print(
                f"  注: 利润为估算值 ({analysis.log_data.get('estimation_method', '未知方法')})")

        # 显示所有代币变化
        print(f"\n【所有代币变化详情】")
        for change in analysis.raw_token_changes:
            owner = change.get('owner', '')[:16]
            symbol = change.get('symbol', '')
            pre = change.get('pre_amount', 0)
            post = change.get('post_amount', 0)
            diff = change.get('change', 0)
            direction = "+" if diff > 0 else ""
            print(
                f"  {owner}... | {symbol}: {pre:.6f} -> {post:.6f} ({direction}{diff:.6f})")

        print("\n" + "=" * 70)
        print("分析完成!")
        print("=" * 70)

        # 保存结果
        output_dir = Path(__file__).parent / 'data'
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"single_tx_analysis_{timestamp}.json"

        # 转换 dataclass 为 dict
        from dataclasses import asdict
        result_dict = asdict(analysis)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result_dict, f, indent=2, ensure_ascii=False)

        print(f"\n结果已保存: {output_file}")

    except Exception as e:
        print(f"错误: 分析失败 - {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
