#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Collect per-module pytest-csv results, combine into dashboard format, and upload.

Environment variables (all optional — upload is skipped when endpoint is unset):
    DASHBOARD_API_ENDPOINT  Dashboard base URL, e.g. http://10.0.0.1:8000
                            or https://my-dashboard.example.com
                            (default: unset → skip upload)
    GIT_BRANCH              Branch name            (default: "unknown")
    GIT_COMMIT              Commit hash            (default: "")
    MICROSERVICE_NAME       Microservice name      (default: "vms-shim")
    DASHBOARD_METADATA      JSON string of extra metadata (default: "{}")
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

DASHBOARD_COLUMNS = [
    "Test_Name",
    "Status",
    "Duration_Seconds",
    "Timestamp",
    "Details",
    "Error_Message",
]


@dataclass
class PushResult:
    """Outcome of a dashboard push attempt."""

    success: bool = False
    message: str = ""
    run_id: str | None = None


class TestResultPublisher:
    """Collect pytest-csv results from unit and integration tests,
    merge them into a single dashboard-format CSV, and optionally
    upload to the Test Dashboard API.
    """

    def __init__(self, reports_dir: str | Path) -> None:
        self.reports_dir = Path(reports_dir)
        self.unit_test_dir = self.reports_dir / "unit_tests"

    def collect_csv_files(self) -> list[Path]:
        """Return all pytest-csv result files (unit + integration)."""
        csv_files: list[Path] = []

        if self.unit_test_dir.is_dir():
            for f in sorted(self.unit_test_dir.glob("*.csv")):
                if f.stat().st_size > 0:
                    csv_files.append(f)

        integration_csv = self.reports_dir / "test_results.csv"
        if integration_csv.is_file() and integration_csv.stat().st_size > 0:
            csv_files.append(integration_csv)

        return csv_files

    @staticmethod
    def _test_name_from_id(test_id: str) -> str:
        """Convert a pytest node id to a dotted dashboard test name.

        Example:
            tests/unit_tests/sensor_management/test_sensor_api.py::test_list
            → unit_tests.sensor_management.test_list
        """
        if "::" in test_id:
            file_part, func_part = test_id.rsplit("::", 1)
        else:
            file_part, func_part = test_id, ""

        file_part = file_part.replace("/", ".").replace("\\", ".")
        if file_part.endswith(".py"):
            file_part = file_part[:-3]

        for prefix in ("tests.", "test."):
            if file_part.startswith(prefix):
                file_part = file_part[len(prefix):]
                break

        parts = file_part.split(".")
        module_parts = [p for p in parts if not p.startswith("test_")]

        if func_part:
            return ".".join(module_parts + [func_part]) if module_parts else func_part
        return ".".join(module_parts) if module_parts else file_part

    def parse_pytest_csv(self, csv_path: Path, source_label: str) -> list[dict[str, str]]:
        """Parse a single pytest-csv file into dashboard-format rows.

        Args:
            csv_path: Path to the pytest-csv file.
            source_label: Human-readable label used in the Details column.

        Returns:
            List of dicts keyed by DASHBOARD_COLUMNS.
        """
        rows: list[dict[str, str]] = []
        now_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

        try:
            with open(csv_path, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                if reader.fieldnames is None:
                    logger.warning("Empty or headerless CSV: %s", csv_path)
                    return rows

                for record in reader:
                    test_id = record.get("id", record.get("name", "unknown"))
                    status = record.get("status", "unknown").upper()
                    duration = record.get("duration", "0")
                    message = record.get("message", "")

                    rows.append({
                        "Test_Name": self._test_name_from_id(test_id),
                        "Status": status,
                        "Duration_Seconds": duration,
                        "Timestamp": now_iso,
                        "Details": source_label,
                        "Error_Message": message,
                    })
        except Exception:
            logger.exception("Failed to parse CSV: %s", csv_path)

        return rows

    def combine_results(self) -> list[dict[str, str]]:
        """Read every discovered CSV and return a merged row list."""
        csv_files = self.collect_csv_files()
        if not csv_files:
            logger.warning("No CSV result files found in %s", self.reports_dir)
            return []

        all_rows: list[dict[str, str]] = []
        for csv_file in csv_files:
            if csv_file.parent == self.unit_test_dir:
                label = f"unit_test/{csv_file.stem}"
            else:
                label = "integration_test"
            rows = self.parse_pytest_csv(csv_file, source_label=label)
            logger.info("Parsed %d result(s) from %s", len(rows), csv_file)
            all_rows.extend(rows)

        logger.info("Total combined results: %d", len(all_rows))
        return all_rows

    def write_combined_csv(self, output_path: Path | None = None) -> Path:
        """Merge all results and write the dashboard-format CSV.

        Args:
            output_path: Destination path.  Defaults to
                ``<reports_dir>/combined_test_results.csv``.

        Returns:
            Path to the written CSV file.
        """
        if output_path is None:
            output_path = self.reports_dir / "combined_test_results.csv"

        rows = self.combine_results()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=DASHBOARD_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        logger.info("Wrote combined CSV (%d rows) to %s", len(rows), output_path)
        return output_path

    @staticmethod
    def push_to_dashboard(
        csv_file: Path,
        api_endpoint: str,
        branch: str,
        metadata: dict[str, Any],
        microservice: str = "vms-shim",
        commit_hash: str = "",
    ) -> PushResult:
        """Upload the combined CSV to the Test Dashboard API.

        Args:
            csv_file: Path to the CSV to upload.
            api_endpoint: Dashboard base URL (e.g. http://host:port or https://domain).
            branch: Git branch name.
            metadata: Extra metadata dict.
            microservice: Microservice name.
            commit_hash: Git commit hash.

        Returns:
            PushResult with success flag, message, and optional run_id.
        """
        if not csv_file.is_file():
            return PushResult(message=f"CSV file not found: {csv_file}")

        endpoint = f"{api_endpoint.rstrip('/')}/api/v1/test-runs"

        try:
            with open(csv_file, "rb") as fh:
                files = {"csv_file": (csv_file.name, fh, "text/csv")}
                data = {
                    "microservice": microservice,
                    "branch": branch,
                    "commit_hash": commit_hash,
                    "metadata": json.dumps(metadata),
                }
                resp = requests.post(endpoint, data=data, files=files, timeout=30)

            if resp.status_code >= 400:
                return PushResult(
                    message=f"API returned HTTP {resp.status_code}: {resp.text[:500]}"
                )

            try:
                body = resp.json()
            except ValueError:
                return PushResult(
                    message=(
                        f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:500]}"
                    )
                )

            run_id = body.get("id")
            if run_id:
                return PushResult(
                    success=True,
                    run_id=run_id,
                    message=f"Successfully pushed test results (Run ID: {run_id})",
                )
            return PushResult(
                message=f"Unexpected response (HTTP {resp.status_code}): {resp.text[:500]}"
            )

        except requests.ConnectionError as exc:
            return PushResult(message=f"Connection failed: {exc}")
        except requests.Timeout:
            return PushResult(message="Request timed out after 30 seconds")
        except requests.RequestException as exc:
            return PushResult(message=f"Request failed: {exc}")

    def run(self) -> int:
        """Collect, combine, write CSV, and optionally upload.

        Returns:
            0 on success (or skipped upload), 1 on upload failure.
        """
        combined_csv = self.write_combined_csv()

        api_endpoint = os.environ.get("DASHBOARD_API_ENDPOINT", "").strip()
        if not api_endpoint:
            logger.info("DASHBOARD_API_ENDPOINT not set — skipping upload")
            return 0

        branch = os.environ.get("GIT_BRANCH", "unknown")
        commit_hash = os.environ.get("GIT_COMMIT", "")
        microservice = os.environ.get("MICROSERVICE_NAME", "VIOS")

        metadata_raw = os.environ.get("DASHBOARD_METADATA", "{}")
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            logger.warning("Invalid DASHBOARD_METADATA JSON, using empty dict")
            metadata = {}

        result = self.push_to_dashboard(
            csv_file=combined_csv,
            api_endpoint=api_endpoint,
            branch=branch,
            metadata=metadata,
            microservice=microservice,
            commit_hash=commit_hash,
        )

        if result.success:
            logger.info(result.message)
            return 0

        logger.error(result.message)
        return 1


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if len(sys.argv) > 1:
        reports_dir = sys.argv[1]
    elif Path("/app/reports").is_dir():
        reports_dir = "/app/reports"
    else:
        reports_dir = str(Path(__file__).resolve().parent.parent / "reports")

    publisher = TestResultPublisher(reports_dir)
    sys.exit(publisher.run())


if __name__ == "__main__":
    main()
