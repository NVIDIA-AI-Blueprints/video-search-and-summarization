# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validation for the VSS capability receipt produced during harness setup."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .json_codec import strict_json_loads

MAX_RECEIPT_BYTES = 256_000
RECEIPT_SCHEMA_VERSION = 1
ARTIFACT_PROTOCOL_VERSION = "1.0"
ARTIFACT_ENVELOPE = "vss-ui-artifact"
REQUIRED_ARTIFACT_KINDS = frozenset({"vss.search.results", "vss.alert.incidents"})
# Production capability attachment is the complete recursive VSS catalog, not
# only the skills needed by today's first search/alert smoke tests. A repository
# contract test keeps this set synchronized when a new skill is added.
REQUIRED_VSS_SKILLS = frozenset(
    {
        "benchmark-video-summarization",
        "vss-ask-video",
        "vss-build-vision-ai",
        "vss-deploy-dense-captioning",
        "vss-deploy-detection-tracking-2d",
        "vss-deploy-detection-tracking-3d",
        "vss-deploy-profile",
        "vss-deploy-video-embedding",
        "vss-deploy-warehouse-helm",
        "vss-generate-video-calibration",
        "vss-generate-video-report",
        "vss-generate-video-report-rag",
        "vss-manage-alerts",
        "vss-manage-video-io-storage",
        "vss-query-analytics",
        "vss-search-archive",
        "vss-setup-behavior-analytics",
        "vss-setup-video-analytics-api",
        "vss-summarize-video",
    }
)
COMMIT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
SAFE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


class CapabilityError(ValueError):
    """A capability receipt is absent, malformed, or incomplete."""


def canonical_receipt_bytes(payload: dict[str, Any]) -> bytes:
    """Return the stable representation used by the deployment digest."""

    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def encode_receipt(payload: dict[str, Any]) -> tuple[str, str]:
    """Encode one receipt for Compose and return ``(base64, sha256)``."""

    raw = canonical_receipt_bytes(payload)
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def _required_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise CapabilityError(f"capability receipt {key} must be an object")
    return value


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CapabilityError(f"capability receipt {key} must be a non-empty string")
    return value.strip()


