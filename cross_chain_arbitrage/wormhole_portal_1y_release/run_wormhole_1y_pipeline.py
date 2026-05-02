#!/usr/bin/env python3
"""
Run the Wormhole 1y reconstruction pipeline into an isolated output directory.

By default this script creates:
  wormhole_data/use/portal_full/recent_1y/matched_<tag>/

It sets WORMHOLE_1Y_RUN_TAG for every child process, so the original
recent_1y/matched/ directory is not overwritten.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RECENT = ROOT / "wormhole_data" / "use" / "portal_full" / "recent_1y"

STAGES = [
    ("build_matched", "wormhole_data/build_matched_1y.py"),
    ("build_context", "wormhole_data/build_matched_context_1y.py"),
    ("detect_arbitrage", "wormhole_data/detect_arbitrage_1y.py"),
    ("tag_subtypes", "wormhole_data/tag_arbitrage_subtypes_1y.py"),
    ("apply_greedy", "wormhole_data/apply_greedy_dedup_1y.py"),
    ("compute_pnl", "wormhole_data/compute_pnl_1y.py"),
]


def default_tag() -> str:
    return "mintfix_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Wormhole 1y pipeline without overwriting existing matched/ outputs."
    )
    parser.add_argument(
        "--tag",
        default=default_tag(),
        help="Run tag. Outputs go to recent_1y/matched_<tag>/",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Allow writing into an existing matched_<tag> directory.",
    )
    parser.add_argument(
        "--start-at",
        choices=[name for name, _ in STAGES],
        help="Start from this stage.",
    )
    parser.add_argument(
        "--stop-after",
        choices=[name for name, _ in STAGES],
        help="Stop after this stage completes.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands and output directory without running.",
    )
    return parser.parse_args()


def selected_stages(start_at: str | None, stop_after: str | None) -> list[tuple[str, str]]:
    start = 0
    end = len(STAGES)
    names = [name for name, _ in STAGES]
    if start_at:
        start = names.index(start_at)
    if stop_after:
        end = names.index(stop_after) + 1
    if start >= end:
        raise SystemExit("--start-at must not come after --stop-after")
    return STAGES[start:end]


def run_stage(name: str, script: str, env: dict[str, str], log_dir: Path) -> None:
    log_path = log_dir / f"{name}.log"
    cmd = [sys.executable, script]

    print(f"\n{'=' * 72}")
    print(f"[{name}] {' '.join(cmd)}")
    print(f"Log: {log_path}")
    print(f"{'=' * 72}")

    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)
        rc = proc.wait()

    if rc != 0:
        raise SystemExit(f"\nStage failed: {name} (exit={rc}). See {log_path}")


def main() -> None:
    args = parse_args()
    tag = args.tag.strip()
    if not tag:
        raise SystemExit("--tag cannot be empty")
    if "/" in tag or tag in {".", ".."}:
        raise SystemExit("--tag must be a simple directory suffix, not a path")

    out_dir = RECENT / f"matched_{tag}"
    log_dir = out_dir / "pipeline_logs"
    stages = selected_stages(args.start_at, args.stop_after)

    print(f"Project root: {ROOT}")
    print(f"Run tag: {tag}")
    print(f"Output dir: {out_dir}")
    print("Stages:")
    for name, script in stages:
        print(f"  - {name}: {script}")

    if out_dir.exists() and not args.resume:
        raise SystemExit(
            f"\nOutput directory already exists: {out_dir}\n"
            "Use --resume to continue writing into it, or choose a new --tag."
        )

    if args.dry_run:
        print("\nDry run only. No commands executed.")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["WORMHOLE_1Y_RUN_TAG"] = tag

    for name, script in stages:
        run_stage(name, script, env, log_dir)

    print(f"\nPipeline complete. Outputs are in:\n{out_dir}")


if __name__ == "__main__":
    main()
