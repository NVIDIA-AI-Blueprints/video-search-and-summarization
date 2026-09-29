# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reviewed footage profiles; filenames never establish calibration identity."""

from __future__ import annotations

from dataclasses import dataclass


ORIGINAL_SHA256 = "21739924f607755906107affa28842df85a4c468e1a8a3a9f28afcb83f346d61"
BALANCED_SHA256 = "a47527bd58438ef0d7a432cf3f990ef8fa58b428ea8f23103924fd3ee0e71472"
# Separately reviewed CLI-upload derivative; color metadata and container bytes
# differ. This is an explicit calibration binding, not an identity equivalence.
BALANCED_VIOS_SHA256 = "1acdbf15d8e121e9afd5cd8328c95489fe0d6a94537c742d8e7423dffb838086"
# Separately evaluated nightly-20260922 VIOS derivative. See native-ui/artifacts/derivative-review.
BALANCED_VIOS_20260922_SHA256 = "16ab996b583753df7b8f77881dffac73fe8aa31bad61e96454ef26238bafb4a9"


@dataclass(frozen=True)
class SourceProfile:
    id: str
    source_sha256: str
    analysis_kind: str
    calibration_file: str
    vision_file: str


def reviewed_profiles(original_sha256: str = ORIGINAL_SHA256) -> dict[str, SourceProfile]:
    """No user-supplied profile, source name, event labels or timestamps are accepted."""
    profiles = [
        SourceProfile("original-filling-shots", original_sha256, "within-shot-heights", "calibration.json", "vision.py"),
        SourceProfile("balanced-single-station", BALANCED_SHA256, "bottle-cycles", "calibration_5min.json", "cycle_vision.py"),
        SourceProfile("balanced-single-station", BALANCED_VIOS_SHA256, "bottle-cycles", "calibration_5min.json", "cycle_vision.py"),
        SourceProfile("balanced-single-station", BALANCED_VIOS_20260922_SHA256, "bottle-cycles", "calibration_5min.json", "cycle_vision.py"),
    ]
    return {profile.source_sha256: profile for profile in profiles}


def profile_for_identity(candidate_sha256: str, approved_reference: dict | None = None,
                         original_sha256: str = ORIGINAL_SHA256) -> SourceProfile | None:
    profiles = reviewed_profiles(original_sha256)
    if candidate_sha256 in profiles:
        return profiles[candidate_sha256]
    if approved_reference:
        from .media_identity import validate_approved_reference

        reference = approved_reference.get("reference_sha256")
        if reference in profiles and validate_approved_reference(approved_reference, candidate_sha256, reference):
            return profiles[reference]
    return None
