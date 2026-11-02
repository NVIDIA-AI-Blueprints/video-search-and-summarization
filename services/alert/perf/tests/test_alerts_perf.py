# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import subprocess

import pytest
import yaml

PERF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF / "benchmark/core"))
from alert_results_adapter import to_test_cases  # noqa: E402
import vss_perf_benchmark as runner  # noqa: E402


def test_scenarios_use_only_agreed_modes_and_dataset():
    config = PERF / "configs/config.yaml"
    for name, mode in (
        ("quick_test", "synthetic_kafka"),
        ("full_alerts_latency", "e2e_kafka"),
    ):
        _, selected, dataset, workload = runner.scenario_settings(config, name)
        assert selected["benchmark_mode"] == mode
        assert dataset["tenant_id"] == "0573334707593577"
        assert workload["alert_category"] == "FOV Count Violation"
        args = runner.native_arguments(
            Path("/tmp/private"),
            selected,
            workload,
            Path("/datasets/test"),
            dataset,
            "H100",
            Path("/tmp/eval.env"),
        )
        assert ("--synthetic-alerts-injection" in args) == (name == "quick_test")
        assert ("--e2e-eval" in args) == (name == "full_alerts_latency")
        assert "--eval-type" in args and "accuracy" not in args


def test_native_latency_values_and_reliability_are_preserved():
    summary = {
        "summary_status": "success",
        "e2e_count": 25,
        "e2e_avg_sec": 1.0301,
        "e2e_p90_sec": 2.1827,
        "verification_failed_events": 25,
        "top_verification_failure_reason": "vlm_invalid_payload",
        "e2e_p99_censored": True,
    }
    case = to_test_cases({"status": "success", "summary": summary})[0]
    assert case["status"] == "FAIL"
    assert case["metrics"]["latency"]["e2e_p90_seconds"] == summary["e2e_p90_sec"]
    assert case["native_summary"] == summary
    assert "verification_failed_events=25" in case["error"]["message"]


def test_success_and_missing_data_have_distinct_results():
    passing = {
        "status": "success",
        "summary": {"summary_status": "success", "e2e_count": 10},
    }
    assert to_test_cases(passing)[0]["status"] == "PASS"
    passing["summary"]["e2e_count"] = 0
    case = to_test_cases(passing)[0]
    assert case["status"] == "FAIL"
    assert "e2e_seconds" not in case["metrics"]["latency"]
    with pytest.raises(ValueError, match="aggregate summary"):
        to_test_cases({"status": "success"})


def test_runtime_image_override_is_exact_and_original_tree_unchanged(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    docker = source / "deploy/docker"
    (docker / "services/alert").mkdir(parents=True)
    (docker / "developer-profiles/dev-profile-alerts").mkdir(parents=True)
    profile = docker / "developer-profiles/dev-profile-alerts/.env"
    profile.write_text('NGC_API_KEY="private-value"\nMODEL_NAME_2D="GDINO"\n')
    compose = docker / "services/alert/compose.yml"
    original = "services:\n  alert:\n    image: ${VSS_ALERT_IMAGE:-registry/vss-alert-ms}:${VSS_ALERT_TAG:-latest}\n"
    compose.write_text(original)
    monkeypatch.setattr(runner, "REPO_ROOT", source)
    settings = yaml.safe_load((PERF / "setup/configs/images.yaml").read_text())
    image = "registry/vss-alert-ms@sha256:" + "a" * 64
    deployment = runner.prepare_runtime(tmp_path / "runtime", image, settings)
    assert (
        yaml.safe_load((deployment / "services/alert/compose.yml").read_text())[
            "services"
        ]["alert"]["image"]
        == image
    )
    assert compose.read_text() == original
    assert (
        "private-value"
        not in (deployment / "developer-profiles/dev-profile-alerts/.env").read_text()
    )
    # Exercise the real native shell's path discovery and Python package imports
    # inside the relocated layout without deploying or contacting services.
    subprocess.run(
        ["bash", str(tmp_path / "runtime/eval/run_eval.sh"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "-m", "eval.scripts.deploy.apply_profile_config", "--help"],
        cwd=tmp_path / "runtime",
        check=True,
        capture_output=True,
        text=True,
    )


def test_dataset_paths_cannot_escape_dataset_root(tmp_path):
    config = yaml.safe_load((PERF / "configs/config.yaml").read_text())
    config["global"]["dataset"]["videos_subpath"] = "../../other"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="dataset-relative"):
        runner.scenario_settings(path, "quick_test")


@pytest.mark.parametrize(
    "native_exit,verification_failures,expected_exit", [(0, 0, 0), (0, 5, 0), (1, 0, 1)]
)
def test_pipeline_outcome_and_canonical_report(
    tmp_path, monkeypatch, native_exit, verification_failures, expected_exit
):
    import json
    from types import SimpleNamespace

    output = tmp_path / "results"
    dataset = tmp_path / "dataset"
    (dataset / "warehouse_chunks").mkdir(parents=True)
    (dataset / "warehouse_chunks/warehouse_1_540_7.mp4").touch()
    monkeypatch.setenv("IMAGE_TAG", "registry/vss-alert-ms:poc")
    monkeypatch.setenv("NGC_API_KEY", "test-value")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(runner, "hardware_profile", lambda: "H100")
    monkeypatch.setattr(runner, "prepare_dataset", lambda *_: dataset)

    def fake_runtime(work, *_):
        (work / "eval").mkdir()

    monkeypatch.setattr(runner, "prepare_runtime", fake_runtime)
    monkeypatch.setattr(runner, "run", lambda *_, **__: None)
    monkeypatch.setattr(
        runner.subprocess, "check_output",
        lambda command, **_: "registry/vss-alert-ms:poc" if "inspect" in command else "",
    )
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(runner, "collect_diagnostics", lambda *_: None)
    monkeypatch.setattr(
        runner, "discover_platform", lambda **_: {"gpu": {"model": "H100", "count": 1}}
    )

    class NativeProcess:
        def __init__(self, argv, **kwargs):
            self.returncode = native_exit

        def wait(self, **kwargs):
            if not native_exit:
                report = output / "native/ab-eval/alert_bridge_evaluation_status.json"
                report.parent.mkdir()
                report.write_text(
                    json.dumps(
                        {
                            "status": "success",
                            "summary": {
                                "summary_status": "success",
                                "e2e_count": 5,
                                "verification_failed_events": verification_failures,
                            },
                        }
                    )
                )
            return native_exit

        def poll(self):
            return native_exit

    monkeypatch.setattr(runner.subprocess, "Popen", NativeProcess)
    assert runner.main(["--output-json", str(output)]) == expected_exit
    reports = list((output / "vss-perf-results").glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["metadata"]["service"] == "Alerts-Verification"
    assert report["metadata"]["config"]["setup"] == "synthetic_kafka"
    if native_exit:
        assert report["test_cases"][-1]["error"]["stage"] == "benchmark"
    assert report["summary"]["overall_status"] == (
        "FAIL" if native_exit or verification_failures else "PASS"
    )
