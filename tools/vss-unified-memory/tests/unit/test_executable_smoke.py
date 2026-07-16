# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import subprocess
from pathlib import Path


def test_persist_executable_emits_one_json_error() -> None:
    root = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["VSS_MEMORY_EMBEDDING_ENDPOINT"] = "http://localhost:8000"
    environment["VSS_MEMORY_TOKENIZER_VOCAB_PATH"] = str(root / "tests" / "fixtures" / "test-vocab.txt")
    result = subprocess.run(
        [str(root / "scripts" / "persist_summary.py")],
        input="{}",
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "failed"
    assert result.stdout.count("\n") == 1


def test_persist_executable_rejects_command_line_data() -> None:
    root = Path(__file__).parents[2]
    environment = os.environ.copy()
    environment["VSS_MEMORY_EMBEDDING_ENDPOINT"] = "http://localhost:8000"
    environment["VSS_MEMORY_TOKENIZER_VOCAB_PATH"] = str(root / "tests" / "fixtures" / "test-vocab.txt")
    result = subprocess.run(
        [str(root / "scripts" / "persist_summary.py"), '{"untrusted":"data"}'],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error_code"] == "invalid_invocation"
