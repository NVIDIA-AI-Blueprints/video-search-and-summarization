#!/usr/bin/env python3
"""Run Harbor's unchanged judge against a local trajectory."""

import argparse
import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
JUDGE = REPO_ROOT / ".github/skill-eval/verifiers/generic_judge.py"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--trajectory", type=Path, required=True)
    args, judge_args = parser.parse_known_args()

    trajectory = args.trajectory.expanduser().resolve()
    if not trajectory.is_file():
        parser.error(f"trajectory not found: {trajectory}")

    spec = importlib.util.spec_from_file_location("harbor_generic_judge", JUDGE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load judge: {JUDGE}")
    judge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(judge)

    judge.locate_trajectory = lambda: str(trajectory)
    sys.argv = [str(JUDGE), *judge_args]
    return judge.main()


if __name__ == "__main__":
    sys.exit(main())
