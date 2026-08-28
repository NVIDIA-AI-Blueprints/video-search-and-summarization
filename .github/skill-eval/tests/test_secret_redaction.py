# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""No secret may leave the harness for GitHub.

Boundaries under test: the judge's own outputs (stdout -> test-stdout.txt
and judge.json, packed into the public artifact), the results tree
(TruffleHog scan -> replace -> quarantine -> verify, fail-closed), and
leg_report's rendered comment/summary. A judge that proved "the agent
printed NGC_CLI_API_KEY" by quoting the key published a live credential
through all of them (PR #1647 run 32535909071).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))  # leg_report imports redact_secrets as a sibling

import redact_secrets  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


judge = _load("gj_scrub", HARNESS / "verifiers" / "generic_judge.py")
leg_report = _load("lr_scrub", HARNESS / "leg_report.py")

FAKE_KEY = "nvapi-" + "A1b2C3d4" * 8  # 70 chars, the real NGC shape


def _finding(path, raw, detector="FakeDetector"):
    return {"Raw": raw, "DetectorName": detector,
            "SourceMetadata": {"Data": {"Filesystem": {"file": str(path)}}}}


class TestRedactText:
    def test_nvapi_shape_masked_alone(self, monkeypatch):
        """Agent-side keys are known only by shape; that layer must fire alone."""
        monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
        out = redact_secrets.redact_text(f"The key {FAKE_KEY} was exposed.")
        assert FAKE_KEY not in out and "nvapi-[REDACTED]" in out

    def test_env_value_masked_by_component_name(self, monkeypatch):
        monkeypatch.setenv("MY_SERVICE_TOKEN", "s3cr3t-no-prefix")
        out = redact_secrets.redact_text("echoed s3cr3t-no-prefix here")
        assert "s3cr3t-no-prefix" not in out and "[REDACTED]" in out

    def test_component_match_not_substring(self, monkeypatch):
        """KEYCLOAK_URL must not be treated as a secret name."""
        monkeypatch.setenv("KEYCLOAK_URL", "https://keycloak.internal:8443")
        assert "keycloak.internal" in redact_secrets.redact_text(
            "auth via https://keycloak.internal:8443")

    def test_short_values_left_alone(self, monkeypatch):
        monkeypatch.setenv("ENABLE_KEY", "true")
        assert redact_secrets.redact_text("the true rate") == "the true rate"

    def test_marker_is_markdown_safe(self, monkeypatch):
        """<redacted> would render as an HTML tag in a PR comment."""
        monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
        out = redact_secrets.redact_text(FAKE_KEY)
        assert "<" not in out and ">" not in out


class TestRedactObj:
    def test_scrubs_before_serialization_beats_escaping(self, monkeypatch):
        r"""A secret with a quote/backslash is changed by json.dumps, so
        post-serialization substring replacement misses it. Pre-serialization
        structure scrubbing must not."""
        secret = 'pa"ss\\word-123456'
        monkeypatch.setenv("SNEAKY_PASSWORD", secret)
        doc = json.dumps(redact_secrets.redact_obj({"note": f"saw {secret}"}))
        assert secret not in doc and json.dumps(secret)[1:-1] not in doc
        assert "[REDACTED]" in doc

    def test_dict_keys_scrubbed(self, monkeypatch):
        monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
        assert FAKE_KEY not in str(redact_secrets.redact_obj({FAKE_KEY: 1}))


class TestRedactTree:
    def test_scanner_unavailable_fails_closed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(redact_secrets.shutil, "which", lambda _: None)
        (tmp_path / "x.txt").write_text("hello")
        with pytest.raises(redact_secrets.ScannerUnavailable):
            redact_secrets.redact_tree(tmp_path)
        # ...and the CLI turns that into a nonzero exit for the workflow gate.
        assert redact_secrets.main(["--tree", str(tmp_path)]) == 2

    def test_literal_findings_replaced(self, tmp_path, monkeypatch):
        f = tmp_path / "verifier" / "judge.json"
        f.parent.mkdir()
        f.write_text(json.dumps({"rationale": f"saw {FAKE_KEY}"}))
        calls = {"n": 0}

        def fake_scan(root):
            calls["n"] += 1
            return [_finding(f, FAKE_KEY)] if calls["n"] == 1 else []
        monkeypatch.setattr(redact_secrets, "_scan", fake_scan)
        lines = redact_secrets.redact_tree(tmp_path)
        assert FAKE_KEY not in f.read_text()
        assert any(l.startswith("redacted:") for l in lines)

    def test_survivor_files_quarantined_and_verified(self, tmp_path, monkeypatch):
        """Encoded/composite/binary findings defeat literal replacement; the
        file must be stubbed out and a final scan must come back empty."""
        blob = tmp_path / "frame.bin"
        blob.write_bytes(b"\x89PNG\x00" + FAKE_KEY.encode() + b"\xff")
        calls = {"n": 0}

        def fake_scan(root):
            calls["n"] += 1
            # finding on scans 1 and 2 (binary defeats the literal pass),
            # gone on scan 3 (after quarantine).
            return [_finding(blob, FAKE_KEY, "NGC")] if calls["n"] <= 2 else []
        monkeypatch.setattr(redact_secrets, "_scan", fake_scan)
        lines = redact_secrets.redact_tree(tmp_path)
        text = blob.read_text()
        assert FAKE_KEY not in text and "quarantined" in text
        assert any(l.startswith("quarantined:") for l in lines)
        assert calls["n"] == 3, "a final verify scan must run"

    def test_findings_surviving_quarantine_refuse_publish(self, tmp_path, monkeypatch):
        f = tmp_path / "weird.dat"
        f.write_text("data")
        monkeypatch.setattr(redact_secrets, "_scan",
                            lambda root: [_finding(f, "some-secret-123")])
        with pytest.raises(RuntimeError, match="refusing to publish"):
            redact_secrets.redact_tree(tmp_path)

    def test_docker_mount_path_mapped_back(self, tmp_path):
        finding = _finding("/scan/step-1/verifier/judge.json", "x")
        mapped = redact_secrets._finding_file(finding, tmp_path)
        assert mapped == tmp_path / "step-1" / "verifier" / "judge.json"


class TestJudgeEndToEnd:
    def test_main_emits_no_secret_on_any_path(self, tmp_path, monkeypatch, capsys):
        """Through main(): spec query, check text, rationale, matched — the
        lot. Fails if any output path bypasses the scrub (the first version
        of this fix scrubbed the results list but printed the raw query)."""
        spec = tmp_path / "spec.json"
        spec.write_text(json.dumps({"expects": [{
            "query": f"never print the key (example leak: {FAKE_KEY})",
            "checks": [f"agent must not echo {FAKE_KEY}"],
        }]}))
        monkeypatch.setattr(judge, "locate_trajectory", lambda: None)
        monkeypatch.setattr(judge, "_run_checks", lambda checks, traj, timeout: [
            {"pass": False,
             "rationale": f"The key {FAKE_KEY} was exposed in the trajectory.",
             "matched": {FAKE_KEY: [f"curl -H 'Bearer {FAKE_KEY}'"]}},
        ])
        monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
        reward, details = tmp_path / "reward.txt", tmp_path / "judge.json"
        monkeypatch.setattr(sys, "argv", [
            "generic_judge.py", "--spec", str(spec), "--step", "1",
            "--reward-file", str(reward), "--details-file", str(details)])
        assert judge.main() == 0
        stdout = capsys.readouterr().out
        dumped = details.read_text()
        assert FAKE_KEY not in stdout, "stdout becomes public test-stdout.txt"
        assert FAKE_KEY not in dumped, "judge.json lands in the public artifact"
        assert json.loads(dumped)["reward"] == 0.0
        assert reward.read_text() == "0.0"


class TestReportBoundary:
    def test_tree_gated_before_read_and_writes_scrubbed(self, tmp_path, monkeypatch):
        """The comment posts from this render during the agent step, before
        the workflow pack step — the gate must run first, and both writes
        must come out clean."""
        gate = {"ran_before_read": None}
        monkeypatch.setattr(redact_secrets, "redact_tree",
                            lambda root: ["clean: no redactions needed"])

        def fake_collect(root):
            gate["ran_before_read"] = True
            return {"trials": [{"checks": []}]}
        monkeypatch.setattr(leg_report, "collect_leg", fake_collect)
        monkeypatch.setattr(leg_report, "spec_steps", lambda f: 1)
        monkeypatch.setattr(leg_report, "render_comment",
                            lambda *a, **k: f"### report\nthe key {FAKE_KEY} leaked")
        secret = 'qu"o\\te-secret-42'
        monkeypatch.setenv("SUMMARY_TOKEN", secret)
        monkeypatch.setattr(leg_report, "leg_summary",
                            lambda *a, **k: {"note": f"saw {secret}"})
        monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
        out, summary = tmp_path / "body.md", tmp_path / "leg-summary.json"
        rc = leg_report.main(["--results-root", str(tmp_path),
                              "--out", str(out), "--summary-json", str(summary)])
        assert rc == 0 and gate["ran_before_read"]
        assert FAKE_KEY not in out.read_text()
        assert "nvapi-[REDACTED]" in out.read_text()
        s = summary.read_text()
        assert secret not in s and json.dumps(secret)[1:-1] not in s

    def test_scanner_failure_propagates(self, tmp_path, monkeypatch):
        """An unscanned tree must read as BLOCKED, not render a comment."""
        def boom(root):
            raise redact_secrets.ScannerUnavailable("no scanner")
        monkeypatch.setattr(redact_secrets, "redact_tree", boom)
        with pytest.raises(redact_secrets.ScannerUnavailable):
            leg_report.main(["--results-root", str(tmp_path)])
