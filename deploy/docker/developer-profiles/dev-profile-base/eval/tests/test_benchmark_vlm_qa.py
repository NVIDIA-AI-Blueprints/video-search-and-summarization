# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the NAT-free ``vss vlm`` QA benchmark (no live VLM)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from benchmark_vlm_qa import QaItem
from benchmark_vlm_qa import download_dataset
from benchmark_vlm_qa import dss_credential
from benchmark_vlm_qa import is_qa_item
from benchmark_vlm_qa import judge_api_key
from benchmark_vlm_qa import judge_completions_url
from benchmark_vlm_qa import latency_stats
from benchmark_vlm_qa import parse_score
from benchmark_vlm_qa import parse_vlm_stdout
from benchmark_vlm_qa import run_bounded
from benchmark_vlm_qa import run_vlm_item
from benchmark_vlm_qa import select_qa_items
from benchmark_vlm_qa import strip_think_tags
from benchmark_vlm_qa import vios_sensor_names
import pytest


def test_parse_vlm_stdout_skips_completion_marker() -> None:
    stdout = (
        json.dumps({"job_id": "vlm-1", "status": "completed", "answer": "Yes, PPE is worn."})
        + "\n"
        + json.dumps({"event": "vss_job_completed", "job_id": "vlm-1", "status": "completed", "exit_hint": 0})
        + "\n"
    )
    assert parse_vlm_stdout(stdout)["answer"] == "Yes, PPE is worn."


def test_parse_vlm_stdout_rejects_marker_only() -> None:
    stdout = json.dumps({"event": "vss_job_completed", "job_id": "vlm-1", "status": "completed"})
    with pytest.raises(ValueError, match="no JSON object"):
        parse_vlm_stdout(stdout)


def test_parse_score_uses_last_line() -> None:
    text = "The answers match semantically.\n1.0\n"
    score, reasoning = parse_score(text)
    assert score == 1.0
    assert "semantically" in reasoning


def test_parse_score_strips_think_blocks() -> None:
    text = "<think>ponder</think>\n0.8\n"
    score, _reasoning = parse_score(text)
    assert score == 0.8


def test_parse_score_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="could not extract"):
        parse_score("the score is 12")


def test_strip_think_tags() -> None:
    assert strip_think_tags("<agent-think>plan</agent-think>Yes") == "Yes"


def test_is_qa_item_skips_report_and_trajectory() -> None:
    assert is_qa_item({"evaluation_method": ["qa"], "ground_truth": "Yes"})
    assert not is_qa_item({"evaluation_method": ["trajectory"], "ground_truth": "Yes"})
    assert not is_qa_item({"evaluation_method": ["report"], "ground_truth": "gt/dev_base_001_report.json"})
    assert not is_qa_item({"evaluation_method": ["qa"], "ground_truth": ""})


def test_is_qa_item_requires_explicit_qa_marking() -> None:
    assert not is_qa_item({"ground_truth": "Yes"})
    assert not is_qa_item({"evaluation_method": [], "ground_truth": "Yes"})
    assert not is_qa_item({"evaluation_method": None, "ground_truth": "Yes"})


def test_select_qa_items_matches_video_stem(tmp_path: Path) -> None:
    video = tmp_path / "dev_base_001.mp4"
    video.write_bytes(b"not-a-real-video")
    items = [
        {
            "id": "qa_001",
            "query": "Did a worker drop any boxes in the video dev_base_001?",
            "ground_truth": "Yes, one box.",
            "evaluation_method": ["qa", "trajectory"],
        },
        {
            "id": "rep_001",
            "query": "Generate a report for the video dev_base_001",
            "ground_truth": "gt/dev_base_001_report.json",
            "evaluation_method": ["report"],
        },
    ]
    selected = select_qa_items(items, {video.stem: video, video.name: video})
    assert [item.id for item in selected.items] == ["qa_001"]
    assert selected.items[0].video_path == video


def test_select_qa_items_honors_explicit_video_field(tmp_path: Path) -> None:
    video = tmp_path / "warehouse.mp4"
    video.write_bytes(b"x")
    items = [
        {
            "id": "qa_002",
            "query": "What color is the vest?",
            "ground_truth": "Orange",
            "evaluation_method": ["qa"],
            "video_name": "warehouse",
        }
    ]
    selected = select_qa_items(items, {video.stem: video, video.name: video})
    assert selected.items[0].video_path == video


