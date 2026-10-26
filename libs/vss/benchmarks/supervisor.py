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

"""POSIX trial supervision, including external tools owned by the worker."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from .diagnostics import retain


class TrialCleanupError(RuntimeError):
    """The worker was reaped, but owned process-group cleanup was unconfirmed."""


def isolated_trial(
    command: list[str], *, cwd: Path, timeout: float, diagnostics: Path | None = None
) -> bytes:
    if os.name != "posix":
        raise NotImplementedError("CPU tool supervision currently requires POSIX")
    # Temporary files avoid pipe deadlocks and permit a bounded protocol read.
    import tempfile

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(
            command, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True
        )
        reason = "ok"
        try:
            started = time.monotonic()
            while True:
                if stdout.tell() > 8_000_000 or stderr.tell() > 1_000_000:
                    raise ValueError("child protocol stream limit")
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    child.wait(timeout=min(0.02, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
            if child.returncode:
                raise subprocess.CalledProcessError(child.returncode, command)
            if stdout.tell() > 8_000_000 or stderr.tell() > 1_000_000:
                raise ValueError("child evidence exceeds protocol limit")
            stdout.seek(0)
            return stdout.read(8_000_001)
        except BaseException as error:
            reason = (
                "timeout" if isinstance(error, subprocess.TimeoutExpired) else "failed"
            )
            raise
        finally:
            # Reap owned descendants even after an apparently successful worker.
            # They inherit this session unless they deliberately detach; this
            # profile runs only trusted, caller-installed FFmpeg tools.
            try:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(child.pid, sig)
                    except ProcessLookupError:
                        break
                    if sig == signal.SIGTERM:
                        time.sleep(0.1)
            except OSError as error:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
                raise TrialCleanupError(
                    "process-group cleanup could not be confirmed"
                ) from error
            finally:
                child.wait()
                if diagnostics is not None:
                    diagnostics.mkdir(parents=True, exist_ok=True)
                    retain(
                        command,
                        stderr,
                        returncode=child.returncode,
                        reason=reason,
                        root=diagnostics,
                    )
