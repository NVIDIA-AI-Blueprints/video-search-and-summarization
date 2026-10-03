# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Post-run artifact acceptance checks, using local fixtures only."""

from __future__ import annotations

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from artifacts import CORPUS_FIELDS, ERROR_FIELDS, SUMMARY_FIELDS
from upload import UploadRecord
from validate_artifacts import CHART_NAMES, main, validate_artifacts


class ArtifactValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict("os.environ", {"VSS_AUTH_TOKEN": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        (self.root / "csv").mkdir()
        for name, fields in (
            ("ingest_corpus.csv", CORPUS_FIELDS),
            ("ingest_requests.csv", UploadRecord.CSV_FIELDS),
            ("ingest_summary.csv", SUMMARY_FIELDS),
            ("ingest_errors.csv", ERROR_FIELDS),
        ):
            with (self.root / "csv" / name).open("w", newline="") as handle:
                csv.writer(handle).writerow(fields)
        scope = "All metrics are client-observed. No internal telemetry was collected."
        self.metadata = {"run_id": "fixture-run", "telemetry_collected": "none", "measurement_scope": scope}
        compatibility = {
            "status": "compatible",
            "skill_version": "v3.3.0",
            "deployed_vss_version": "3.3.0",
            "requires_vss": "==3.3.0",
            "version_url": "http://fixture.invalid/api/v1/version",
            "checked_at_utc": "2026-09-28T00:00:00+00:00",
        }
        self.metadata["version_compatibility"] = compatibility
        self.summary = {
            "run_id": "fixture-run",
            "version_compatibility": compatibility.copy(),
            "readiness_stops_clock_at": "max(ingested_at) across matched documents",
            "harness_comparable": False,
            "measurement_scope": scope,
        }
        (self.root / "raw").mkdir()
        (self.root / "raw/upload_details.jsonl").write_text('{"outcome":"confirmed"}\n')
        (self.root / "visualizations").mkdir()
        for name in CHART_NAMES:
            (self.root / "visualizations" / name).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        (self.root / "visualizations/summary.md").write_text("All metrics are client-observed.\n")
        self.write_metadata()
        self.write_summary()

    def write_metadata(self):
        (self.root / "run-metadata.json").write_text(json.dumps(self.metadata))

    def write_summary(self):
        (self.root / "visualizations/summary.json").write_text(json.dumps(self.summary))

    def test_complete_artifacts_pass(self):
        self.assertEqual(validate_artifacts(self.root), [])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["--results-dir", str(self.root)]), 0)
        self.assertIn("Artifact validation passed", output.getvalue())

    def test_missing_or_inconsistent_compatibility_evidence_fails(self):
        self.summary["version_compatibility"]["deployed_vss_version"] = "3.2.1"
        self.write_summary()
        errors = validate_artifacts(self.root)
        self.assertTrue(any("version_compatibility does not match" in error for error in errors))
        self.metadata.pop("version_compatibility")
        self.write_metadata()
        errors = validate_artifacts(self.root)
        self.assertTrue(any("confirmed version_compatibility check" in error for error in errors))

    def test_missing_csv_and_malformed_headers_fail(self):
        (self.root / "csv/ingest_corpus.csv").unlink()
        (self.root / "csv/ingest_requests.csv").write_text("worker_index,wrong_field\n")
        errors = validate_artifacts(self.root)
        self.assertTrue(any("ingest_corpus.csv" in error for error in errors))
        self.assertTrue(any("ingest_requests.csv" in error for error in errors))

    def test_summary_requires_fields_types_and_matching_run(self):
        self.summary.pop("readiness_stops_clock_at")
        self.summary["harness_comparable"] = "false"
        self.summary["run_id"] = "other-run"
        self.write_summary()
        errors = validate_artifacts(self.root)
        self.assertTrue(any("readiness_stops_clock_at" in error for error in errors))
        self.assertTrue(any("boolean harness_comparable" in error for error in errors))
        self.assertTrue(any("run_id does not match" in error for error in errors))

    def test_invalid_json_reports_an_error_without_raising(self):
        (self.root / "visualizations/summary.json").write_text("not json")
        (self.root / "run-metadata.json").write_text("[]")
        errors = validate_artifacts(self.root)
        self.assertTrue(any("summary.json must exist and contain valid JSON" in error for error in errors))
        self.assertTrue(any("run-metadata.json must contain a JSON object" in error for error in errors))

    def test_only_three_nonempty_charts_and_summary_markdown(self):
        (self.root / "visualizations/extra.png").write_bytes(b"extra")
        (self.root / "visualizations" / next(iter(CHART_NAMES))).write_bytes(b"")
        (self.root / "visualizations/summary.md").unlink()
        errors = validate_artifacts(self.root)
        self.assertTrue(any("exactly the three required PNG" in error for error in errors))
        self.assertTrue(any(".png must exist and be nonempty" in error for error in errors))
        self.assertTrue(any("summary.md must exist and be nonempty" in error for error in errors))

    def test_raw_records_must_be_json_objects_and_nonempty(self):
        raw = self.root / "raw/upload_details.jsonl"
        for value in ("", "[]\n", "not json\n"):
            with self.subTest(value=value):
                raw.write_text(value)
                self.assertTrue(any("upload_details.jsonl" in error for error in validate_artifacts(self.root)))

    def test_measurement_scope_matches_metadata_and_disclaims_telemetry(self):
        self.metadata["telemetry_collected"] = "prometheus"
        self.summary["measurement_scope"] = "Different scope"
        self.write_metadata()
        self.write_summary()
        errors = validate_artifacts(self.root)
        self.assertTrue(any("telemetry_collected as none" in error for error in errors))
        self.assertTrue(any("measurement_scope does not match" in error for error in errors))

    def test_token_scans_all_files_and_crosses_read_boundaries(self):
        token = "dummy-fixture-token-321"
        (self.root / "extra.bin").write_bytes(b"x" * (64 * 1024 - 3) + token.encode() + b"end")
        with patch.dict("os.environ", {"VSS_AUTH_TOKEN": token}):
            errors = validate_artifacts(self.root)
        self.assertTrue(any("token found in extra.bin" in error for error in errors))
        self.assertNotIn(token, "\n".join(errors))

    def test_json_escaped_token_is_detected(self):
        token = 'private-"-token-321'
        (self.root / "extra.json").write_text(json.dumps({"echo": token}))
        with patch.dict("os.environ", {"VSS_AUTH_TOKEN": token}):
            errors = validate_artifacts(self.root)
        self.assertTrue(any("token found in extra.json" in error for error in errors))
        self.assertNotIn(token, "\n".join(errors))

    def test_diagnostics_never_expose_token_in_filename(self):
        token = "fixture-secret-321"
        (self.root / f"{token}.txt").write_text(token)
        output = io.StringIO()
        with patch.dict("os.environ", {"VSS_AUTH_TOKEN": token}), redirect_stdout(output):
            self.assertEqual(main(["--results-dir", str(self.root)]), 2)
        self.assertNotIn(token, output.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())


if __name__ == "__main__":
    unittest.main()
