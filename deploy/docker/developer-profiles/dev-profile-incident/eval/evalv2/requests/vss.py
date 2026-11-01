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

"""Track B requests: one experimental ``POST /api/v1/incidents/{id}/analyze`` body per pipeline profile, with its
accounting.

A **profile** is one versioned pipeline configuration of the agent: the P4 experiment fields it sends (``skip_rp1``,
``max_crop_calls``, ``max_zoom_calls``, optionally ``experiment_object_tracking`` or a registry prompt), the
analysis flags the agent must run with, the stages it may call and what one run may spend. The adapter
(:mod:`evalv2.adapters.vss`) sends ``body`` and charges against ``accounting``; it decides no content.

- A different cap or switch is a new profile id, or an override that the run records in each prediction's
  provenance; an existing profile is never edited.
- The caps are provisional until the Track B pilot (C1b) recalibrates them. A capped item keeps its gap and is
  unscorable, not a miss.
- The experimental path runs no caption pass (the caption windows need the R2 scratch store, blocker X16), and
  never person tracking or set-of-marks prompting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any

P1_MAX_ATTEMPTS = 2  # the agent's P1_MAX_ATTEMPTS: one retry after a runaway output
AGENT_CAP_LIMIT = 200  # the agent's MAX_CROP_CALLS_LIMIT, the upper bound of max_crop_calls and max_zoom_calls
# Agent run-provenance stages (run_provenance.STAGES) and the ledger each is charged to. ``embedding`` (the revision's
# summary embedding) never runs in an experiment, which persists nothing; it is mapped so a stray call is charged.
STAGE_LEDGERS = {"caption": "caption", "p1": "inference", "rp1": "inference", "grounding": "grounding",
                 "zoom": "zoom", "object_tracking": "object_tracking", "privacy_heads": "grounding",
                 "embedding": "embedding"}
OVERRIDABLE = frozenset({"max_crop_calls", "max_zoom_calls", "prompt_id", "p1_tokens_bound",
                         "detector_tokens_bound", "zoom_tokens_bound"})


@dataclass(frozen=True)
class VssProfile:
    """One Track B pipeline profile: the experimental request and what one run may spend."""

    id: str
    max_crop_calls: int
    max_zoom_calls: int
    object_tracking: bool = False
    # The agent's INCIDENT_OBJECT_TRACKING_MAX_DETECTOR_CALLS default. The adapter checks the cap each reply reports
    # (object_tracking.detector.cap) and stops the run when it is higher, so this worst case cannot be passed silently.
    max_object_tracking_calls: int = 80
    prompt_id: str | None = None
    expected_flags: Mapping[str, Any] = field(default_factory=dict)
    p1_tokens_bound: int = 35000
    detector_tokens_bound: int = 6000
    zoom_tokens_bound: int = 4000

    def __post_init__(self) -> None:
        for name in ("max_crop_calls", "max_zoom_calls"):
            if not 1 <= getattr(self, name) <= AGENT_CAP_LIMIT:
                raise ValueError(f"profile {self.id}: {name} must be 1..{AGENT_CAP_LIMIT} (an uncapped zoom has "
                                 "no worst case, so the cap is required)")

    def with_overrides(self, overrides: Mapping[str, Any]) -> VssProfile:
        unknown = sorted(set(overrides) - OVERRIDABLE)
        if unknown:
            raise ValueError(f"profile {self.id}: {unknown} cannot be overridden; make a new profile instead")
        return replace(self, **dict(overrides))

    def stages(self) -> frozenset[str]:
        """The agent stages one run of this profile may call."""
        return frozenset({"p1", "grounding", "zoom"} | ({"object_tracking"} if self.object_tracking else set()))

    def token_bound(self, ledger: str) -> int:
        """Tokens charged for an attempt whose usage is unknown."""
        return {"inference": self.p1_tokens_bound, "zoom": self.zoom_tokens_bound}.get(ledger,
                                                                                       self.detector_tokens_bound)

    def worst_case(self) -> dict[str, tuple[int, int]]:
        """Per ledger, the most attempts (retries included) and tokens one run can spend."""
        calls = {"inference": P1_MAX_ATTEMPTS, "grounding": self.max_crop_calls, "zoom": self.max_zoom_calls}
        if self.object_tracking:
            calls["object_tracking"] = self.max_object_tracking_calls
        return {ledger: (n, n * self.token_bound(ledger)) for ledger, n in calls.items()}

    def body(self, *, video_url: str, model: str, duration_seconds: float | None) -> dict[str, Any]:
        body: dict[str, Any] = {"video_url": video_url, "model": model, "skip_rp1": True,
                                "max_crop_calls": self.max_crop_calls, "max_zoom_calls": self.max_zoom_calls}
        if self.object_tracking:
            body["experiment_object_tracking"] = True
        if self.prompt_id is not None:
            body["experiment_prompt_id"] = self.prompt_id
        if duration_seconds is not None:
            body["video_duration_seconds"] = duration_seconds
        return body

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["expected_flags"] = dict(self.expected_flags)
        return out


# The run provenance's flags describe what the request ran, not the agent's environment (P3): an experiment never
# runs person tracking or set-of-marks prompting, runs object tracking exactly when it asks for it, and says
# ``experiment: true``. The zoomed second look stays on, as on the shared agent (status.md, live VM config), and
# INCIDENT_EXPERIMENTS_ENABLED is on, as on every experiment agent. Expected flags are a subset: each listed flag must
# hold, others are pinned but not prescribed.
PRODUCTION_FLAGS = {"experiment": True, "zoom_names": True, "tracking": False, "set_of_marks": False,
                    "object_tracking": False, "experiments": True}
OBJECT_TRACKING_FLAGS = {**PRODUCTION_FLAGS, "object_tracking": True}
PROFILES: dict[str, VssProfile] = {
    "production-v4": VssProfile("production-v4", max_crop_calls=40, max_zoom_calls=80,
                                expected_flags=PRODUCTION_FLAGS),
    "production-v4-object-tracking": VssProfile("production-v4-object-tracking", max_crop_calls=40,
                                                max_zoom_calls=80, object_tracking=True,
                                                expected_flags=OBJECT_TRACKING_FLAGS),
}


def profile_for(profile_id: str | None, overrides: Mapping[str, Any] | None = None) -> VssProfile:
    if profile_id not in PROFILES:
        raise ValueError(f"unknown Track B profile {profile_id!r}; known: {sorted(PROFILES)}")
    return PROFILES[profile_id].with_overrides(overrides or {})


def build_analyze_request(profile: VssProfile, *, video_url: str, model: str,
                          duration_seconds: float | None) -> dict[str, Any]:
    """``{"body", "accounting"}``: the request the adapter sends, and how it checks and charges it."""
    return {"body": profile.body(video_url=video_url, model=model, duration_seconds=duration_seconds),
            "accounting": {"profile": profile.to_dict(),
                           "worst_case": {ledger: list(pair) for ledger, pair in profile.worst_case().items()},
                           "stages": sorted(profile.stages()), "stage_ledgers": dict(STAGE_LEDGERS),
                           "token_bounds": {ledger: profile.token_bound(ledger)
                                            for ledger in sorted(set(STAGE_LEDGERS.values()))},
                           "expected_flags": dict(profile.expected_flags)}}