def _validate_origin(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.params
    ):
        raise CapabilityError(
            "capability receipt vss_origin must be a bare absolute http(s) origin"
        )
    try:
        port = parsed.port
    except ValueError as error:
        raise CapabilityError(
            "capability receipt vss_origin contains an invalid port"
        ) from error
    if port is not None and not 1 <= port <= 65535:
        raise CapabilityError("capability receipt vss_origin contains an invalid port")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class CapabilityReceipt:
    schema_version: int
    harness: str
    identity_mode: str
    vss_origin: str
    runtime_root: str
    runtime_commit: str
    skills: tuple[str, ...]
    artifact_version: str
    artifact_kinds: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Any) -> CapabilityReceipt:
        if not isinstance(payload, dict):
            raise CapabilityError("capability receipt must be a JSON object")
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version != RECEIPT_SCHEMA_VERSION:
            raise CapabilityError(
                f"capability receipt schema_version must be {RECEIPT_SCHEMA_VERSION}"
            )

        harness = _required_string(payload, "harness")
        if not SAFE_NAME_PATTERN.fullmatch(harness):
            raise CapabilityError("capability receipt harness is invalid")
        identity_mode = _required_string(payload, "identity_mode")
        if identity_mode not in {"dedicated", "preserve"}:
            raise CapabilityError(
                "capability receipt identity_mode must be dedicated or preserve"
            )
        raw_origin = payload.get("vss_origin")
        if not isinstance(raw_origin, str):
            raise CapabilityError("capability receipt vss_origin must be a string")
        if not raw_origin.strip() and identity_mode != "dedicated":
            raise CapabilityError(
                "a preserved-agent capability receipt requires vss_origin"
            )
        vss_origin = _validate_origin(raw_origin.strip()) if raw_origin.strip() else ""

        runtime = _required_mapping(payload, "runtime")
        runtime_root = _required_string(runtime, "repo_root")
        if not runtime_root.startswith("/sandbox/") or ".." in runtime_root.split("/"):
            raise CapabilityError(
                "capability receipt runtime.repo_root must be below /sandbox"
            )
        runtime_commit = _required_string(runtime, "commit").lower()
        if not COMMIT_PATTERN.fullmatch(runtime_commit):
            raise CapabilityError(
                "capability receipt runtime.commit must be a full Git commit ID"
            )

        raw_skills = payload.get("skills")
        if (
            not isinstance(raw_skills, list)
            or not raw_skills
            or any(
                not isinstance(skill, str) or not SAFE_NAME_PATTERN.fullmatch(skill)
                for skill in raw_skills
            )
            or len(set(raw_skills)) != len(raw_skills)
        ):
            raise CapabilityError(
                "capability receipt skills must be a non-empty unique name list"
            )
        missing_skills = sorted(REQUIRED_VSS_SKILLS - set(raw_skills))
        if missing_skills:
            raise CapabilityError(
                "capability receipt is missing required VSS skills: "
                + ", ".join(missing_skills)
            )

        artifacts = _required_mapping(payload, "ui_artifacts")
        artifact_version = _required_string(artifacts, "version")
        if artifact_version != ARTIFACT_PROTOCOL_VERSION:
            raise CapabilityError(
                "capability receipt has an unsupported UI artifact version"
            )
        if _required_string(artifacts, "envelope") != ARTIFACT_ENVELOPE:
            raise CapabilityError(
                "capability receipt has an unsupported UI artifact envelope"
            )
        raw_kinds = artifacts.get("kinds")
        if (
            not isinstance(raw_kinds, list)
            or any(not isinstance(kind, str) for kind in raw_kinds)
            or len(set(raw_kinds)) != len(raw_kinds)
        ):
            raise CapabilityError(
                "capability receipt ui_artifacts.kinds must be a unique string list"
            )
        missing_kinds = sorted(REQUIRED_ARTIFACT_KINDS - set(raw_kinds))
        if missing_kinds:
            raise CapabilityError(
                "capability receipt is missing required UI artifacts: "
                + ", ".join(missing_kinds)
            )

        return cls(
            schema_version=schema_version,
            harness=harness,
            identity_mode=identity_mode,
            vss_origin=vss_origin,
            runtime_root=runtime_root,
            runtime_commit=runtime_commit,
            skills=tuple(raw_skills),
            artifact_version=artifact_version,
            artifact_kinds=tuple(raw_kinds),
        )

    def public_summary(self) -> dict[str, Any]:
        """Return authenticated, non-secret readiness metadata."""

        return {
            "attached": True,
            # This is capability readiness, not live VSS service readiness.
            # The gateway cannot probe routes inside the harness policy boundary.
            "ready": True,
            "schema_version": self.schema_version,
            "harness": self.harness,
            "identity_mode": self.identity_mode,
            "runtime_commit": self.runtime_commit,
            "skill_count": len(self.skills),
            "artifact_version": self.artifact_version,
            "artifact_kinds": list(self.artifact_kinds),
        }


def decode_receipt(
    encoded: str,
    expected_sha256: str,
    expected_runtime_commit: str | None = None,
) -> CapabilityReceipt:
    """Decode and digest-check the receipt injected by the host bootstrap."""

    digest = expected_sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise CapabilityError(
            "AGENT_VSS_CAPABILITIES_SHA256 must be a lowercase SHA-256 digest"
        )
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise CapabilityError(
            "AGENT_VSS_CAPABILITIES_B64 must be strict base64"
        ) from error
    if not raw or len(raw) > MAX_RECEIPT_BYTES:
        raise CapabilityError(
            f"decoded VSS capability receipt must be 1..{MAX_RECEIPT_BYTES} bytes"
        )
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
        raise CapabilityError("VSS capability receipt digest does not match")
    try:
        payload = strict_json_loads(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise CapabilityError("VSS capability receipt must be strict JSON") from error
    receipt = CapabilityReceipt.from_payload(payload)
    if expected_runtime_commit is not None:
        expected_commit = expected_runtime_commit.strip().lower()
        if not COMMIT_PATTERN.fullmatch(expected_commit):
            raise CapabilityError(
                "AGENT_EXPECTED_VSS_RUNTIME_REF must be a full Git commit ID"
            )
        if not hmac.compare_digest(receipt.runtime_commit, expected_commit):
            raise CapabilityError(
                "VSS capability receipt runtime commit does not match the deployment"
            )
    return receipt
