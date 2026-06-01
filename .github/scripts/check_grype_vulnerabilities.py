#!/usr/bin/env python3
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
"""Fail CI when a Grype report contains high or critical vulnerabilities."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys


BLOCKING_SEVERITIES = {"critical", "high"}


@dataclass(frozen=True)
class Finding:
    """Normalized vulnerability finding from a Grype JSON match."""

    vulnerability_id: str
    severity: str
    package: str
    package_type: str
    installed_version: str
    fixed_versions: tuple[str, ...]


def _as_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)


def load_blocking_findings(report_path: Path) -> list[Finding]:
    """Return high/critical findings from a Grype JSON report."""
    with report_path.open(encoding="utf-8") as report_file:
        report = json.load(report_file)

    findings_by_key: dict[tuple[str, str, str], Finding] = {}
    for match in report.get("matches", []):
        vulnerability = match.get("vulnerability", {})
        artifact = match.get("artifact", {})
        severity = str(vulnerability.get("severity", "unknown"))
        if severity.casefold() not in BLOCKING_SEVERITIES:
            continue

        finding = Finding(
            vulnerability_id=str(vulnerability.get("id", "unknown")),
            severity=severity,
            package=str(artifact.get("name", "unknown")),
            package_type=str(artifact.get("type", "unknown")),
            installed_version=str(artifact.get("version", "unknown")),
            fixed_versions=_as_tuple(vulnerability.get("fix", {}).get("versions")),
        )
        key = (finding.vulnerability_id, finding.package, finding.installed_version)
        findings_by_key[key] = finding

    return sorted(
        findings_by_key.values(),
        key=lambda item: (item.severity.casefold() != "critical", item.package, item.vulnerability_id),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Path to grype JSON report")
    parser.add_argument("--service", required=True, help="Service name for log output")
    args = parser.parse_args()

    findings = load_blocking_findings(args.report)
    if not findings:
        print(f"OK: no high or critical vulnerabilities found for {args.service}.")
        return 0

    print(f"ERROR: found {len(findings)} high/critical vulnerabilities for {args.service}:")
    for finding in findings:
        fixed = ", ".join(finding.fixed_versions) if finding.fixed_versions else "no fixed version reported"
        print(
            f"  {finding.severity}: {finding.vulnerability_id} in "
            f"{finding.package} {finding.installed_version} ({finding.package_type}); fix: {fixed}"
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
