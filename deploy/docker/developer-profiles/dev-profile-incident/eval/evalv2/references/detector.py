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

"""The offline open-vocabulary detector that proposes boxes for box references (never a subject, never production).

The detector runs in its own throwaway environment (its model weights are a separately approved download, X5), so
eval never imports it: :class:`SubprocessDetector` sends one JSON request on stdin and reads one JSON reply,
``{"detector": {"name", "version", "weights_sha256"}, "frames": {"<path>": [{"box", "score", "label"}]}}``. Each
reference record names the detector that proposed its boxes. :class:`FakeDetector` serves hermetic tests.

The proposed detector is OWLv2 base (``google/owlv2-base-patch16-ensemble``, Apache-2.0) on CPU:
``scripts/v2_detector.py`` is its runner, ``scripts/v2_detector_env.py`` builds the environment and
``scripts/v2_detector_weights.py`` is the approved weights download, which writes the lock the runner checks.
:func:`runner_command` is the command a reference run gives :class:`SubprocessDetector`, and
:func:`frames_needed` counts the frames a box-reference run sends it.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from evalv2.references.boxes import VALIDATOR_OFFSET_S, Proposal, frame_plan

PROTOCOL = "evalv2-detector-v1"
OWLV2_MODEL = "google/owlv2-base-patch16-ensemble"
RUNNER_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "v2_detector.py"


class DetectorError(RuntimeError):
    """The detector failed, or answered outside the protocol."""


class Detector(Protocol):
    identity: Mapping[str, Any]

    def propose(self, frames: Sequence[str], queries: Sequence[str]) -> dict[str, list[Proposal]]: ...


def parse_reply(reply: Mapping[str, Any], frames: Sequence[str]) -> tuple[dict[str, Any], dict[str, list[Proposal]]]:
    """The detector's identity and its proposals per frame; refuses missing frames or identity fields."""
    identity = dict(reply.get("detector") or {})
    if not all(identity.get(key) for key in ("name", "version", "weights_sha256")):
        raise DetectorError("the reply must identify the detector (name, version, weights_sha256)")
    rows = reply.get("frames") or {}
    missing = sorted(set(frames) - set(rows))
    if missing:
        raise DetectorError(f"no proposals for frames {missing[:5]}")
    proposals = {frame: [Proposal(tuple(row["box"]), float(row["score"]), str(row.get("label", "")))
                         for row in rows[frame]] for frame in frames}
    return identity, proposals


@dataclass
class SubprocessDetector:
    """A detector command (for example ``uv run --script scripts/v2_detector.py``) speaking the JSON protocol."""

    command: Sequence[str]
    timeout_s: float = 3600.0
    identity: dict[str, Any] = field(default_factory=dict)
    expected_weights_sha256: str | None = None  # the approved weights' lock; a reply naming other weights is refused

    def propose(self, frames: Sequence[str], queries: Sequence[str]) -> dict[str, list[Proposal]]:
        request = json.dumps({"protocol": PROTOCOL, "frames": list(frames), "queries": list(queries)})
        try:
            done = subprocess.run(list(self.command), input=request, capture_output=True, text=True,
                                  timeout=self.timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DetectorError(f"detector did not run: {exc}") from exc
        if done.returncode != 0:
            raise DetectorError(f"detector exited {done.returncode}: {done.stderr.strip()[-300:]}")
        try:
            reply = json.loads(done.stdout)
        except json.JSONDecodeError as exc:
            raise DetectorError(f"detector reply is not JSON: {exc}") from exc
        identity, proposals = parse_reply(reply, frames)
        if self.expected_weights_sha256 and identity["weights_sha256"] != self.expected_weights_sha256:
            raise DetectorError(f"the detector ran weights {identity['weights_sha256']}, not the approved "
                                f"{self.expected_weights_sha256}")
        self.identity = identity
        return proposals


@dataclass
class FakeDetector:
    """Scripted proposals per frame, for tests."""

    proposals: Mapping[str, Sequence[Proposal]]
    identity: Mapping[str, Any] = field(default_factory=lambda: {"name": "fake", "version": "0",
                                                                  "weights_sha256": "0" * 64})

    def propose(self, frames: Sequence[str], queries: Sequence[str]) -> dict[str, list[Proposal]]:
        return {frame: list(self.proposals.get(frame, ())) for frame in frames}


def runner_command(python: Path | str, weights_dir: Path | str, *, script: Path = RUNNER_SCRIPT,
                   extra: Sequence[str] = ()) -> list[str]:
    """The OWLv2 runner in its throwaway environment (``python`` is that environment's interpreter), reading the
    weights and their lock from ``weights_dir``. The runner never downloads anything."""
    return [str(python), str(script), "--backend", "owlv2", "--weights-dir", str(weights_dir), *extra]


def frames_needed(durations: Mapping[str, float]) -> dict[str, int]:
    """The frames a box-reference run sends the detector: every second of each clip for the generator pass, and
    the validator's frames half a second later (:func:`~evalv2.references.boxes.frame_plan`). One detector pass per
    frame covers every item query of its clip."""
    generator = sum(len(frame_plan(d)) for d in durations.values())
    validator = sum(len(frame_plan(d, offset_s=VALIDATOR_OFFSET_S)) for d in durations.values())
    return {"clips": len(durations), "generator": generator, "validator": validator, "frames": generator + validator}
