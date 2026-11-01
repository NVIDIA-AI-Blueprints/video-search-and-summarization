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

"""Run provenance: the model, prompt and settings each stage of one analysis used, and every call it sent.

One analysis collects a :class:`RunProvenance` in a context variable (:func:`collecting`), so every model call
inside it is counted without threading a meter through the evidence pipeline: the context reaches the request's
tasks and ``asyncio.to_thread`` workers. Each stage (``caption``, ``p1``, ``rp1``, ``grounding``, ``zoom``,
``object_tracking``, ``privacy_heads``) counts the HTTP attempts it sent, the ones that failed (an error status or
a transport error; for P1, grounding and zoom also a reply without a usable answer), the ones a cap refused before
sending, tokens as reported, attempts that reported none, and time. A detector call is counted under the stage set
by :func:`stage_scope` (``grounding`` unless the object tracker or the privacy head search set theirs); a zoom call
always counts as ``zoom``.

The record (:meth:`RunProvenance.to_dict`) also names the agent's code (:func:`agent_identity`), the analysis
flags in effect (:func:`analysis_flags`) and each stage's prompt SHA-256 from the shared registry. The console
writes it once into ``model_runs.notes`` (``response.provenance``).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field
from functools import cache
import os
from pathlib import Path
import subprocess
import threading
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Mapping

SCHEMA = "incident-run-provenance-v1"
STAGES = ("caption", "p1", "rp1", "grounding", "zoom", "object_tracking", "privacy_heads")
GIT_SHA_ENV = "VSS_AGENT_GIT_SHA"
_REPO_ROOT = Path(__file__).resolve().parents[5]


@dataclass
class StageMeter:
    """Every call one stage sent, thread-safe (the evidence step calls the detector from worker threads)."""

    model: str | None = None
    prompts: dict[str, str] = field(default_factory=dict)
    calls: int = 0
    failed: int = 0
    refused: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls_without_usage: int = 0
    elapsed_ms: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def record(self, *, ok: bool, usage: Mapping[str, Any] | None, elapsed_ms: float) -> None:
        prompt = int((usage or {}).get("prompt_tokens") or 0)
        completion = int((usage or {}).get("completion_tokens") or 0)
        total = int((usage or {}).get("total_tokens") or 0) or prompt + completion
        with self._lock:
            self.calls += 1
            self.failed += 0 if ok else 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.total_tokens += total
            self.calls_without_usage += 0 if total else 1
            self.elapsed_ms += elapsed_ms

    def refuse(self) -> None:
        with self._lock:
            self.refused += 1

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "model": self.model,
                "prompts": dict(self.prompts),
                "calls": self.calls,
                "failed": self.failed,
                "refused": self.refused,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "calls_without_usage": self.calls_without_usage,
                "elapsed_ms": round(self.elapsed_ms, 1),
            }


class RunProvenance:
    """The stages of one analysis; created by :func:`collecting`."""

    def __init__(self) -> None:
        self._stages: dict[str, StageMeter] = {}
        self._lock = threading.Lock()

    def stage(self, name: str) -> StageMeter:
        if name not in STAGES:
            raise ValueError(f"unknown stage {name!r}")
        with self._lock:
            return self._stages.setdefault(name, StageMeter())

    def use(self, stage: str, *, model: str | None = None, prompts: Mapping[str, str] | None = None) -> None:
        """Name the model and the prompts (id to SHA-256) a stage uses, whether or not it ends up calling."""
        meter = self.stage(stage)
        with meter._lock:
            if model is not None:
                meter.model = model
            meter.prompts.update(prompts or {})

    def to_dict(self, *, contract_version: str, flags: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            stages = {name: meter.to_dict() for name, meter in sorted(self._stages.items())}
        return {
            "schema": SCHEMA,
            "agent": agent_identity(),
            "contract_version": contract_version,
            "flags": dict(flags if flags is not None else analysis_flags()),
            "stages": stages,
        }


_CURRENT: ContextVar[RunProvenance | None] = ContextVar("incident_run_provenance", default=None)
_STAGE: ContextVar[str] = ContextVar("incident_run_stage", default="grounding")


@contextmanager
def collecting() -> Iterator[RunProvenance]:
    """Collect the provenance of the analysis running inside this block (and its tasks and threads)."""
    provenance = RunProvenance()
    token = _CURRENT.set(provenance)
    try:
        yield provenance
    finally:
        _CURRENT.reset(token)


def current() -> RunProvenance | None:
    return _CURRENT.get()


@contextmanager
def stage_scope(name: str) -> Iterator[None]:
    """Count the detector calls made inside this block (in this task) under ``name``."""
    if name not in STAGES:
        raise ValueError(f"unknown stage {name!r}")
    token = _STAGE.set(name)
    try:
        yield
    finally:
        _STAGE.reset(token)


def record_call(
    stage: str | None, *, ok: bool, usage: Mapping[str, Any] | None, elapsed_ms: float, model: str | None = None
) -> None:
    """Count one HTTP attempt for ``stage`` (``None``: the current detector stage); a no-op outside an analysis."""
    provenance = _CURRENT.get()
    if provenance is None:
        return
    name = stage or _STAGE.get()
    meter = provenance.stage(name)
    if model is not None and meter.model is None:
        provenance.use(name, model=model)
    meter.record(ok=ok, usage=usage, elapsed_ms=elapsed_ms)


def record_refused(stage: str | None = None) -> None:
    provenance = _CURRENT.get()
    if provenance is not None:
        provenance.stage(stage or _STAGE.get()).refuse()


def usage_of(response: Any) -> Mapping[str, Any] | None:
    """The ``usage`` object of an OpenAI-compatible JSON reply, or ``None`` when there is none."""
    try:
        body = response.json()
    except Exception:  # an unreadable body reports no usage
        return None
    usage = body.get("usage") if isinstance(body, dict) else None
    return usage if isinstance(usage, dict) else None


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), *args], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


@cache
def agent_identity() -> dict[str, Any]:
    """The agent's code: its git commit (``VSS_AGENT_GIT_SHA`` when set, else the checkout's), read once per process.

    The VM runs an editable install updated by ``git pull`` and a restart, so the checkout's commit at startup is the
    code that runs; ``dirty`` says whether tracked files differed from it. The package version is install-time
    metadata and can be older.
    """
    from importlib import metadata

    sha = os.environ.get(GIT_SHA_ENV, "").strip() or _git("rev-parse", "HEAD")
    status = None if os.environ.get(GIT_SHA_ENV) else _git("status", "--porcelain", "--untracked-files=no")
    try:
        version: str | None = metadata.version("vss_agents")
    except metadata.PackageNotFoundError:
        version = None
    return {
        "git_sha": sha or None,
        "source": "env" if os.environ.get(GIT_SHA_ENV) else ("git" if sha else None),
        "dirty": None if status is None else bool(status),
        "package_version": version,
    }


def analysis_flags() -> dict[str, Any]:
    """The analysis flags and settings in effect, read the way each stage reads them."""
    from vss_agents.tools import incident_experiments
    from vss_agents.utils import chunk_captions
    from vss_agents.utils import crop_evidence
    from vss_agents.utils import object_tracking
    from vss_agents.utils import privacy_blur
    from vss_agents.utils import set_of_marks
    from vss_agents.utils import track_evidence

    try:
        chunking: dict[str, Any] = vars(chunk_captions.chunk_settings()).copy()
    except ValueError as exc:  # a bad setting skips the caption pass
        chunking = {"enabled": False, "error": str(exc)}
    return {
        "chunking": chunking,
        "tracking": track_evidence.tracking_enabled(),
        "object_tracking": object_tracking.object_tracking_enabled(),
        "set_of_marks": set_of_marks.som_enabled(),
        "zoom_names": crop_evidence.zoom_names_enabled(),
        "blur_detector_call_cap": privacy_blur.call_cap(),
        "experiments": incident_experiments.experiments_enabled(),
    }
