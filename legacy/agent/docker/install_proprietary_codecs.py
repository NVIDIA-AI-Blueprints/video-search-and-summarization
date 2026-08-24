#!/usr/local/bin/python3
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
"""Opt-in installer for patent-encumbered multimedia codecs (OpenCV/FFmpeg).

The VSS Agent image does **not** bundle ``opencv-python-headless`` because that
wheel ships FFmpeg libraries containing patent-encumbered codecs (H.264/H.265),
which NVIDIA must not redistribute. When the operator sets
``INSTALL_PROPRIETARY_CODECS=true``, this script downloads the wheel **on the
operator's own machine** (from PyPI, a third party — never from an NVIDIA source)
and extracts it into a writable directory that is added to ``PYTHONPATH`` so the
agent can decode video.

Design constraints (the runtime is a non-root, shell-less distroless image):
  * Pure standard library only (``urllib`` + ``zipfile``); no pip/uv/curl/apt.
  * Writes only to ``VSS_PROPRIETARY_CODECS_DIR`` (writable, owned by the runtime user).
  * Idempotent: a ``.installed`` marker short-circuits subsequent starts.
  * Air-gapped friendly: ``VSS_PROPRIETARY_CODECS_WHEEL`` may point at a
    pre-downloaded wheel so no network access is required.
  * Resilient: transient network/server errors are retried with exponential
    backoff + jitter for up to 20 min (tune via
    ``VSS_PROPRIETARY_CODECS_MAX_RETRY_SECONDS``); permanent errors fail fast.
  * Never fatal: once the retry budget is exhausted the failure is logged and the
    agent still starts (degraded), so a network outage does not crash the container.
"""

from __future__ import annotations

import json
import os
import platform
import random
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

# Keep in sync with the floor in services/agent/pyproject.toml ("opencv-python-headless").
DEFAULT_OPENCV_VERSION = "4.13.0.92"
PACKAGE = "opencv-python-headless"
DEFAULT_TARGET_DIR = "/vss-agent/.codecs"

# Retry policy for the (network) PyPI metadata query + wheel download. PyPI and
# the operator's network can flake transiently, so we retry with exponential
# backoff + jitter until this total wall-clock budget is exhausted. Override the
# budget with VSS_PROPRIETARY_CODECS_MAX_RETRY_SECONDS (e.g. 0 to disable retry).
_MAX_TOTAL_RETRY_SECONDS = 20 * 60  # 20 minutes
_INITIAL_BACKOFF_SECONDS = 5.0
_MAX_BACKOFF_SECONDS = 120.0  # cap a single sleep so we still get many attempts
# HTTP status codes worth retrying (rate limiting + transient server errors).
_RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


def _log(msg: str) -> None:
    print(f"[proprietary-codecs] {msg}", flush=True)


def _is_transient(exc: BaseException) -> bool:
    """True if ``exc`` looks like a transient network/server error worth retrying.

    A 4xx (other than rate-limit / request-timeout) or a "no wheel found" is a
    permanent misconfiguration — retrying would just waste the budget."""
    if isinstance(exc, urllib.error.HTTPError):  # subclass of URLError; check first
        return exc.code in _RETRYABLE_HTTP_STATUS
    if isinstance(exc, urllib.error.URLError):  # DNS failure, refused, timeout, ...
        return True
    return isinstance(exc, (TimeoutError, ConnectionError, socket.timeout))


def _max_retry_seconds() -> float:
    raw = os.environ.get("VSS_PROPRIETARY_CODECS_MAX_RETRY_SECONDS")
    if raw is None:
        return float(_MAX_TOTAL_RETRY_SECONDS)
    try:
        return max(0.0, float(raw))
    except ValueError:
        _log(f"Ignoring invalid VSS_PROPRIETARY_CODECS_MAX_RETRY_SECONDS={raw!r}")
        return float(_MAX_TOTAL_RETRY_SECONDS)


