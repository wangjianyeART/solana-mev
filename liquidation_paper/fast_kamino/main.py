#!/usr/bin/env python3
"""
Fast Kamino - Kamino Liquidation MEV Analysis Tool

Complete three-step pipeline:
1. Fetch Data - Concurrently fetch liquidation transactions
2. Parse Data - Parse Kamino instructions using the IDL
3. Analyze Data - Calculate profits, generate statistical reports

Usage:
    # Full pipeline (default: scan 5000 signatures)
    python main.py

    # Specify scan count
    python main.py --limit 10000

    # Run a specific step only
    python main.py --step 1              # Fetch only
    python main.py --step 2 --input xxx  # Parse only
    python main.py --step 3 --input xxx  # Analyze only

    # Resume from a specific step
    python main.py --from 2 --input xxx  # Start from step 2
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# Import step modules
from step1_fetch_liquidations_fast import fetch_liquidations_fast
from step2_batch_parse import batch_parse
from step3_analyze import analyze_liquidations


# ============================================================================
# Configuration
# ============================================================================

DEFAULT_LIMIT = 5000
DEFAULT_WORKERS = 5
DEFAULT_OUTPUT_DIR = "./data"


# ============================================================================
# Main Pipeline
# ============================================================================

def run_full_pipeline(
    limit: int = DEFAULT_LIMIT,
    workers: int = DEFAULT_WORKERS,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    Run the complete three-step pipeline

    Args:
        limit: Number of signatures to scan
        workers: Number of concurrent workers
        output_dir: Output directory
        verbose: Whether to print detailed information

    Returns:
        Dictionary containing results from all steps
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
        # Step 1: Fetch Data
        # ========================================
        print("\n" + "=" * 70)
        print("                    FAST KAMINO - Liquidation MEV Analysis")
        print("=" * 70)
        print(f"\nStart time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Scan target: {limit} signatures")
        print(f"Output directory: {output_dir}")

        print("\n" + "-" * 70)
        print("[Step 1/3] Fetch Liquidation Transactions")
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

        # Check if any liquidation transactions were found
        if step1_stats.get('liquidation_count', 0) == 0:
            print("\nNo liquidation transactions found, pipeline complete")
            results['success'] = True
            return results

        # ========================================
        # Step 2: Parse Data
        # ========================================
        print("\n" + "-" * 70)
        print("[Step 2/3] Parse Liquidation Transactions")
        print("-" * 70)

        step2_output_file = Path(output_dir) / f"liquidations_parsed_{timestamp}.json"
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

        # Check if any liquidation transactions remain
        if step2_result['liquidation_count'] == 0:
            print("\nNo liquidation transactions after parsing, pipeline complete")
            results['success'] = True
            return results

        # ========================================
        # Step 3: Analyze Data
        # ========================================
        print("\n" + "-" * 70)
        print("[Step 3/3] Analyze Liquidation Data")
        print("-" * 70)

        step3_output_file = Path(output_dir) / f"liquidation_analysis_{timestamp}.json"
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
        # Summary Report
        # ========================================
        print("\n" + "=" * 70)
        print("                         Pipeline Complete")
        print("=" * 70)

        print(f"""
Completion time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Output files:
  Step 1 (raw data):       {step1_output}
  Step 2 (parsed data):    {step2_result['output_file']}
  Step 3 (analysis report): {step3_output_file}

Summary:
  Signatures scanned:        {step1_stats.get('total_signatures', 0)}
  Successful transactions:   {step1_stats.get('success_signatures', 0)}
  Liquidation transactions:  {len(step3_results)}