def test_select_qa_items_resolves_the_clip_from_the_expected_tool_call() -> None:
    """vss-devx-base names no video field; the trajectory's sensor_id is the reliable source."""
    video = Path("/clips/vss-sample-warehouse-4min.mp4")
    index = {video.stem: video, video.name: video}
    items = [
        {
            "id": "vqa_001",
            # Deliberately does not name the clip, so only the sensor_id can resolve it.
            "query": "Did a worker drop any boxes?",
            "ground_truth": "Yes, a worker dropped one box.",
            "evaluation_method": ["qa", "trajectory"],
            "trajectory_ground_truth": [
                {"name": "video_understanding", "params": {"sensor_id": "vss-sample-warehouse-4min"}, "step": 1}
            ],
        }
    ]
    selected = select_qa_items(items, index)
    assert [item.id for item in selected.items] == ["vqa_001"]
    assert selected.items[0].video_path == video


def test_select_qa_items_records_a_clarification_item_instead_of_failing_the_run() -> None:
    """`clarify_001` is a qa item with no clip by design; it must not abort the benchmark."""
    video = Path("/clips/dev_base_001.mp4")
    index = {video.stem: video, video.name: video}
    other = Path("/clips/dev_base_002.mp4")
    index |= {other.stem: other, other.name: other}
    items = [
        {
            "id": "clarify_001",
            "query": "Show me the video",
            "ground_truth": "The agent should ask the user to specify which video.",
            "evaluation_method": ["qa", "trajectory"],
            "trajectory_ground_truth": [{"name": "vst_video_list", "params": {}, "step": 1}],
        },
        {
            "id": "vqa_002",
            "query": "What happened in dev_base_001?",
            "ground_truth": "A box fell.",
            "evaluation_method": ["qa"],
        },
    ]
    selected = select_qa_items(items, index)
    assert [item.id for item in selected.items] == ["vqa_002"]
    assert [skip["id"] for skip in selected.skipped] == ["clarify_001"]
    assert "could not match" in selected.skipped[0]["reason"]


@pytest.mark.parametrize("limit", [0, -1])
def test_select_qa_items_zero_or_negative_limit_selects_nothing(tmp_path: Path, limit: int) -> None:
    video = tmp_path / "dev_base_001.mp4"
    video.write_bytes(b"x")
    items = [
        {
            "id": "qa_001",
            "query": "Did a worker drop any boxes in dev_base_001?",
            "ground_truth": "Yes, one box.",
            "evaluation_method": ["qa"],
        }
    ]
    assert select_qa_items(items, {video.stem: video, video.name: video}, limit=limit).items == []


def test_select_qa_items_stops_before_unresolvable_video(tmp_path: Path) -> None:
    """A --limit run must not fail on an item it was never going to evaluate."""
    video = tmp_path / "dev_base_001.mp4"
    video.write_bytes(b"x")
    other = tmp_path / "dev_base_002.mp4"
    other.write_bytes(b"x")
    index = {p.stem: p for p in (video, other)} | {p.name: p for p in (video, other)}
    items = [
        {
            "id": "qa_001",
            "query": "What happened in dev_base_001?",
            "ground_truth": "A box fell.",
            "evaluation_method": ["qa"],
        },
        {
            "id": "qa_002",
            "query": "A question naming no clip at all.",
            "ground_truth": "Something.",
            "evaluation_method": ["qa"],
        },
    ]
    selected = select_qa_items(items, index, limit=1)
    assert [item.id for item in selected.items] == ["qa_001"]
    assert selected.skipped == []


def test_run_bounded_returns_output_within_budget() -> None:
    rc, stdout, _stderr, timed_out = run_bounded([sys.executable, "-c", "print('hi')"], 30)
    assert (rc, stdout.strip(), timed_out) == (0, "hi", False)


def test_run_bounded_kills_stalled_child_and_its_grandchild(tmp_path: Path) -> None:
    """`vss` runs under `uv`, so the whole group must die, not just the direct child.

    A plain `Popen.kill()` leaves the grandchild — the process actually calling the
    VLM — running. The grandchild here reports survival by writing a marker file.
    """
    marker = tmp_path / "grandchild_alive"
    grandchild = f"import time; time.sleep(2); open(r'{marker}', 'w').write('alive')"
    stall = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(10)"

    started = time.perf_counter()
    _rc, _stdout, _stderr, timed_out = run_bounded([sys.executable, "-c", stall], 0.5)
    assert timed_out
    assert time.perf_counter() - started < 10

    time.sleep(3)
    assert not marker.exists(), "grandchild outlived the watchdog kill"


