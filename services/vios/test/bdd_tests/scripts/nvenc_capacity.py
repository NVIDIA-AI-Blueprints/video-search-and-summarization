# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT

"""NVENC capacity probe and a cross-process slot semaphore.

The BDD suite triggers server-side NVENC transcodes via PUT uploads and
sub-range downloads. Running with pytest-xdist -n auto easily exceeds the
host GPU's concurrent NVENC session limit (typically 3-8 on consumer
NVIDIA GPUs) which surfaces as ``Device '/dev/v4l2-nvenc' failed during
initialization`` in the server log and a 4xx to the client.

This module provides:
  - probe_nvenc_capacity():     ramp ffmpeg h264_nvenc sessions until one
                                fails, return the max concurrent count
  - nvenc_slot_count():         capacity minus a safety margin, cached on
                                disk so all xdist workers share the value
  - NvencSlotPool:              fcntl.flock-based semaphore so any number
                                of worker processes can wait on slots
"""

from __future__ import annotations

import fcntl
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)


# Hard ceiling for the probe -- never try more than this many concurrent
# encoders, even on data-center GPUs.
_PROBE_MAX = 12

# Per-session probe duration -- short enough to keep the probe cheap, long
# enough that ffmpeg actually opens an NVENC session before exiting.
_PROBE_SECONDS = 2

# Safety margin: how many NVENC sessions to reserve for server-side
# background work (sensor transcodes, replay transcodes, etc.) so the test
# pool never fully saturates the GPU.
_SAFETY_MARGIN = 2

# Empirical hard cap for the test slot pool. The host's measured NVENC
# session limit can be much higher than what the server's V4L2-NVENC
# stack tolerates under back-to-back concurrent inits (the server reissues
# encoder sessions for every transcode while sensor recordings drive
# continuous background transcodes in parallel). Without a cap the slot
# pool happily allows 10+ concurrent transcode-triggering tests and the
# server starts logging ``/dev/v4l2-nvenc failed during initialization``
# again. 4 is the value that has been stable across recent BDD runs;
# operators can override via the ``NVENC_MAX_SLOTS`` environment variable.
_DEFAULT_HARD_CAP = 4
_HARD_CAP_ENV = "NVENC_MAX_SLOTS"


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _nvenc_present() -> bool:
    """Cheap pre-check: does ffmpeg report an h264_nvenc encoder?"""
    if not _ffmpeg_available():
        return False
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return "h264_nvenc" in (out.stdout or "")
    except (subprocess.TimeoutExpired, OSError):
        return False