""")

        # Calculate total profit
        if step3_results:
            total_profit = sum(r.net_profit_usd for r in step3_results)
            avg_profit = total_profit / len(step3_results)
            print(f"Profit statistics:")
            print(f"  Total net profit:   ${total_profit:.4f}")
            print(f"  Average net profit: ${avg_profit:.4f}")

        print("=" * 70)

    except KeyboardInterrupt:
        print("\n\nUser interrupted")
        results['error'] = 'interrupted'
    except Exception as e:
        print(f"\nError: {e}")
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
    """Run a single step"""

    if step == 1:
        output, stats = fetch_liquidations_fast(
            limit=limit,
            workers=workers,
            output_dir=output_dir
        )
        return {'output_file': output, 'stats': stats}

    elif step == 2:
        if not input_file:
            print("Error: Step 2 requires an input file (--input)")
            sys.exit(1)
        result = batch_parse(input_file, output_file)
        return result

    elif step == 3:
        if not input_file:
            print("Error: Step 3 requires an input file (--input)")
            sys.exit(1)
        results = analyze_liquidations(input_file, output_file)
        return {'liquidation_count': len(results)}

    else:
        print(f"Error: Invalid step {step}, valid values are 1, 2, 3")
        sys.exit(1)


def run_from_step(
    from_step: int,
    input_file: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    workers: int = DEFAULT_WORKERS,
    output_dir: str = DEFAULT_OUTPUT_DIR
) -> Dict[str, Any]:
    """Run starting from the specified step"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = {}

    # Step 1
    if from_step <= 1:
        print("\n[Step 1] Fetch Liquidation Transactions")
        output, stats = fetch_liquidations_fast(
            limit=limit,
            workers=workers,
            output_dir=output_dir
        )
        input_file = output
        results['step1'] = {'output_file': output, 'stats': stats}

        if stats.get('liquidation_count', 0) == 0:
            print("No liquidation transactions found, pipeline complete")
            return results

    # Step 2
    if from_step <= 2:
        if not input_file:
            print("Error: Input file is required")
            sys.exit(1)

        print("\n[Step 2] Parse Liquidation Transactions")
        step2_output = Path(output_dir) / f"liquidations_parsed_{timestamp}.json"
        result = batch_parse(input_file, str(step2_output))
        input_file = result['output_file']
        results['step2'] = result

        if result['liquidation_count'] == 0:
            print("No liquidation transactions after parsing, pipeline complete")
            return results

    # Step 3
    if from_step <= 3:
        if not input_file:
            print("Error: Input file is required")
            sys.exit(1)

        print("\n[Step 3] Analyze Liquidation Data")
        step3_output = Path(output_dir) / f"liquidation_analysis_{timestamp}.json"
        analysis_results = analyze_liquidations(input_file, str(step3_output))
        results['step3'] = {
            'output_file': str(step3_output),
            'liquidation_count': len(analysis_results)
        }

    return results


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='Fast Kamino - Kamino Liquidation MEV Analysis Tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                      # Full pipeline, scan 5000 signatures
  python main.py --limit 10000        # Scan 10000 signatures
  python main.py --step 1             # Run step 1 only
  python main.py --step 2 --input x   # Run step 2 only
  python main.py --step 3 --input x   # Run step 3 only
  python main.py --from 2 --input x   # Start from step 2
"""
    )

    parser.add_argument(
        '--limit', type=int, default=DEFAULT_LIMIT,
        help=f'Number of signatures to scan (default: {DEFAULT_LIMIT})'
    )
    parser.add_argument(
        '--workers', type=int, default=DEFAULT_WORKERS,
        help=f'Number of concurrent workers (default: {DEFAULT_WORKERS})'
    )
    parser.add_argument(
        '--output', '-o', default=DEFAULT_OUTPUT_DIR,
        help=f'Output directory (default: {DEFAULT_OUTPUT_DIR})'
    )
    parser.add_argument(
        '--step', type=int, choices=[1, 2, 3],
        help='Run only the specified step'
    )
    parser.add_argument(
        '--from', dest='from_step', type=int, choices=[1, 2, 3],
        help='Start from the specified step'
    )
    parser.add_argument(
        '--input', '-i', type=str,
        help='Input file (required for steps 2 and 3)'
    )
    parser.add_argument(
        '--output-file', type=str,
        help='Specify output file name'
    )
    parser.add_argument(
        '--quiet', '-q', action='store_true',
        help='Reduce output verbosity'
    )

    args = parser.parse_args()

    # Ensure output directory exists
    Path(args.output).mkdir(parents=True, exist_ok=True)

    try:
        if args.step:
            # Run a single step only
            run_single_step(
                step=args.step,
                input_file=args.input,
                output_file=args.output_file,
                limit=args.limit,
                workers=args.workers,
                output_dir=args.output
            )
        elif args.from_step:
            # Start from specified step
            run_from_step(
                from_step=args.from_step,
                input_file=args.input,
                limit=args.limit,
                workers=args.workers,
                output_dir=args.output
            )
        else:
            # Full pipeline
            run_full_pipeline(
                limit=args.limit,
                workers=args.workers,
                output_dir=args.output,
                verbose=not args.quiet
            )

    except KeyboardInterrupt:
        print("\n\nUser interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
