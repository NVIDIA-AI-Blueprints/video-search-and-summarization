# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Opt-in, bounded native/worker diagnostics without raw media or commands."""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_ROOT: ContextVar[Path | None] = ContextVar("m8_diagnostics", default=None)
MAX_FILES = 64
MAX_BYTES = 16 * 1024**2


@contextmanager
def tool_diagnostics(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    token = _ROOT.set(root)
    try:
        yield
    finally:
        _ROOT.reset(token)


def retain(
    command, stderr, *, returncode, reason, stdout=None, root=None, returned_bytes=None
):
    """Called before temporary streams close. Failure to retain fails evidence."""
    root = root or _ROOT.get()
    if root is None:
        return
    paths = list(root.glob("*.json"))
    if len(paths) >= MAX_FILES or sum(p.stat().st_size for p in paths) >= MAX_BYTES:
        raise ValueError("diagnostic budget")
    stderr.seek(0)
    raw = stderr.read(1024**2 + 1)
    text = raw[: 1024**2].decode("utf-8", errors="replace")
    # No command arguments/environment are retained. Redact absolute paths,
    # including tool/media paths emitted by trusted native tools.
    text = re.sub(r"(?:/[A-Za-z0-9_. -]+){2,}", "<local-path>", text)
    data = {
        "tool": Path(command[0]).name,
        "returncode": returncode,
        "reason": reason,
        "stderr": text,
        "stderr_truncated": len(raw) > 1024**2,
    }
    if stdout is not None and Path(command[0]).name == "ffprobe":
        stdout.seek(0)
        raw = stdout.read(8 * 1024**2 + 1)
        # Only bounded textual timestamp JSON is admitted, never raw RGB bytes.
        data["ffprobe_json"] = raw[: 8 * 1024**2].decode("utf-8", errors="replace")
        data["stdout_truncated"] = len(raw) > 8 * 1024**2
    if stdout is not None:
        # File size and returned length distinguish tool output from capture;
        # raw RGB and command arguments remain excluded from diagnostics.
        data["stdout_bytes"] = os.fstat(stdout.fileno()).st_size
        data["stdout_returned_bytes"] = returned_bytes
    encoded = json.dumps(data).encode()
    if sum(p.stat().st_size for p in paths) + len(encoded) > MAX_BYTES:
        raise ValueError("diagnostic byte budget")
    with (root / (uuid.uuid4().hex + ".json")).open("xb") as target:
        target.write(encoded)
