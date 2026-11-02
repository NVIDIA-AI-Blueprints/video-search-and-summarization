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

"""The owner's approval of one live run: a marker file that names the run, its config and its hard caps.

``run --live`` and a live ``score`` refuse to start without a marker that fits the run, like ``probe.py``'s. The
marker is JSON (``evalv2-live-approval-v1``):

- ``experiment``, ``config_sha256`` (``sha256_json`` of the validated config), ``purpose`` and ``half`` must be the
  config's: an edited config needs a new marker;
- ``approved_by`` and ``approved_at`` say who approved it and when (relayed approvals say so in ``note``);
- ``max_items``: the most work items (runs) the plan may hold;
- ``max_total_tokens``: the run-wide token caps of every ledger the run charges must sum to at most this;
- ``caps``: per ledger, the most attempts and tokens the run may spend; every non-judge cap in the config must be at
  most its ledger's, and a ledger the marker does not list is not approved. They are added to the run's ledger, so
  they hold even if the config were wrong;
- ``enabled_profiles``: gated Track B profiles the run may send (the caption profile after the base64 probe);
- ``judge`` (optional): ``max_requests`` and ``max_tokens`` for the scoring stage's judge calls. A live ``score``
  needs it, and the config's judge cap must fit it.

``draft`` writes a marker skeleton from a config for the owner to complete; it approves nothing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.budget import LEDGERS, Cap
from evalv2.core.hashing import sha256_json
from evalv2.requests.vss import PROFILES

APPROVAL_VERSION = "evalv2-live-approval-v1"
JUDGE_LEDGER = "judge"
_KEYS = frozenset({"approval", "experiment", "config_sha256", "purpose", "half", "approved_by", "approved_at",
                   "max_items", "max_total_tokens", "caps", "enabled_profiles", "judge", "note"})


class ApprovalRefused(RuntimeError):
    """No marker, an unreadable one, or one that does not cover the run; nothing was sent."""


def config_sha256(config: Any) -> str:
    """The SHA-256 a marker pins: the validated config as JSON (defaults included)."""
    return sha256_json(config.model_dump(mode="json"))


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def read_approval(path: Path) -> dict[str, Any]:
    """A live-run marker, refused unless it is well formed; ``sha256`` and ``path`` are added for the manifest."""
    path = Path(path)
    try:
        data = path.read_bytes()
        marker = json.loads(data)
    except (OSError, ValueError) as exc:
        raise ApprovalRefused(f"approval marker {path} is unreadable: {exc}") from exc
    if not isinstance(marker, dict) or marker.get("approval") != APPROVAL_VERSION:
        raise ApprovalRefused(f"approval marker {path} is not an {APPROVAL_VERSION} marker")
    problems = []
    unknown = sorted(set(marker) - _KEYS)
    if unknown:
        problems.append(f"unknown keys {unknown}")
    for key in ("experiment", "config_sha256", "purpose", "half", "approved_by", "approved_at"):
        if not isinstance(marker.get(key), str) or not marker[key].strip():
            problems.append(f"{key} must be a non-empty string")
    for key in ("max_items", "max_total_tokens"):
        if not _positive_int(marker.get(key)):
            problems.append(f"{key} must be a positive integer")
    caps = marker.get("caps")
    if not isinstance(caps, list) or not caps:
        problems.append("caps must list at least one ledger")
    else:
        seen = set()
        for cap in caps:
            if not isinstance(cap, dict) or cap.get("ledger") not in LEDGERS or cap["ledger"] == JUDGE_LEDGER:
                problems.append(f"cap {cap!r}: ledger must be one of {[x for x in LEDGERS if x != JUDGE_LEDGER]}")
                continue
            if cap["ledger"] in seen:
                problems.append(f"ledger {cap['ledger']!r} is capped twice")
            seen.add(cap["ledger"])
            if not _positive_int(cap.get("max_tokens")):
                problems.append(f"cap on {cap['ledger']}: max_tokens must be a positive integer")
            if cap.get("max_attempts") is not None and not _positive_int(cap["max_attempts"]):
                problems.append(f"cap on {cap['ledger']}: max_attempts must be a positive integer")
            if set(cap) - {"ledger", "max_attempts", "max_tokens"}:
                problems.append(f"cap on {cap['ledger']}: unknown keys {sorted(set(cap) - {'ledger', 'max_attempts', 'max_tokens'})}")
    enabled = marker.get("enabled_profiles", [])
    if not isinstance(enabled, list) or not all(isinstance(p, str) for p in enabled):
        problems.append("enabled_profiles must be a list of profile ids")
    else:
        unknown_profiles = sorted(set(enabled) - {p for p, profile in PROFILES.items() if profile.gate})
        if unknown_profiles:
            problems.append(f"enabled_profiles names profiles that are not gated: {unknown_profiles}")
    judge = marker.get("judge")
    if judge is not None and not (isinstance(judge, dict) and _positive_int(judge.get("max_requests"))
                                  and _positive_int(judge.get("max_tokens"))):
        problems.append("judge must be {max_requests, max_tokens} with positive integers")
    if problems:
        raise ApprovalRefused(f"approval marker {path}: " + "; ".join(problems))
    return {**marker, "enabled_profiles": list(enabled), "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest()}


def marker_caps(marker: Mapping[str, Any]) -> list[Cap]:
    """The marker's caps as run-wide ledger caps, added to the run's own so they bind whatever the config says."""
    caps = [Cap(c["ledger"], c.get("max_attempts"), c["max_tokens"]) for c in marker["caps"]]
    judge = marker.get("judge")
    if judge:
        caps.append(Cap(JUDGE_LEDGER, judge["max_requests"], judge["max_tokens"]))
    return caps


def run_problems(marker: Mapping[str, Any], config: Any, items: Mapping[str, Sequence[Any]],
                 charged_ledgers: set[str]) -> list[str]:
    """Why the marker does not cover this run (an empty list means it does).

    ``charged_ledgers`` are the ledgers the plan's worst case draws on (``cli.worst_cases``).
    """
    problems = []
    if marker["experiment"] != config.experiment:
        problems.append(f"the marker approves {marker['experiment']!r}, not {config.experiment!r}")
    digest = config_sha256(config)
    if marker["config_sha256"] != digest:
        problems.append(f"the marker pins config {marker['config_sha256'][:12]}..., but this config is {digest[:12]}... "
                        "(an edited config needs a new approval)")
    if config.purpose is None:
        problems.append("a live run's config names its purpose (pilot or final)")
    elif marker["purpose"] != config.purpose:
        problems.append(f"the marker approves a {marker['purpose']} run, the config is a {config.purpose} run")
    if marker["half"] != config.split.half:
        problems.append(f"the marker approves the {marker['half']} half, the config runs the {config.split.half} half")
    planned = sum(len(task_items) for task_items in items.values())
    if planned > marker["max_items"]:
        problems.append(f"the plan holds {planned} work items, over the approved {marker['max_items']}")
    approved = {c["ledger"]: c for c in marker["caps"]}
    for cap in config.caps:
        if cap.ledger == JUDGE_LEDGER:
            judge = marker.get("judge")
            if judge and ((cap.max_attempts or 0) > judge["max_requests"] or (cap.max_tokens or 0) > judge["max_tokens"]):
                problems.append(f"the config's judge cap ({cap.max_attempts} requests, {cap.max_tokens} tokens) exceeds "
                                f"the approved {judge['max_requests']} and {judge['max_tokens']}")
            continue
        limit = approved.get(cap.ledger)
        scope = f"(subject={cap.subject}, task={cap.task})"
        if limit is None:
            problems.append(f"the config caps ledger {cap.ledger!r} {scope}, which the marker does not approve")
            continue
        if cap.max_tokens is None or cap.max_tokens > limit["max_tokens"]:
            problems.append(f"{cap.ledger} tokens cap {cap.max_tokens} {scope} exceeds the approved {limit['max_tokens']}")
        if limit.get("max_attempts") is not None and (cap.max_attempts is None
                                                       or cap.max_attempts > limit["max_attempts"]):
            problems.append(f"{cap.ledger} attempts cap {cap.max_attempts} {scope} exceeds the approved "
                            f"{limit['max_attempts']}")
    run_wide = {cap.ledger: cap for cap in config.caps
                if cap.subject == "*" and cap.task == "*" and cap.ledger != JUDGE_LEDGER}
    for ledger in sorted(charged_ledgers - set(run_wide)):
        problems.append(f"the run charges ledger {ledger!r} but has no run-wide cap on it (subject and task '*')")
    for ledger in sorted(charged_ledgers - set(approved)):
        problems.append(f"the run charges ledger {ledger!r}, which the marker does not approve")
    total = sum(cap.max_tokens or 0 for cap in run_wide.values())
    if total > marker["max_total_tokens"]:
        problems.append(f"the run-wide token caps sum to {total}, over the approved {marker['max_total_tokens']}")
    gated = sorted({s.profile for s in config.subjects if s.profile in PROFILES and PROFILES[s.profile].gate})
    for profile in gated:
        if profile not in marker["enabled_profiles"]:
            problems.append(f"profile {profile} is gated on {PROFILES[profile].gate}; the marker does not enable it")
    return problems


def draft(config: Any, items: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    """A marker skeleton for the owner: the config's identity and caps, with the approval fields left blank."""
    run_wide = [cap for cap in config.caps if cap.subject == "*" and cap.task == "*"]
    judge = next((cap for cap in run_wide if cap.ledger == JUDGE_LEDGER), None)
    marker: dict[str, Any] = {
        "approval": APPROVAL_VERSION, "experiment": config.experiment, "config_sha256": config_sha256(config),
        "purpose": config.purpose or "", "half": config.split.half, "approved_by": "", "approved_at": "",
        "max_items": sum(len(task_items) for task_items in items.values()),
        "max_total_tokens": sum(cap.max_tokens or 0 for cap in run_wide if cap.ledger != JUDGE_LEDGER),
        "caps": [{"ledger": cap.ledger, "max_attempts": cap.max_attempts, "max_tokens": cap.max_tokens}
                 for cap in run_wide if cap.ledger != JUDGE_LEDGER],
        "enabled_profiles": [],
        "note": "DRAFT: fill approved_by and approved_at only with the owner's approval; lower any figure to the "
                "approved budget.",
    }
    if judge is not None:
        marker["judge"] = {"max_requests": judge.max_attempts, "max_tokens": judge.max_tokens}
    return marker
