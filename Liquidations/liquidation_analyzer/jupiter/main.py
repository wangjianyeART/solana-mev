#!/usr/bin/env python3
"""
Fast Jupiter - Jupiter Lend 清算 MEV 分析工具

完整的三步流程：
1. 获取数据 - 快速并发获取清算交易
2. 解析数据 - 使用 IDL 解析 Jupiter Vaults 指令
3. 分析数据 - 计算利润、生成统计报告

使用方法：
    # 完整流程（默认扫描 5000 条签名）
    python main.py

    # 指定扫描数量
    python main.py --limit 10000

    # 只执行特定步骤
    python main.py --step 1              # 只获取
    python main.py --step 2 --input xxx  # 只解析
    python main.py --step 3 --input xxx  # 只分析

    # 从指定步骤继续
    python main.py --from 2 --input xxx  # 从步骤2开始
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# 导入各步骤模块
from step1_fetch_liquidations_fast import fetch_liquidations_fast
from step2_batch_parse import batch_parse
from step3_analyze import analyze_liquidations


# ============================================================================
# 配置
# ============================================================================

DEFAULT_LIMIT = 5000
DEFAULT_WORKERS = 5
DEFAULT_OUTPUT_DIR = "./data"


# ============================================================================
# 主流程
# ============================================================================

def run_full_pipeline(
    limit: int = DEFAULT_LIMIT,
    workers: int = DEFAULT_WORKERS,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    运行完整的三步流程

    Args:
        limit: 扫描签名数量
        workers: 并发线程数
        output_dir: 输出目录
        verbose: 是否打印详细信息

    Returns:
        包含所有步骤结果的字典
    """
    results = {
        'step1': None,
        'step2': None,
        'step3': None,
        'success': False,
        'error': None
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        # ========================================
        # 步骤 1: 获取数据
        # ========================================
        print("\n" + "=" * 70)
        print("                    FAST JUPITER - 清算 MEV 分析")
        print("=" * 70)
        print(f"\n开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"扫描目标: {limit} 条签名")
        print(f"输出目录: {output_dir}")

        print("\n" + "-" * 70)
        print("【步骤 1/3】获取清算交易")
        print("-" * 70)

        step1_output, step1_stats = fetch_liquidations_fast(
            limit=limit,
            workers=workers,
            output_dir=output_dir
        )

        results['step1'] = {
            'output_file': step1_output,
            'stats': step1_stats
        }

        # 检查是否有清算交易
        if step1_stats.get('liquidation_count', 0) == 0:
            print("\n未发现清算交易，流程结束")
            results['success'] = True
            return results

        # ========================================
        # 步骤 2: 解析数据
        # ========================================
        print("\n" + "-" * 70)
        print("【步骤 2/3】解析清算交易")
        print("-" * 70)

        step2_output_file = Path(output_dir) / f"jupiter_liquidations_parsed_{timestamp}.json"
        step2_result = batch_parse(
            input_file=step1_output,
            output_file=str(step2_output_file),
            verbose=verbose
        )

        results['step2'] = {
            'output_file': step2_result['output_file'],
            'parsed_count': step2_result['parsed_count'],
            'liquidation_count': step2_result['liquidation_count']
        }

        # 检查是否有清算交易
        if step2_result['liquidation_count'] == 0:
            print("\n解析后无清算交易，流程结束")
            results['success'] = True
            return results

        # ========================================
        # 步骤 3: 分析数据
        # ========================================
        print("\n" + "-" * 70)
        print("【步骤 3/3】分析清算数据")
        print("-" * 70)

        step3_output_file = Path(output_dir) / f"jupiter_liquidation_analysis_{timestamp}.json"
        step3_results = analyze_liquidations(
            input_file=step2_result['output_file'],
            output_file=str(step3_output_file)
        )

        results['step3'] = {
            'output_file': str(step3_output_file),
            'liquidation_count': len(step3_results)
        }

        results['success'] = True

        # ========================================
        # 汇总报告
        # ========================================
        print("\n" + "=" * 70)
        print("                         流程完成")
        print("=" * 70)

        print(f"""
完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

输出文件:
  步骤1 (原始数据): {step1_output}
  步骤2 (解析数据): {step2_result['output_file']}
  步骤3 (分析报告): {step3_output_file}

统计摘要:
  扫描签名数: {step1_stats.get('total_signatures', 0)}
  成功交易数: {step1_stats.get('success_signatures', 0)}
  清算交易数: {len(step3_results)}
""")

        # 计算总利润
        if step3_results:
            total_profit = sum(r.net_profit_usd for r in step3_results)
            avg_profit = total_profit / len(step3_results)
            print(f"利润统计:")
            print(f"  总净利润: ${total_profit:.4f}")
            print(f"  平均净利润: ${avg_profit:.4f}")

        print("=" * 70)

    except KeyboardInterrupt:
        print("\n\n用户中断")
        results['error'] = 'interrupted'
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        results['error'] = str(e)

    return results


def run_single_step(
    step: int,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    workers: int = DEFAULT_WORKERS,
    output_dir: str = DEFAULT_OUTPUT_DIR
) -> Dict[str, Any]:
    """运行单个步骤"""

    if step == 1:
        output, stats = fetch_liquidations_fast(
            limit=limit,
            workers=workers,
            output_dir=output_dir
        )
        return {'output_file': output, 'stats': stats}

    elif step == 2:
        if not input_file:
            print("错误: 步骤 2 需要指定输入文件 (--input)")
            sys.exit(1)
        result = batch_parse(input_file, output_file)
        return result

    elif step == 3:
        if not input_file:
            print("错误: 步骤 3 需要指定输入文件 (--input)")
            sys.exit(1)
        results = analyze_liquidations(input_file, output_file)
        return {'liquidation_count': len(results)}

    else:
        print(f"错误: 无效的步骤 {step}，有效值为 1, 2, 3")
        sys.exit(1)


def run_from_step(
    from_step: int,
    input_file: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    workers: int = DEFAULT_WORKERS,
    output_dir: str = DEFAULT_OUTPUT_DIR
) -> Dict[str, Any]:
    """从指定步骤开始运行"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = {}

    # 步骤 1
    if from_step <= 1:
        print("\n【步骤 1】获取清算交易")
        output, stats = fetch_liquidations_fast(
            limit=limit,
            workers=workers,
            output_dir=output_dir
        )
        input_file = output
        results['step1'] = {'output_file': output, 'stats': stats}

        if stats.get('liquidation_count', 0) == 0:
            print("未发现清算交易，流程结束")
            return results

    # 步骤 2
    if from_step <= 2:
        if not input_file:
            print("错误: 需要指定输入文件")
            sys.exit(1)

        print("\n【步骤 2】解析清算交易")
        step2_output = Path(output_dir) / f"jupiter_liquidations_parsed_{timestamp}.json"
        result = batch_parse(input_file, str(step2_output))
        input_file = result['output_file']
        results['step2'] = result

        if result['liquidation_count'] == 0:
            print("解析后无清算交易，流程结束")
            return results

    # 步骤 3
    if from_step <= 3:
        if not input_file:
            print("错误: 需要指定输入文件")
            sys.exit(1)

        print("\n【步骤 3】分析清算数据")
        step3_output = Path(output_dir) / f"jupiter_liquidation_analysis_{timestamp}.json"
        analysis_results = analyze_liquidations(input_file, str(step3_output))
        results['step3'] = {
            'output_file': str(step3_output),
            'liquidation_count': len(analysis_results)
        }

    return results


# ============================================================================
# 命令行入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Fast Jupiter - Jupiter Lend 清算 MEV 分析工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py                      # 完整流程，扫描 5000 条
  python main.py --limit 10000        # 扫描 10000 条
  python main.py --step 1             # 只执行步骤 1
  python main.py --step 2 --input x   # 只执行步骤 2
  python main.py --step 3 --input x   # 只执行步骤 3
  python main.py --from 2 --input x   # 从步骤 2 开始
"""
    )

    parser.add_argument(
        '--limit', type=int, default=DEFAULT_LIMIT,
        help=f'扫描签名数量 (默认: {DEFAULT_LIMIT})'
    )
    parser.add_argument(
        '--workers', type=int, default=DEFAULT_WORKERS,
        help=f'并发线程数 (默认: {DEFAULT_WORKERS})'
    )
    parser.add_argument(
        '--output', '-o', default=DEFAULT_OUTPUT_DIR,
        help=f'输出目录 (默认: {DEFAULT_OUTPUT_DIR})'
    )
    parser.add_argument(
        '--step', type=int, choices=[1, 2, 3],
        help='只执行指定步骤'
    )
    parser.add_argument(
        '--from', dest='from_step', type=int, choices=[1, 2, 3],
        help='从指定步骤开始'
    )
    parser.add_argument(
        '--input', '-i', type=str,
        help='输入文件（步骤 2, 3 需要）'
    )
    parser.add_argument(
        '--output-file', type=str,
        help='指定输出文件名'
    )
    parser.add_argument(
        '--quiet', '-q', action='store_true',
        help='减少输出'
    )

    args = parser.parse_args()

    # 确保输出目录存在
    Path(args.output).mkdir(parents=True, exist_ok=True)

    try:
        if args.step:
            # 只执行单个步骤
            run_single_step(
                step=args.step,
                input_file=args.input,
                output_file=args.output_file,
                limit=args.limit,
                workers=args.workers,
                output_dir=args.output
            )
        elif args.from_step:
            # 从指定步骤开始
            run_from_step(
                from_step=args.from_step,
                input_file=args.input,
                limit=args.limit,
                workers=args.workers,
                output_dir=args.output
            )
        else:
            # 完整流程
            run_full_pipeline(
                limit=args.limit,
                workers=args.workers,
                output_dir=args.output,
                verbose=not args.quiet
            )

    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
