#!/usr/bin/env python3
"""Manage reusable Markdown procedures."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from core import MemoryMiss, ProceduralMemoryError, enabled
from memory import ProcedureStore


def read_procedure(value: str) -> str:
    return (sys.stdin.read() if value == "-" else
            Path(value).expanduser().read_text(encoding="utf-8"))


def recall(args: argparse.Namespace) -> int:
    text, _metadata = ProcedureStore().load(args.key)
    print("status: memory")
    print("artifact source: memory\n")
    print(text.rstrip())
    return 0


def remember(args: argparse.Namespace) -> int:
    target = ProcedureStore().remember(
        args.key, read_procedure(args.procedure_file), args.source,
    )
    print(f"remembered: {args.key}")
    print(f"artifact: {target}")
    return 0


def forget(args: argparse.Namespace) -> int:
    target = ProcedureStore().procedure_dir(args.key)
    if not target.exists():
        raise MemoryMiss(f"no reusable procedure for {args.key}")
    shutil.rmtree(target)
    print(f"forgot: {args.key}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="list remembered procedures")
    listing.add_argument("--json", action="store_true")

    recalling = commands.add_parser("recall", help="return a validated procedure")
    recalling.add_argument("--key", required=True)

    remembering = commands.add_parser(
        "remember", help="store a procedure after executing and verifying it",
    )
    remembering.add_argument("--key", required=True)
    remembering.add_argument("--procedure-file", required=True)
    remembering.add_argument("--source", action="append", required=True)

    forgetting = commands.add_parser(
        "forget", help="delete one remembered procedure"
    )
    forgetting.add_argument("--key", required=True)

    args = parser.parse_args()
    try:
        if args.command == "list":
            entries = [{"key": key, **metadata}
                       for key, metadata in ProcedureStore().inventory()]
            print(json.dumps(entries) if args.json else "\n".join(
                f"{item['key']}\t{item.get('description', '')}" for item in entries
            ))
            return 0
        if not enabled():
            raise ProceduralMemoryError(
                f"{args.command} requires PLAN_EXECUTE_CACHE=1"
            )
        if args.command == "recall":
            return recall(args)
        if args.command == "remember":
            return remember(args)
        return forget(args)
    except MemoryMiss:
        print("MISS", file=sys.stderr)
        return 3
    except (FileNotFoundError, json.JSONDecodeError, OSError,
            subprocess.SubprocessError, ProceduralMemoryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
