"""Local-docker compute backend.

Runs one containerized stage (TAO train / inference / embedding) as a
blocking subprocess, streaming combined stdout+stderr to a per-stage log
file. Supports cooperative cancellation (`docker kill` by container name),
a hard wall-clock timeout, and a GPU-slot semaphore so concurrent runs on a
single-GPU host serialize instead of OOMing each other.

This is the `docker` implementation of the backend seam. kfp / slurm / brev
submissions raise `BackendUnavailable` — the service rejects real-mode runs
on those backends at submission time (see routers/fine_tune.py).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import GPU_SLOTS, STAGE_TIMEOUT_S

logger = logging.getLogger("deft.docker")

_gpu_slots = threading.BoundedSemaphore(GPU_SLOTS)

# env vars forwarded from the service into containers when present; values
# are never logged (HF_TOKEN gates gated HuggingFace weights, e.g. SigLIP).
_FORWARDED_ENV = ("HF_TOKEN",)

# On a FIPS-enabled host kernel, /proc/sys/crypto/fips_enabled is 1 and the
# container inherits it; a stock image's OpenSSL 3 then tries to load the FIPS
# provider (absent in the image), so RNG/SSLContext init fails and the process
# dies (`unable to fetch drbg` / `_ssl.c` CRYPTO error) before doing any work
# (e.g. the TAO entrypoint importing requests). Masking the flag to 0 inside the
# container lets OpenSSL use its default provider.
#
# This is a HOST/image mismatch workaround, and it disables the container's FIPS
# view — so it is OPT-IN via DEFT_FIPS_MASK ("auto" = apply only on FIPS hosts,
# "1"/"on" = force, "0"/"off" = never; default "auto") and always logged. No-op
# on non-FIPS hosts.
_FIPS_FLAG = Path("/proc/sys/crypto/fips_enabled")
_FIPS_ZERO_FILE = Path(os.environ.get("DEFT_FIPS_ZERO_FILE", "/tmp/deft_fips_zero"))


def _host_fips_enabled() -> bool:
    try:
        return _FIPS_FLAG.read_text().strip() == "1"
    except OSError:
        return False


def _fips_mask_args() -> list[str]:
    mode = os.environ.get("DEFT_FIPS_MASK", "auto").strip().lower()
    if mode in ("0", "off", "false", "no"):
        return []
    if mode not in ("1", "on", "true", "yes") and not _host_fips_enabled():
        return []  # "auto" and host is not FIPS -> nothing to do
    try:
        if not _FIPS_ZERO_FILE.exists():
            _FIPS_ZERO_FILE.write_text("0\n")
    except OSError as exc:
        logger.warning("FIPS mask requested but could not write %s: %s", _FIPS_ZERO_FILE, exc)
        return []
    logger.info(
        "masking /proc/sys/crypto/fips_enabled=0 in containers (host FIPS workaround; "
        "DEFT_FIPS_MASK=%s)",
        mode,
    )
    return ["-v", f"{_FIPS_ZERO_FILE}:/proc/sys/crypto/fips_enabled:ro"]


class BackendUnavailable(RuntimeError):
    pass


class ContainerFailed(RuntimeError):
    def __init__(self, message: str, exit_code: int, log_tail: str):
        super().__init__(message)
        self.exit_code = exit_code
        self.log_tail = log_tail


@dataclass
class ContainerSpec:
    image: str
    args: list[str]                              # argv after the image name
    mounts: dict[str, str]                       # host path -> container path
    name: str
    log_path: Path
    use_gpu: bool = True
    env: dict[str, str] = field(default_factory=dict)
    workdir: str | None = None
    entrypoint: str | None = None
    timeout_s: int = STAGE_TIMEOUT_S


def _docker_cmd(spec: ContainerSpec) -> list[str]:
    cmd = ["docker", "run", "--rm", "--name", spec.name, "--ipc=host"]
    cmd += _fips_mask_args()
    if spec.use_gpu:
        cmd += ["--gpus", "all"]
    for host, container in spec.mounts.items():
        cmd += ["-v", f"{host}:{container}"]
    for key, value in spec.env.items():
        cmd += ["-e", f"{key}={value}"]
    for key in _FORWARDED_ENV:
        if os.environ.get(key):
            cmd += ["-e", f"{key}={os.environ[key]}"]
    if spec.workdir:
        cmd += ["-w", spec.workdir]
    if spec.entrypoint:
        cmd += ["--entrypoint", spec.entrypoint]
    cmd.append(spec.image)
    cmd += spec.args
    return cmd


def tail(path: Path, n_bytes: int = 4000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-n_bytes:].decode("utf-8", errors="replace")


def run_container(
    spec: ContainerSpec,
    cancel_event: threading.Event,
    on_heartbeat: Callable[[], None] | None = None,
) -> int:
    """Run the container to completion. Returns the exit code (0 = success).

    Raises ContainerFailed on nonzero exit or timeout; returns normally on
    cancellation *only* if cancel_event is set (caller maps that to the
    cancelled state).
    """
    spec.log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = _docker_cmd(spec)
    acquired = False
    if spec.use_gpu:
        _gpu_slots.acquire()
        acquired = True
    try:
        with open(spec.log_path, "ab") as log:
            log.write((" ".join(_redact(cmd)) + "\n").encode())
            log.flush()
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + spec.timeout_s
            # Liveness is derived from the LOG GROWING, not from parsing it. The
            # container's stdout is redirected straight to this file, so nothing
            # in Python sees the bytes -- but the size still moves whenever the
            # stage emits anything (TAO writes a line per epoch). That gives a
            # caller a way to distinguish "long" from "hung" without this service
            # knowing what a stage's output means.
            last_size = -1
            while True:
                rc = proc.poll()
                if rc is not None:
                    break
                if on_heartbeat is not None:
                    try:
                        size = spec.log_path.stat().st_size
                    except OSError:
                        size = last_size
                    if size != last_size:
                        last_size = size
                        on_heartbeat()
                if cancel_event.is_set():
                    subprocess.run(["docker", "kill", spec.name], capture_output=True)
                    proc.wait(timeout=60)
                    return -2
                if time.monotonic() > deadline:
                    subprocess.run(["docker", "kill", spec.name], capture_output=True)
                    proc.wait(timeout=60)
                    raise ContainerFailed(
                        f"container {spec.name} exceeded the {spec.timeout_s}s stage timeout",
                        exit_code=-1,
                        log_tail=tail(spec.log_path),
                    )
                time.sleep(2.0)
        if rc != 0:
            raise ContainerFailed(
                f"container {spec.name} exited with code {rc}",
                exit_code=rc,
                log_tail=tail(spec.log_path),
            )
        return 0
    finally:
        if acquired:
            _gpu_slots.release()


def _redact(cmd: list[str]) -> list[str]:
    # never write forwarded secrets into stage logs
    out = []
    for part in cmd:
        for key in _FORWARDED_ENV:
            if part.startswith(f"{key}="):
                part = f"{key}=***"
        out.append(part)
    return out
