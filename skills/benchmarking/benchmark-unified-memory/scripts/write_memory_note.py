#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write one persisted parent through the configured VSS Markdown sink."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from vss_cli import config
from vss_cli import memory_notes
from vss_core.memory import UnifiedMemoryRecord

if TYPE_CHECKING:
    from vss_core.memory.notes import MemoryNoteWriteResult


def write_memory_note(input_path: Path) -> MemoryNoteWriteResult:
    record = UnifiedMemoryRecord.model_validate_json(input_path.read_text(encoding="utf-8"))
    if record.job.is_child:
        raise ValueError("Markdown notes support parent records only")
    return memory_notes.write(record, config.load())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    write_memory_note(args.input)


if __name__ == "__main__":
    main()
