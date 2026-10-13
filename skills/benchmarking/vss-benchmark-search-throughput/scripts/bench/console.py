# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Console output and the on-disk transcript of a sweep.

A sweep runs for hours, usually under ``nohup``. Everything printed is also
written to ``benchmark.log`` in the output directory, so a disconnected
terminal does not cost the record of what happened.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TextIO

RULE = "=" * 78

_lock = threading.Lock()
_log_handle: TextIO | None = None


def attach_log_file(path: Path) -> None:
    """Start mirroring console output to ``path`` (append)."""
    global _log_handle
    with _lock:
        if _log_handle is not None:
            _log_handle.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        _log_handle = open(path, "a", encoding="utf-8")


def close_log_file() -> None:
    global _log_handle
    with _lock:
        if _log_handle is not None:
            _log_handle.close()
            _log_handle = None


def say(message: str = "") -> None:
    """Print to stdout and to the transcript.

    Flushed on every line: a sweep under ``nohup`` is watched with ``tail -f``,
    and a block-buffered stream would show nothing for minutes at a time.
    """
    with _lock:
        print(message, flush=True)
        if _log_handle is not None:
            _log_handle.write(message + "\n")
            _log_handle.flush()