def _spawn_nvenc_probe() -> subprocess.Popen:
    """Spawn one h264_nvenc transcode of a synthetic clip.

    Outputs to /dev/null so the process is light-weight; the only purpose
    is to hold an NVENC session for the probe window. ffmpeg returns
    non-zero immediately if OpenEncodeSessionEx fails (the saturation
    signature we care about).
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={_PROBE_SECONDS}:r=30",
        "-c:v", "h264_nvenc", "-preset", "p1", "-f", "null", "-",
    ]
    return subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )


def probe_nvenc_capacity() -> int:
    """Find the highest concurrent h264_nvenc session count this host can
    sustain right now. Returns 0 if NVENC is unavailable.

    Ramp from 1 upward. At each step, start enough new ffmpeg processes
    to reach N concurrent. Give them a brief moment to open the encoder
    session, then check liveness. The first N where any probe has exited
    non-zero indicates we hit the ceiling at N-1.
    """
    if not _nvenc_present():
        logger.info("NVENC probe: h264_nvenc not available -- capacity=0")
        return 0

    procs: list[subprocess.Popen] = []
    capacity = 0
    try:
        for n in range(1, _PROBE_MAX + 1):
            # Reap any probes that already finished (their _PROBE_SECONDS
            # window expired) so the "top up to N" count below reflects
            # truly concurrent live sessions, not a mix of running and
            # already-exited processes. Without this, by about n=10 the
            # earliest probes have been running for >= _PROBE_SECONDS and
            # exit 0 on their own; counting them toward len(procs) would
            # under-spawn at the top-up step and let the loop report a
            # capacity higher than the host actually sustained.
            procs = [p for p in procs if p.poll() is None]

            # Top up to N concurrent encoders.
            while len(procs) < n:
                procs.append(_spawn_nvenc_probe())

            # Give ffmpeg long enough to open the encode session and start
            # producing frames. ~150ms is enough on modern drivers.
            time.sleep(0.20)

            # Any process that has already exited non-zero hit the
            # OpenEncodeSessionEx ceiling. ffmpeg exits ~immediately in
            # that case; healthy probes are still running and an rc of
            # 0 means the probe simply finished its _PROBE_SECONDS run
            # (success, not saturation).
            failed = False
            for p in procs:
                rc = p.poll()
                if rc is not None and rc != 0:
                    failed = True
                    break
            if failed:
                # n concurrent does NOT work; the prior step (n-1) does.
                break
            capacity = n
    finally:
        for p in procs:
            try:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        # Drain so we don't leave zombies.
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
                p.wait(timeout=1)

    logger.info("NVENC probe: detected concurrent capacity = %d", capacity)
    return capacity


def _hard_cap() -> int:
    raw = os.environ.get(_HARD_CAP_ENV, "").strip()
    if not raw:
        return _DEFAULT_HARD_CAP
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not an int; using default cap %d",
            _HARD_CAP_ENV, raw, _DEFAULT_HARD_CAP,
        )
        return _DEFAULT_HARD_CAP
    return max(1, parsed)


def nvenc_slot_count(probed_capacity: int) -> int:
    """Translate raw probed capacity into the number of slots the test
    pool may use.

    Three effects compose, in order:
      * if ``probed_capacity <= 0`` the host has no usable NVENC and we
        return 0 -- the pool is disabled and ``NvencSlotPool.acquire()``
        yields ``None`` immediately so tests proceed without gating;
      * subtract ``_SAFETY_MARGIN`` to leave room for server-side
        background transcodes (sensor recording, replay, etc.);
      * clamp to ``_DEFAULT_HARD_CAP`` (or the ``NVENC_MAX_SLOTS`` env
        override) because the server's V4L2-NVENC reinit path saturates
        well below the host's measured libnvenc session ceiling.

    Then floor at 1: on tiny hosts (``probed_capacity <= _SAFETY_MARGIN``)
    the math would yield 0 test slots, but disabling the pool entirely
    in that regime would let parallel tests stampede the encoder. One
    slot still serializes all NVENC-using tests, which is strictly
    safer than no gating, so we keep at least one slot whenever NVENC
    is present at all.
    """
    if probed_capacity <= 0:
        return 0
    return max(1, min(probed_capacity - _SAFETY_MARGIN, _hard_cap()))


# ---------------------------------------------------------------------------
# Cross-process slot semaphore via fcntl.flock
# ---------------------------------------------------------------------------


class NvencSlotPool:
    """File-lock based semaphore for NVENC sessions.

    One lock file per slot lives under ``pool_dir``. ``acquire`` walks
    the slot files trying ``fcntl.flock(LOCK_EX | LOCK_NB)``; the first
    one that succeeds is owned by this process until ``release`` (or the
    process exits, which drops the OS-level lock automatically).
    """

    def __init__(self, pool_dir: Path, slot_count: int) -> None:
        self._dir = Path(pool_dir)
        self._slots = max(0, slot_count)
        self._dir.mkdir(parents=True, exist_ok=True)
        for i in range(self._slots):
            (self._dir / f"slot_{i:02d}.lock").touch()

    @property
    def slot_count(self) -> int:
        """Number of NVENC slots this pool owns (0 when disabled)."""
        return self._slots

    @contextmanager
    def acquire(self, wait_timeout: float = 600.0,
                poll_interval: float = 0.1) -> Iterator[Optional[int]]:
        """Block until a slot is free, then yield the slot index.

        If ``slot_count`` is 0 (NVENC unavailable or disabled), yields
        ``None`` immediately -- callers proceed without gating, which
        is the documented "no throttling configured" path.

        If a slot cannot be acquired within ``wait_timeout`` seconds,
        raises ``TimeoutError``. Yielding ``None`` on timeout would
        disable back-pressure exactly when contention is highest (a
        leaked lock, a hung transcode, or genuine overload), turning a
        single stall into renewed NVENC saturation; failing loudly
        instead lets the caller surface the underlying problem.
        """
        if self._slots == 0:
            yield None
            return

        deadline = time.monotonic() + wait_timeout
        fd: Optional[int] = None
        owned: Optional[int] = None
        try:
            while True:
                for i in range(self._slots):
                    slot_path = self._dir / f"slot_{i:02d}.lock"
                    candidate = os.open(slot_path, os.O_RDWR | os.O_CREAT)
                    try:
                        fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        os.close(candidate)
                        continue
                    fd = candidate
                    owned = i
                    break

                if fd is not None:
                    break

                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"NVENC slot acquire timed out after {wait_timeout:.1f}s "
                        f"(slots={self._slots})"
                    )

                time.sleep(poll_interval)

            yield owned
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)


# ---------------------------------------------------------------------------
# Disk-cached capacity (so xdist workers don't each probe)
# ---------------------------------------------------------------------------


def _cache_file(reports_dir: Path) -> Path:
    return Path(reports_dir) / "nvenc_capacity.txt"


def write_cached_capacity(reports_dir: Path, capacity: int) -> None:
    """Persist the probed NVENC capacity for other xdist workers.

    The value is written to ``<reports_dir>/nvenc_capacity.txt`` so
    workers that come up after the controller can read the result of
    the probe rather than running their own. Parent directories are
    created if they do not yet exist.
    """
    cache = _cache_file(reports_dir)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(str(int(capacity)))


def read_cached_capacity(reports_dir: Path) -> Optional[int]:
    """Return the cached NVENC capacity, or ``None`` when unavailable.

    Returns ``None`` if the cache file is missing or unreadable
    (``OSError``) or if its contents cannot be parsed as an integer
    (``ValueError``); both exceptions are intentionally suppressed so
    callers can fall back to re-probing.
    """
    cache = _cache_file(reports_dir)
    if not cache.exists():
        return None
    try:
        return int(cache.read_text().strip() or "0")
    except (OSError, ValueError):
        return None