def _fake_vss(script: str) -> list[str]:
    """A stand-in for the `vss` argv prefix; `vlm run ...` lands harmlessly in sys.argv."""
    return [sys.executable, "-c", script]


def test_run_vlm_item_returns_answer_on_success(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    emit = (
        "import json; print(json.dumps("
        "{'job_id': 'vlm-1', 'status': 'completed', 'answer': 'A box fell.', 'model': 'cosmos-reason3'}))"
    )
    item = QaItem(id="qa_001", query="What happened?", ground_truth="A box fell.", video_path=video)

    answer, elapsed, returncode, error, served_model = run_vlm_item(
        vss=_fake_vss(emit), item=item, timeout_s=30, num_frames=4, model=None
    )

    assert (answer, returncode, error) == ("A box fell.", 0, "")
    assert served_model == "cosmos-reason3"
    assert elapsed > 0


def test_run_vlm_item_reports_failure_without_an_answer(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    fail = "import sys; print('rt-vlm unreachable', file=sys.stderr); sys.exit(3)"
    item = QaItem(id="qa_002", query="What happened?", ground_truth="A box fell.", video_path=video)

    answer, _elapsed, returncode, error, served_model = run_vlm_item(
        vss=_fake_vss(fail), item=item, timeout_s=30, num_frames=4, model=None
    )

    assert answer is None
    assert returncode == 3
    assert "rt-vlm unreachable" in error
    assert served_model is None


def test_run_vlm_item_addresses_a_sensor_instead_of_inlining_the_clip(tmp_path: Path) -> None:
    """`--file` inlines base64 and the VLM rejects the larger vss-devx-base clips."""
    video = tmp_path / "vss-sample-warehouse-4min.mp4"
    video.write_bytes(b"x")
    echo_argv = "import json, sys; print(json.dumps({'answer': ' '.join(sys.argv[1:]), 'model': 'cosmos-reason3'}))"
    item = QaItem(id="vqa_001", query="How many boxes?", ground_truth="One.", video_path=video)

    answer, _elapsed, _rc, _error, _served = run_vlm_item(
        vss=_fake_vss(echo_argv),
        item=item,
        timeout_s=30,
        num_frames=4,
        model=None,
        sensor="vss-sample-warehouse-4min",
    )

    assert "--sensor vss-sample-warehouse-4min" in str(answer)
    assert "--file" not in str(answer)


def test_run_vlm_item_falls_back_to_inline_media_without_a_sensor(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    echo_argv = "import json, sys; print(json.dumps({'answer': ' '.join(sys.argv[1:]), 'model': 'cosmos-reason3'}))"
    item = QaItem(id="qa_001", query="What happened?", ground_truth="A box fell.", video_path=video)

    answer, _elapsed, _rc, _error, _served = run_vlm_item(
        vss=_fake_vss(echo_argv), item=item, timeout_s=30, num_frames=4, model=None, sensor=None
    )

    assert f"--file {video}" in str(answer)
    assert "--sensor" not in str(answer)


def test_download_dataset_is_bounded_and_kills_a_stalled_download(tmp_path: Path) -> None:
    """An unresponsive DSS must not hang the run before a single item is measured."""
    stall = tmp_path / "stalling-nvdataset"
    stall.write_text(f"#!/bin/sh\nexec {sys.executable} -c 'import time; time.sleep(300)'\n", encoding="utf-8")
    stall.chmod(0o755)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exceeded"):
        download_dataset(tmp_path / "ds", nvdataset_bin=str(stall), timeout_s=2)
    assert time.monotonic() - started < 60


def test_download_dataset_reports_a_failing_download(tmp_path: Path) -> None:
    failing = tmp_path / "failing-nvdataset"
    failing.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    failing.chmod(0o755)

    with pytest.raises(RuntimeError, match="exit 7"):
        download_dataset(tmp_path / "ds", nvdataset_bin=str(failing), timeout_s=30)


def test_run_bounded_without_capture_still_reports_exit_status() -> None:
    """Leaving the child on our streams keeps download progress visible."""
    rc, stdout, stderr, timed_out = run_bounded([sys.executable, "-c", "raise SystemExit(5)"], 30, capture=False)
    assert (rc, stdout, stderr, timed_out) == (5, "", "", False)


def test_vios_sensor_names_reports_unreachable_rather_than_empty() -> None:
    """An empty sensor list and an unreachable VIOS must not look the same."""
    fail = "import sys; sys.exit(4)"
    assert vios_sensor_names(_fake_vss(fail), 30) is None

    empty = "import json; print(json.dumps({'count': 0, 'sensors': []}))"
    assert vios_sensor_names(_fake_vss(empty), 30) == set()

    listed = "import json; print(json.dumps({'count': 1, 'sensors': [{'name': 'warehouse'}]}))"
    assert vios_sensor_names(_fake_vss(listed), 30) == {"warehouse"}


def test_judge_url_normalizes_base() -> None:
    assert judge_completions_url("http://llm:8000") == "http://llm:8000/v1/chat/completions"
    assert judge_completions_url("http://llm:8000/v1") == "http://llm:8000/v1/chat/completions"
    assert judge_completions_url("http://llm:8000/v1/chat/completions") == "http://llm:8000/v1/chat/completions"


def test_judge_key_never_sends_an_nvidia_credential_to_a_third_party() -> None:
    """A run sets NGC_API_KEY for the dataset download; it must not leak to the judge."""
    env = {"NGC_API_KEY": "ngc-secret", "NVIDIA_API_KEY": "nvidia-secret"}
    assert judge_api_key("https://api.openai.com/v1/chat/completions", env) is None


def test_judge_key_uses_nvidia_credential_for_nvidia_and_onprem_hosts() -> None:
    env = {"NGC_API_KEY": "ngc-secret"}
    assert judge_api_key("https://integrate.api.nvidia.com/v1/chat/completions", env) == "ngc-secret"
    assert judge_api_key("http://10.86.83.113:30081/v1/chat/completions", env) == "ngc-secret"
    assert judge_api_key("http://localhost:8000/v1/chat/completions", env) == "ngc-secret"


def test_judge_key_prefers_the_explicitly_named_key() -> None:
    env = {"EVAL_LLM_JUDGE_API_KEY": "judge-key", "OPENAI_API_KEY": "openai-key", "NGC_API_KEY": "ngc-secret"}
    assert judge_api_key("https://api.openai.com/v1/chat/completions", env) == "judge-key"
    assert judge_api_key("http://localhost:8000/v1/chat/completions", env) == "judge-key"
    assert judge_api_key("https://api.openai.com/v1/chat/completions", {"OPENAI_API_KEY": "openai-key"}) == "openai-key"


def test_dss_credential_prefers_the_dataset_service_key_over_the_legacy_ngc_name() -> None:
    env = {"NVDATASET_API_KEY": "personal-key", "NGC_API_KEY": "global-key"}
    assert dss_credential(env) == "NVDATASET_API_KEY"
    assert dss_credential({"NGC_API_KEY": "global-key"}) == "NGC_API_KEY"


def test_dss_credential_accepts_a_starfleet_login_with_no_api_key(tmp_path: Path) -> None:
    """`nvdataset auth login` stores a token and needs no key; refusing to start would be wrong."""
    token = tmp_path / ".nvdataset" / "starfleet_token.json"
    token.parent.mkdir(parents=True)
    token.write_text("{}", encoding="utf-8")
    assert dss_credential({"HOME": str(tmp_path)}) == "starfleet token (nvdataset auth login)"


def test_dss_credential_reports_none_when_nothing_is_configured(tmp_path: Path) -> None:
    assert dss_credential({"HOME": str(tmp_path)}) is None
    assert dss_credential({"HOME": str(tmp_path), "NVDATASET_DOTENV_PATH": str(tmp_path / "absent.env")}) is None


def test_dss_credential_accepts_a_dotenv_file(tmp_path: Path) -> None:
    dotenv = tmp_path / "dss.env"
    dotenv.write_text("NVDATASET_API_KEY=nvapi-x\n", encoding="utf-8")
    env = {"HOME": str(tmp_path), "NVDATASET_DOTENV_PATH": str(dotenv)}
    assert dss_credential(env) == f"NVDATASET_DOTENV_PATH={dotenv}"


def test_latency_stats() -> None:
    stats = latency_stats([10.0, 20.0, 30.0, 40.0])
    assert stats["n"] == 4
    assert stats["mean"] == 25.0
    assert stats["p50"] == 25.0
    assert stats["min"] == 10.0
    assert stats["max"] == 40.0
