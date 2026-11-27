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
import tarfile
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
        monkeypatch.setattr(redact_secrets.shutil, "which", lambda name: None)
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
        real = tmp_path / "step-1" / "verifier" / "judge.json"
        real.parent.mkdir(parents=True)
        real.write_text("{}")
        finding = _finding("/scan/step-1/verifier/judge.json", "x")
        assert redact_secrets._finding_file(finding, tmp_path) == real

    def test_missing_file_is_fatal(self, tmp_path):
        """A finding pointing at a path that is not there cannot be redacted
        or quarantined, so it must refuse the publish, not pass silently."""
        finding = _finding("/scan/gone.txt", "x")
        with pytest.raises(RuntimeError, match="does not exist"):
            redact_secrets._finding_file(finding, tmp_path)


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
        monkeypatch.setattr(judge, "_run_checks", lambda checks, traj, timeout, **context: [
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
        order = []
        monkeypatch.setattr(redact_secrets, "redact_tree",
                            lambda root: order.append("redact") or ["clean"])

        def fake_collect(root):
            order.append("read")
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
        assert rc == 0 and order == ["redact", "read"], \
            "the gate must run before anything reads the tree"
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


class TestRoundFourGuards:
    def test_multipart_finding_quarantined_when_values_never_match(self, tmp_path, monkeypatch):
        """AWS-style: Raw is the id, RawV2 is id:secret on separate lines.
        Replacing the id blinds the rescan while the secret survives — the
        source file must be quarantined on the initial finding's evidence."""
        f = tmp_path / "trial.log"
        f.write_text("id AKIAFAKEID0000\nsecret wJalrXUtnFAKESECRET\n")
        composite = "AKIAFAKEID0000:wJalrXUtnFAKESECRET"
        calls = {"n": 0}

        def fake_scan(root):
            calls["n"] += 1
            # Initial scan reports only the composite; rescans see nothing
            # because the id got replaced.
            return [_finding(f, composite, "AWS")] if calls["n"] == 1 else []
        monkeypatch.setattr(redact_secrets, "_scan", fake_scan)
        lines = redact_secrets.redact_tree(tmp_path)
        text = f.read_text()
        assert "wJalrXUtnFAKESECRET" not in text and "quarantined" in text
        assert any("incomplete match" in l for l in lines)

    def test_unmappable_finding_is_fatal(self, tmp_path, monkeypatch):
        bad = {"Raw": "x" * 20, "DetectorName": "X", "SourceMetadata": {}}
        f = tmp_path / "a.txt"; f.write_text("x" * 20)
        monkeypatch.setattr(redact_secrets, "_scan", lambda root: [bad])
        with pytest.raises(RuntimeError, match="without a file path"):
            redact_secrets.redact_tree(tmp_path)

    def test_path_escape_is_fatal(self, tmp_path):
        finding = _finding("/scan/../../etc/passwd", "x" * 20)
        with pytest.raises(RuntimeError, match="escapes the tree"):
            redact_secrets._finding_file(finding, tmp_path)

    def test_binary_nvapi_quarantined_without_scanner_finding(self, tmp_path, monkeypatch):
        """The builtin layer must cover binaries: an ASCII token inside a
        non-UTF-8 file leaks the same, even when TruffleHog misses it."""
        blob = tmp_path / "frame.bin"
        blob.write_bytes(b"\xff\xfe" + FAKE_KEY.encode() + b"\x00")
        monkeypatch.setattr(redact_secrets, "_scan", lambda root: [])
        lines = redact_secrets.redact_tree(tmp_path)
        assert FAKE_KEY not in blob.read_text()
        assert any("builtin-binary" in l for l in lines)

    def test_json_quarantine_stub_parses(self, tmp_path):
        f = tmp_path / "judge.json"
        f.write_text("{}")
        redact_secrets._quarantine(f, tmp_path, ["NGC"])
        assert "redacted" in json.loads(f.read_text())

    def test_marker_idempotent_with_redacted_env_value(self, monkeypatch):
        """TOKEN=REDACTED must not grow [REDACTED] into [[REDACTED]]."""
        monkeypatch.setenv("WEIRD_TOKEN", "REDACTED")
        assert redact_secrets.redact_text("[REDACTED]") == "[REDACTED]"

    def test_malformed_scanner_stdout_is_fatal(self, tmp_path, monkeypatch):
        class P:
            returncode = 0
            stdout = "not json at all"
            stderr = ""
        monkeypatch.setattr(redact_secrets, "_run_scanner", lambda target: P())
        with pytest.raises(redact_secrets.ScannerUnavailable, match="unparseable"):
            redact_secrets._scan(tmp_path)

    def test_scan_error_flag_present(self):
        """--fail-on-scan-errors verified to exist in trufflehog 3.94.2;
        without it a partly-unscanned tree exits 0 and publishes."""
        assert "--fail-on-scan-errors" in redact_secrets._TRUFFLEHOG_ARGS
        assert "--no-verification" in redact_secrets._TRUFFLEHOG_ARGS


class TestRoundFiveGuards:
    def test_partial_multipart_match_still_quarantines(self, tmp_path, monkeypatch):
        """THE round-3/4 bug: Raw (the id) matches and is replaced, RawV2
        (id:secret) does not because the halves sit on different lines.
        Replacing the id blinds the rescan while the secret survives — the
        finding is neutralized only if its COMPLETE representation (RawV2)
        was replaced."""
        f = tmp_path / "trial.log"
        f.write_text("id AKIAFAKEID0000\nsecret wJalrXUtnFAKESECRET\n")
        finding = _finding(f, "AKIAFAKEID0000", "AWS")
        finding["RawV2"] = "AKIAFAKEID0000:wJalrXUtnFAKESECRET"
        calls = {"n": 0}

        def fake_scan(root):
            calls["n"] += 1
            return [finding] if calls["n"] == 1 else []
        monkeypatch.setattr(redact_secrets, "_scan", fake_scan)
        lines = redact_secrets.redact_tree(tmp_path)
        text = f.read_text()
        assert "wJalrXUtnFAKESECRET" not in text and "quarantined" in text
        assert any("incomplete match" in l for l in lines)

    def test_full_rawv2_match_is_not_quarantined(self, tmp_path, monkeypatch):
        """When the composite itself occurs literally, replacement covers
        both halves and quarantine would be a false positive."""
        f = tmp_path / "trial.log"
        f.write_text("cred AKIAFAKEID0000:wJalrXUtnFAKESECRET here\n")
        finding = _finding(f, "AKIAFAKEID0000", "AWS")
        finding["RawV2"] = "AKIAFAKEID0000:wJalrXUtnFAKESECRET"
        calls = {"n": 0}

        def fake_scan(root):
            calls["n"] += 1
            return [finding] if calls["n"] == 1 else []
        monkeypatch.setattr(redact_secrets, "_scan", fake_scan)
        lines = redact_secrets.redact_tree(tmp_path)
        text = f.read_text()
        assert "wJalrXUtnFAKESECRET" not in text and "quarantined" not in text
        assert not any("quarantined:" in l for l in lines)

    def test_bad_metadata_fatal_even_when_literal_replaced(self, tmp_path, monkeypatch):
        """Findings are pinned to files BEFORE any rewrite: replacement can
        blind the rescan, after which bad metadata would never be checked."""
        f = tmp_path / "a.txt"
        f.write_text("tok " + "x" * 20)
        bad = {"Raw": "x" * 20, "DetectorName": "X", "SourceMetadata": {}}
        monkeypatch.setattr(redact_secrets, "_scan", lambda root: [bad])
        with pytest.raises(RuntimeError, match="without a file path"):
            redact_secrets.redact_tree(tmp_path)
        assert "x" * 20 in f.read_text(), "no rewrite before validation"

    def test_file_symlink_replaced_not_followed(self, tmp_path, monkeypatch):
        outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
        outside.write_text(f"precious {FAKE_KEY}")
        tree = tmp_path / "tree"; tree.mkdir()
        (tree / "link.txt").symlink_to(outside)
        monkeypatch.setattr(redact_secrets, "_scan", lambda root: [])
        lines = redact_secrets.redact_tree(tree)
        assert outside.read_text() == f"precious {FAKE_KEY}", \
            "the gate must never write outside the tree"
        assert not (tree / "link.txt").is_symlink()
        assert "symlink removed" in (tree / "link.txt").read_text() + str(lines)

    def test_dir_symlink_not_traversed(self, tmp_path, monkeypatch):
        outside_dir = tmp_path.parent / f"{tmp_path.name}-outdir"
        outside_dir.mkdir()
        victim = outside_dir / "victim.txt"
        victim.write_text(f"target {FAKE_KEY}")
        tree = tmp_path / "tree"; tree.mkdir()
        (tree / "sub").symlink_to(outside_dir)
        monkeypatch.setattr(redact_secrets, "_scan", lambda root: [])
        redact_secrets.redact_tree(tree)
        assert victim.read_text() == f"target {FAKE_KEY}"
        assert not (tree / "sub").is_symlink()

    def test_judge_marker_idempotent(self, monkeypatch):
        monkeypatch.setenv("WEIRD_TOKEN", "REDACTED")
        assert judge._scrub_secrets("[REDACTED]") == "[REDACTED]"


class TestTrajectoryArtifactPackaging:
    def test_archive_includes_redacted_trajectory_and_excludes_raw_agent_files(
        self, tmp_path, monkeypatch
    ):
        run_id = "45599274929"
        leg_results = tmp_path / run_id
        trial = leg_results / "2026-11-25__12-19-13" / "step-3__abc123"
        (trial / "verifier").mkdir(parents=True)
        (trial / "artifacts" / "logs" / "artifacts" / "nemoclaw").mkdir(parents=True)
        (trial / "agent" / "sessions" / "session-env").mkdir(parents=True)
        (trial / "agent" / "sessions" / "shell-snapshots").mkdir(parents=True)
        (trial / "agent" / "setup").mkdir(parents=True)

        compat_secret = "compat-hub-secret-987654321"  # pragma: allowlist secret
        anth_secret = "anth-ci-secret-1234567890"  # pragma: allowlist secret
        escaped_secret = 'pa"ss\\word-escaped-4242'  # pragma: allowlist secret
        gw_url_tok = "a1b2c3d4e5f60718293a4b5c6d7e8f9011223344556"  # pragma: allowlist secret
        bearer_tok = "bearer-tok-abcdef0123456789xyz"  # pragma: allowlist secret
        json_field_tok = "openclaw-hook-tok-9988776655443322"  # pragma: allowlist secret
        env_dump_tok = "worker-generated-backend-tok-77665544"  # pragma: allowlist secret
        fake_sk = "sk-proj-" + "Z9y8X7w6" * 4  # pragma: allowlist secret
        fake_hf = "hf_" + "Q1w2E3r4" * 4  # pragma: allowlist secret
        fake_gh = "ghs_" + "M5n6B7v8" * 4  # pragma: allowlist secret

        monkeypatch.setenv("COMPATIBLE_API_KEY", compat_secret)
        monkeypatch.setenv("ANTHROPIC_API_KEY", anth_secret)
        monkeypatch.setenv("ESCAPED_SECRET", escaped_secret)

        nested_tool_json = json.dumps({"nested_secret": escaped_secret, "token": json_field_tok})
        trajectory_doc = {
            "schema_version": "ATIF-v1.2",
            "steps": [
                {
                    "source": "agent",
                    "message": "Onboarded NemoClaw and verified stack.",
                    "observation": (
                        f"compat={compat_secret} anth={anth_secret} ngc={FAKE_KEY}\n"
                        f"{fake_sk}\n{fake_hf}\n{fake_gh}\n"
                        f"dashboard=http://127.0.0.1:18789/#token={gw_url_tok}\n"
                        f"Authorization: Bearer {bearer_tok}\n"
                        f"AGENT_BACKEND_TOKEN={env_dump_tok}\n"
                        f"tool_json={nested_tool_json}"
                    ),
                }
            ],
        }
        (trial / "agent" / "trajectory.json").write_text(
            json.dumps(trajectory_doc), encoding="utf-8"
        )
        (trial / "result.json").write_text('{"reward": 1.0}\n', encoding="utf-8")
        (trial.parent / "result.json").write_text('{"trials": 1}\n', encoding="utf-8")
        (trial / "verifier" / "judge.json").write_text(
            json.dumps({"reward": 1.0, "note": f"checked {compat_secret}"}),
            encoding="utf-8",
        )
        (
            trial / "artifacts" / "logs" / "artifacts" / "nemoclaw" / "readiness.json"
        ).write_text('{"stages": [{"stage": "gateway_health", "status": "passed"}]}\n')

        # Raw agent scratch/session files that MUST stay excluded from public tarballs.
        (trial / "agent" / "codex.txt").write_text("raw codex stream\n")
        (trial / "agent" / "claude-code.txt").write_text("raw claude stream\n")
        (trial / "agent" / "openclaw.txt").write_text("raw openclaw stream\n")
        (trial / "agent" / "openclaw.session.jsonl").write_text("{}\n")
        (trial / "agent" / "instruction.txt").write_text("prompt\n")
        (trial / "agent" / "readiness.json").write_text("stale readiness\n")
        (trial / "agent" / "sessions" / ".claude.json").write_text("{}\n")
        (trial / "agent" / "sessions" / "session-env" / "env.sh").write_text("SECRET=1\n")
        (trial / "agent" / "sessions" / "shell-snapshots" / "snap.sh").write_text("alias x=y\n")
        (trial / "agent" / "setup" / "setup.log").write_text("setup\n")

        trial2 = leg_results / "2026-11-25__12-25-00" / "step-4__def456"
        (trial2 / "agent").mkdir(parents=True)
        (trial2 / "result.json").write_text('{"reward": 1.0}\n')
        (trial2 / "agent" / "trajectory.jsonl").write_text(
            json.dumps({"event": "turn", "secret": compat_secret}) + "\n"
        )

        monkeypatch.setattr(redact_secrets, "_scan", lambda root: [])
        tarball = tmp_path / "skills-eval-results.tar.gz"
        rc = redact_secrets.main(
            ["--tree", str(leg_results), "--tarball", str(tarball)]
        )
        assert rc == 0
        assert tarball.is_file()

        with tarfile.open(tarball, "r:gz") as tf:
            names = set(tf.getnames())
            traj_member = f"{run_id}/2026-11-25__12-19-13/step-3__abc123/agent/trajectory.json"
            trajl_member = f"{run_id}/2026-11-25__12-25-00/step-4__def456/agent/trajectory.jsonl"
            assert traj_member in names
            assert trajl_member in names
            assert f"{run_id}/2026-11-25__12-19-13/step-3__abc123/result.json" in names
            assert f"{run_id}/2026-11-25__12-19-13/step-3__abc123/verifier/judge.json" in names
            assert (
                f"{run_id}/2026-11-25__12-19-13/step-3__abc123/"
                "artifacts/logs/artifacts/nemoclaw/readiness.json"
            ) in names

            # Every non-trajectory file under `<trial>/agent/` must be excluded.
            agent_members = [n for n in names if "/agent/" in n]
            assert sorted(agent_members) == sorted([traj_member, trajl_member])

            # Trajectory must remain valid JSON with all turns preserved and zero secrets.
            extracted_traj = tf.extractfile(traj_member).read().decode("utf-8")
            parsed_traj = json.loads(extracted_traj)
            assert len(parsed_traj["steps"]) == 1
            assert "#token=[REDACTED]" in parsed_traj["steps"][0]["observation"]

            all_payloads = "\n".join(
                tf.extractfile(m).read().decode("utf-8", errors="replace")
                for m in tf.getmembers()
                if m.isfile()
            )
            for secret in (
                compat_secret,
                anth_secret,
                escaped_secret,
                json.dumps(escaped_secret)[1:-1],
                FAKE_KEY,
                gw_url_tok,
                bearer_tok,
                json_field_tok,
                env_dump_tok,
                fake_sk,
                fake_hf,
                fake_gh,
            ):
                assert secret not in all_payloads, f"leaked {secret!r}"

    def test_archive_fails_closed_and_deletes_stale_tarball_on_scan_error(
        self, tmp_path, monkeypatch
    ):
        leg_results = tmp_path / "run-123"
        trial = leg_results / "step-1"
        (trial / "agent").mkdir(parents=True)
        (trial / "result.json").write_text('{"reward": 1.0}\n')
        (trial / "agent" / "trajectory.json").write_text('{"steps": []}\n')

        tarball = tmp_path / "stale.tar.gz"
        tarball.write_bytes(b"stale-unredacted-tarball")
        monkeypatch.setattr(redact_secrets.shutil, "which", lambda name: None)

        rc = redact_secrets.main(
            ["--tree", str(leg_results), "--tarball", str(tarball)]
        )
        assert rc == 2
        assert not tarball.exists(), "stale tarball must be removed before scan"

    def test_archive_skips_empty_results_dir_without_result_json(
        self, tmp_path, monkeypatch
    ):
        leg_results = tmp_path / "run-empty"
        leg_results.mkdir()
        (leg_results / "notes.txt").write_text("no trials ran\n")
        tarball = tmp_path / "out.tar.gz"
        monkeypatch.setattr(redact_secrets, "_scan", lambda root: [])

        rc = redact_secrets.main(
            ["--tree", str(leg_results), "--tarball", str(tarball)]
        )
        assert rc == 0
        assert not tarball.exists()

    def test_json_escaped_scanner_finding_redacted_without_quarantine(
        self, tmp_path, monkeypatch
    ):
        """TruffleHog's JSON decoder can emit unescaped Raw while on disk
        trajectory.json stores the JSON-escaped string; literal+escaped
        matching must clean it in place without quarantining trajectory.json."""
        traj = tmp_path / "step-1" / "agent" / "trajectory.json"
        traj.parent.mkdir(parents=True)
        raw_secret = 'sec"ret\\val-987654321'  # pragma: allowlist secret
        traj.write_text(json.dumps({"steps": [{"source": "agent", "out": raw_secret}]}))
        calls = {"n": 0}

        def fake_scan(root):
            calls["n"] += 1
            return [_finding(traj, raw_secret, "CustomJSON")] if calls["n"] == 1 else []

        monkeypatch.setattr(redact_secrets, "_scan", fake_scan)
        lines = redact_secrets.redact_tree(tmp_path)
        parsed = json.loads(traj.read_text())
        assert "steps" in parsed and "redacted" not in parsed
        assert parsed["steps"][0]["out"] == "[REDACTED]"
        assert not any(l.startswith("quarantined:") for l in lines)


class TestWorkflowArtifactRedactionContract:
    @pytest.mark.parametrize(
        "workflow_rel",
        [
            ".github/workflows/skills-eval.yml",
            ".github/workflows/skills-eval-daily.yml",
        ],
    )
    def test_collect_results_loads_secrets_unconditionally_and_uses_tarball_gate(
        self, workflow_rel
    ):
        repo_root = HARNESS.parents[1]
        text = (repo_root / workflow_rel).read_text(encoding="utf-8")
        marker = "- name: Collect results for workflow artifact"
        start = text.index(marker)
        end = text.index("- name: Upload results artifact", start)
        block = text[start:end]

        assert "CI_ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}" in block
        source_idx = block.index("source /home/ubuntu/eval-coordinator/.env")
        anth_idx = block.index('export ANTHROPIC_API_KEY="$saved_ci_anthropic"')
        nemo_if_idx = block.index('if [ "$OPERATIONAL_HARNESS" = nemoclaw ]')
        redact_idx = block.index(
            'python3 .github/skill-eval/redact_secrets.py --tree "$LEG_RESULTS" --tarball "$LEG_TARBALL"'
        )
        assert source_idx < nemo_if_idx < redact_idx, (
            f"{workflow_rel}: coordinator .env must load on EVERY leg before "
            "both gateway cleanup and redact_secrets.py"
        )
        assert anth_idx < redact_idx
        assert "tar czf" not in block, (
            f"{workflow_rel}: archiving must go through redact_secrets.py --tarball"
        )