def _retry(operation, description: str, *, sleep=time.sleep):
    """Run ``operation`` with exponential backoff + jitter on transient errors.

    Retries until it succeeds, hits a non-transient error, or exhausts the total
    retry budget (default 20 min). ``sleep`` is injectable for tests."""
    budget = _max_retry_seconds()
    deadline = time.monotonic() + budget
    attempt = 0
    while True:
        attempt += 1
        try:
            result = operation()
            if attempt > 1:
                _log(f"{description}: succeeded on attempt {attempt}.")
            return result
        except Exception as exc:
            remaining = deadline - time.monotonic()
            if not _is_transient(exc):
                _log(f"{description}: attempt {attempt} hit a non-retryable error ({exc}); giving up.")
                raise
            if remaining <= 0:
                _log(
                    f"{description}: attempt {attempt} failed ({exc}); {budget:.0f}s retry budget exhausted, giving up."
                )
                raise
            # Exponential backoff capped per-sleep, with equal jitter, never past the deadline.
            backoff = min(_INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)), _MAX_BACKOFF_SECONDS)
            delay = min(backoff / 2 + random.uniform(0, backoff / 2), remaining)
            _log(
                f"{description}: attempt {attempt} failed ({exc}); "
                f"retrying in {delay:.1f}s ({remaining:.0f}s of retry budget left)."
            )
            sleep(delay)


def _target_dir() -> str:
    return os.environ.get("VSS_PROPRIETARY_CODECS_DIR", DEFAULT_TARGET_DIR)


def _wheel_arch_tag() -> str:
    """Return the substring identifying wheels for this machine's architecture."""
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "aarch64"
    raise RuntimeError(f"Unsupported architecture for codec install: {platform.machine()}")


def _select_wheel_url(version: str, arch: str) -> str:
    """Resolve the manylinux abi3 wheel URL for this version+arch from PyPI."""
    api = f"https://pypi.org/pypi/{PACKAGE}/{version}/json"
    _log(f"Querying {api}")
    with urllib.request.urlopen(api, timeout=30) as resp:
        data = json.load(resp)
    candidates = [
        url["url"]
        for url in data.get("urls", [])
        if url.get("packagetype") == "bdist_wheel"
        and url["filename"].endswith(".whl")
        and "manylinux" in url["filename"]
        and arch in url["filename"]
    ]
    if not candidates:
        raise RuntimeError(f"No manylinux {arch} wheel found for {PACKAGE}=={version}")
    # Prefer the newer manylinux_2_28 build (matches the distroless glibc) when present.
    candidates.sort(key=lambda u: ("manylinux_2_28" not in u, u))
    return candidates[0]


def _download(url: str, dest: str) -> None:
    _log(f"Downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as resp, open(dest, "wb") as fh:
        fh.write(resp.read())


def install() -> str | None:
    """Install the proprietary codecs if requested. Returns the dir to add to ``PYTHONPATH``.

    Returns ``None`` when nothing was installed (already present is reported as the dir).
    """
    target = _target_dir()
    marker = os.path.join(target, ".installed")
    if os.path.exists(marker):
        _log(f"Already installed at {target}")
        return target

    os.makedirs(target, exist_ok=True)
    version = os.environ.get("VSS_OPENCV_VERSION", DEFAULT_OPENCV_VERSION)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            local_wheel = os.environ.get("VSS_PROPRIETARY_CODECS_WHEEL")
            if local_wheel:
                if not os.path.isfile(local_wheel):
                    raise RuntimeError(f"VSS_PROPRIETARY_CODECS_WHEEL not found: {local_wheel}")
                _log(f"Using pre-downloaded wheel {local_wheel}")
                wheel_path = local_wheel
            else:
                arch = _wheel_arch_tag()
                wheel_path = os.path.join(tmp, "opencv.whl")

                def _fetch_wheel() -> None:
                    # Re-resolve the URL on every attempt: a stale/expired CDN
                    # URL is itself a reason the previous download failed.
                    url = _select_wheel_url(version, arch)
                    _download(url, wheel_path)

                _log(
                    f"Fetching {PACKAGE}=={version} ({arch}) from PyPI; "
                    f"will retry transient failures for up to {_max_retry_seconds():.0f}s."
                )
                _retry(_fetch_wheel, f"Fetch {PACKAGE}=={version}")

            _log(f"Extracting wheel into {target}")
            with zipfile.ZipFile(wheel_path) as zf:
                zf.extractall(target)

        # Make the bundled .so files executable/loadable (zip drops perms).
        for root, _dirs, files in os.walk(target):
            for name in files:
                if name.endswith(".so") or ".so." in name:
                    os.chmod(os.path.join(root, name), 0o555)

        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(f"{PACKAGE}=={version}\n")
        _log(f"Proprietary codecs installed at {target}")
        return target
    except Exception as exc:
        # Never crash container startup over an optional, opt-in feature.
        _log(f"WARNING: codec installation failed ({exc}); continuing without video decode.")
        return None


if __name__ == "__main__":
    install()
    sys.exit(0)
