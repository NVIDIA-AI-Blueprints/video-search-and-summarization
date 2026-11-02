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
"""The offline detector (X5): the OWLv2 runner on the subprocess protocol with a stub model, the approved weights
step and its lock, and the throwaway environment. No weights are downloaded and no package is installed."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from evalv2.core.netguard import no_network
from evalv2.references import boxes as bx
from evalv2.references.detector import (
    OWLV2_MODEL,
    RUNNER_SCRIPT,
    DetectorError,
    SubprocessDetector,
    frames_needed,
    runner_command,
)

EVAL_DIR = Path(__file__).resolve().parents[2]
SCRIPTS = EVAL_DIR / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load("v2_detector")
weights = _load("v2_detector_weights")
env = _load("v2_detector_env")


def _png(path: Path, width: int, height: int, shade: int = 128) -> Path:
    raw = b"".join(b"\x00" + bytes([shade]) * (width * 3) for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def _run(args, request):
    return subprocess.run([sys.executable, str(RUNNER_SCRIPT), *args], input=json.dumps(request),
                          capture_output=True, text=True, check=False, timeout=120)


# ---------------------------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------------------------


def test_the_runner_speaks_the_protocol_with_the_stub_model(tmp_path):
    frames = [str(_png(tmp_path / "a.png", 64, 48)), str(_png(tmp_path / "b.png", 48, 64, shade=40))]
    detector = SubprocessDetector([sys.executable, str(RUNNER_SCRIPT), "--backend", "stub"])
    proposals = detector.propose(frames, ["wooden stick", "person", "person"])
    assert detector.identity["name"] == "stub-owlv2" and detector.identity["stub"] is True
    assert detector.identity["weights_sha256"] == runner.STUB_WEIGHTS_SHA256
    assert detector.identity["params"] == runner.DEFAULT_PARAMS
    assert set(proposals) == set(frames) and all(proposals.values())
    for frame in frames:
        assert all(bx.valid_box(p.box) and p.label in {"wooden stick", "person"} for p in proposals[frame])
        assert len(proposals[frame]) <= runner.DEFAULT_PARAMS["max_per_frame"]
        for label in ("wooden stick", "person"):
            assert sum(p.label == label for p in proposals[frame]) <= runner.DEFAULT_PARAMS["top_k"]
    assert SubprocessDetector(detector.command).propose(frames, ["wooden stick", "person"]) == proposals, \
        "deterministic"


def test_the_reply_reports_queries_and_timing(tmp_path):
    frame = str(_png(tmp_path / "a.png", 32, 32))
    done = _run(["--backend", "stub", "--top-k", "2"], {"protocol": runner.PROTOCOL, "frames": [frame],
                                                        "queries": ["stick", " stick ", "bag"]})
    reply = json.loads(done.stdout)
    assert reply["queries"]["given"] == ["stick", "bag"] and reply["timing"]["frames"] == 1
    assert reply["detector"]["params"]["top_k"] == 2


@pytest.mark.parametrize(("args", "request_body", "message"), [
    (["--backend", "stub"], {"protocol": "other", "frames": [], "queries": ["x"]}, "must speak evalv2-detector-v1"),
    (["--backend", "stub"], {"protocol": runner.PROTOCOL, "frames": ["/no/such.png"], "queries": ["x"]},
     "not found"),
    (["--backend", "stub"], {"protocol": runner.PROTOCOL, "frames": [], "queries": []}, "non-empty list"),
    (["--backend", "owlv2"], {"protocol": runner.PROTOCOL, "frames": [], "queries": ["x"]}, "--weights-dir"),
    (["--backend", "stub", "--threshold", "0"], {"protocol": runner.PROTOCOL, "frames": [], "queries": ["x"]},
     "invalid parameters"),
])
def test_the_runner_refuses_bad_requests(args, request_body, message):
    done = _run(args, request_body)
    assert done.returncode == 1 and message in done.stderr and done.stdout == ""


def test_the_owlv2_backend_refuses_weights_without_an_approved_lock(tmp_path):
    (tmp_path / runner.WEIGHTS_FILE).write_bytes(b"weights")
    done = _run(["--backend", "owlv2", "--weights-dir", str(tmp_path)],
                {"protocol": runner.PROTOCOL, "frames": [], "queries": ["x"]})
    assert done.returncode == 1 and "no weights lock" in done.stderr
    with pytest.raises(DetectorError, match="no weights lock"):
        SubprocessDetector(runner_command(sys.executable, tmp_path)).propose([], ["x"])


def test_a_reply_from_unapproved_weights_is_refused(tmp_path):
    frame = str(_png(tmp_path / "a.png", 16, 16))
    pinned = SubprocessDetector([sys.executable, str(RUNNER_SCRIPT), "--backend", "stub"],
                                expected_weights_sha256="a" * 64)
    with pytest.raises(DetectorError, match="not the approved"):
        pinned.propose([frame], ["x"])
    assert runner_command("/env/bin/python", "/w") == ["/env/bin/python", str(RUNNER_SCRIPT), "--backend", "owlv2",
                                                       "--weights-dir", "/w"]


def test_boxes_follow_owlv2s_padded_square():
    # A 200 x 100 image is padded to a 200 x 200 square (the image top-left): padded coordinates scale by 200, so
    # x 0.2-0.3 is pixels 40-60 of 200 (200-300) and y 0.05-0.15 is pixels 10-30 of 100 (100-300).
    assert runner.agent_box(0.25, 0.1, 0.1, 0.1, 200, 100) == [100.0, 200.0, 300.0, 300.0]
    assert runner.agent_box(0.1, 0.25, 0.1, 0.1, 100, 200) == [200.0, 100.0, 300.0, 300.0]  # tall: padding right
    assert runner.agent_box(0.5, 0.9, 0.2, 0.1, 200, 100) is None, "a box in the padding is no box"
    assert runner.agent_box(0.5, 0.48, 0.4, 0.1, 200, 100) == [860.0, 300.0, 1000.0, 700.0], "clipped to the image"
    assert runner.agent_box(0.5, 0.5, 0.001, 0.5, 100, 100) is None, "thinner than 4 units"


def test_suppression_selection_and_threshold():
    a = {"box": [100.0, 100.0, 300.0, 300.0], "score": 0.9, "label": "stick"}
    a_overlap = {"box": [110.0, 110.0, 310.0, 310.0], "score": 0.8, "label": "stick"}
    a_other_label = {**a_overlap, "label": "person"}
    far = {"box": [600.0, 600.0, 800.0, 800.0], "score": 0.3, "label": "stick"}
    kept = runner.nms([far, a_overlap, a, a_other_label], 0.5)
    assert kept == [a, a_other_label, far], "overlaps within a label go; other labels stay; best first"
    assert runner.select(kept, top_k=1, max_per_frame=5) == [a, a_other_label]
    assert runner.select(kept, top_k=5, max_per_frame=1) == [a]
    candidates = [([0.5, 0.5, 0.2, 0.2], 0.95, 0), ([0.5, 0.5, 0.2, 0.2], 0.05, 1)]
    detections = runner.frame_detections(candidates, ["stick", "bag"], 100, 100, runner.DEFAULT_PARAMS)
    assert detections == [{"box": [400.0, 400.0, 600.0, 600.0], "score": 0.95, "label": "stick"}]


def test_candidates_come_from_the_sigmoid_of_the_logits():
    logits = [[3.0, -5.0], [0.0, 2.0], [-3.0, -3.0]]
    boxes = [[0.5, 0.5, 0.1, 0.1], [0.2, 0.2, 0.1, 0.1], [0.8, 0.8, 0.1, 0.1]]
    found = runner.candidates_from_arrays(logits, boxes, 0.4)
    assert [(box, round(score, 4), query) for box, score, query in found] == [
        ([0.5, 0.5, 0.1, 0.1], 0.9526, 0), ([0.2, 0.2, 0.1, 0.1], 0.5, 0), ([0.2, 0.2, 0.1, 0.1], 0.8808, 1)]
    many = [[1.0] for _ in range(runner.PRE_NMS_PER_QUERY + 50)]
    assert len(runner.candidates_from_arrays(many, [[0.5, 0.5, 0.1, 0.1]] * len(many), 0.1)) == \
        runner.PRE_NMS_PER_QUERY


def test_the_runner_shares_the_box_reference_rules_and_reads_frame_sizes(tmp_path):
    assert (runner.MIN_SIDE, runner.SCALE) == (bx.MIN_SIDE, bx.SCALE)
    assert runner.MODEL_REPO == OWLV2_MODEL
    assert runner.image_size(_png(tmp_path / "f.png", 640, 360)) == (640, 360)
    jpeg = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xc0\x00\x11\x08\x01\xe0\x02\x80\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9")
    (tmp_path / "f.jpg").write_bytes(jpeg)
    assert runner.image_size(tmp_path / "f.jpg") == (640, 480)
    (tmp_path / "f.gif").write_bytes(b"GIF89a")
    with pytest.raises(runner.DetectorFailure):
        runner.image_size(tmp_path / "f.gif")


# ---------------------------------------------------------------------------------------------------------------
# The weights step and its lock
# ---------------------------------------------------------------------------------------------------------------

FILES = {"model.safetensors": b"weights" * 100, "config.json": b'{"model_type": "owlv2"}',
         "preprocessor_config.json": b"{}", "merges.txt": b"#version\n", "pytorch_model.bin": b"never fetched"}
COMMIT = "c" * 40


def _blob(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


class _FakeHub:
    """Hub metadata and downloads from memory: no network."""

    version = "fake"

    def __init__(self, *, license_name="apache-2.0", served=None, lfs_sha=None):
        self.license_name, self.served, self.downloads = license_name, dict(served or FILES), []
        self.lfs_sha = lfs_sha or hashlib.sha256(FILES["model.safetensors"]).hexdigest()

    def info(self, repo_id, revision):
        assert repo_id == OWLV2_MODEL
        return {"commit": COMMIT, "license": self.license_name, "files": [
            {"name": name, "bytes": len(data), "blob_id": _blob(data),
             "lfs_sha256": self.lfs_sha if name.endswith((".safetensors", ".bin")) else None}
            for name, data in FILES.items()]}

    def download(self, repo_id, filename, revision, local_dir):
        assert revision == COMMIT
        self.downloads.append(filename)
        path = Path(local_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.served[filename])
        return path


def _marker(tmp_path, **overrides):
    marker = {"approval": weights.APPROVAL_VERSION, "repo_id": OWLV2_MODEL, "revision": "main",
              "approved_by": "owner (test)", "approved_at": "2026-11-01", "max_download_mb": 1, **overrides}
    path = tmp_path / "weights-approval.json"
    path.write_text(json.dumps(marker))
    return path


def test_the_weights_download_refuses_without_approval_and_touches_no_network(tmp_path, capsys):
    def no_hub():
        raise AssertionError("the hub must not be reached")

    with no_network():
        assert weights.main(["download", "--home", str(tmp_path)], hub_factory=no_hub) == 2
    assert "needs the owner's approval marker" in json.loads(capsys.readouterr().err)["refused"]
    assert not (tmp_path / "weights").exists()


@pytest.mark.parametrize(("overrides", "message"), [
    ({"repo_id": "google/owlv2-large-patch14-ensemble"}, "not 'google/owlv2-base-patch16-ensemble'"),
    ({"revision": "v1"}, "40-character commit hash or 'main'"),
    ({"approved_by": " "}, "must name approved_by"),
    ({"max_download_mb": 0}, "positive integer"),
    ({"expected_sha256": "abc"}, "64-character SHA-256"),
    ({"approval": "evalv2-probe-approval-v1"}, "is not an evalv2-detector-weights-approval-v1 marker"),
])
def test_a_weights_marker_must_approve_exactly_this_download(tmp_path, capsys, overrides, message):
    code = weights.main(["download", "--approval", str(_marker(tmp_path, **overrides)), "--home", str(tmp_path)],
                        hub_factory=lambda: _FakeHub())
    assert code == 2 and message in json.loads(capsys.readouterr().err)["refused"]


def test_the_dry_run_names_the_download_without_the_hub(tmp_path, capsys):
    with no_network():
        assert weights.main(["download", "--approval", str(_marker(tmp_path)), "--home", str(tmp_path), "--dry-run"],
                            hub_factory=lambda: (_ for _ in ()).throw(AssertionError("no hub"))) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] and plan["allow"] == ["model.safetensors", "*.json", "*.txt"]


def test_an_approved_download_is_verified_file_by_file_then_locked(tmp_path, capsys):
    expected = hashlib.sha256(FILES["model.safetensors"]).hexdigest()
    hub = _FakeHub()
    code = weights.main(["download", "--approval", str(_marker(tmp_path, expected_sha256=expected)),
                         "--home", str(tmp_path)], hub_factory=lambda: hub)
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert sorted(hub.downloads) == ["config.json", "merges.txt", "model.safetensors", "preprocessor_config.json"]
    target = Path(result["weights_dir"])
    assert target.name == f"google--owlv2-base-patch16-ensemble@{COMMIT}"
    lock = runner.verify_lock(target)
    assert lock["weights_sha256"] == expected and lock["revision"] == COMMIT and lock["license"] == "apache-2.0"
    marker_bytes = _marker(tmp_path, expected_sha256=expected).read_bytes()
    assert lock["approval_sha256"] == hashlib.sha256(marker_bytes).hexdigest()
    assert weights.main(["verify", "--weights-dir", str(target)]) == 0
    (target / "config.json").write_bytes(b'{"model_type": "changed"}')
    with pytest.raises(runner.DetectorFailure, match="differs from the lock"):
        runner.verify_lock(target)
    capsys.readouterr()
    assert weights.main(["verify", "--weights-dir", str(target)]) == 2


@pytest.mark.parametrize(("hub", "overrides", "message", "fetched"), [
    (_FakeHub(served={**FILES, "model.safetensors": b"tampered" * 100}), {}, "the Hub reported", True),
    (_FakeHub(served={**FILES, "config.json": b'{"model_type": "x"}' + b" " * 4}), {}, "git blob id", True),
    (_FakeHub(served={**FILES, "merges.txt": b"#v\n"}), {}, "the Hub reported", True),
    (_FakeHub(), {"expected_sha256": "e" * 64}, "not the approved", False),
    (_FakeHub(license_name="cc-by-nc-4.0"), {}, "licence is 'cc-by-nc-4.0'", False),
    (_FakeHub(), {"max_download_mb": 0}, "positive integer", False),
])
def test_a_download_that_does_not_verify_writes_no_lock(tmp_path, capsys, hub, overrides, message, fetched):
    code = weights.main(["download", "--approval", str(_marker(tmp_path, **overrides)), "--home", str(tmp_path)],
                        hub_factory=lambda: hub)
    assert code == 2 and message in json.loads(capsys.readouterr().err)["refused"]
    assert not list(tmp_path.rglob(runner.LOCK_NAME)) and bool(hub.downloads) == fetched


def test_a_download_over_the_ceiling_is_refused_before_fetching(tmp_path, capsys):
    hub = _FakeHub()
    big = {**FILES, "model.safetensors": b"w" * (2 * 1024 * 1024)}
    hub.info = lambda repo, revision: {"commit": COMMIT, "license": "apache-2.0", "files": [
        {"name": n, "bytes": len(d), "blob_id": _blob(d), "lfs_sha256": "a" * 64 if n.endswith("safetensors") else None}
        for n, d in big.items()]}
    code = weights.main(["download", "--approval", str(_marker(tmp_path)), "--home", str(tmp_path)],
                        hub_factory=lambda: hub)
    assert code == 2 and "over the approved 1 MB" in json.loads(capsys.readouterr().err)["refused"]
    assert hub.downloads == []


# ---------------------------------------------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------------------------------------------


def test_the_environment_is_cpu_only_and_pinned_to_evals_lock():
    lines = [line for line in env.REQUIREMENTS.read_text().splitlines() if line and not line.startswith("#")]
    pins = dict(line.split("==") for line in lines)
    assert all(re.fullmatch(r"[a-z0-9-]+==[0-9][0-9A-Za-z.]*", line) for line in lines)
    assert "torch" not in pins and "torchvision" not in pins, "torch comes only from the CPU index"
    lock = (EVAL_DIR / "uv.lock").read_text()
    for name, version in [*pins.items(), ("torch", env.TORCH_VERSION)]:
        assert re.search(rf'name = "{name}"\nversion = "{re.escape(version)}"', lock), name
    commands = env.commands(Path("/h"))
    assert commands[1][-3:] == ["--index-url", env.TORCH_INDEX, f"torch=={env.TORCH_VERSION}"]
    assert env.TORCH_INDEX == "https://download.pytorch.org/whl/cpu"
    assert not any("hf_hub_download" in part or "snapshot" in part for c in commands for part in c)


def test_installing_needs_yes_and_records_the_environment(tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "uv" and command[1] == "venv":
            (Path(command[-1]) / "bin").mkdir(parents=True)
            (Path(command[-1]) / "bin" / "python").write_text("")
        stdout = json.dumps({"torch": "2.14.0+cpu", "torch_cuda": None, "transformers": "5.17.0"}) \
            if "-c" in command else "torch==2.14.0+cpu\ntransformers==5.17.0\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    with pytest.raises(env.EnvRefused, match="pass --yes"):
        env.install(tmp_path / "home", yes=False, run=fake_run)
    assert calls == []
    manifest = env.install(tmp_path / "home", yes=True, run=fake_run)
    assert manifest["versions"]["cpu_only"] and manifest["freeze"][0] == "torch==2.14.0+cpu"
    assert manifest["requirements_sha256"] == env.requirements_sha256()
    assert json.loads((tmp_path / "home" / env.MANIFEST_NAME).read_text())["env"] == env.ENV_VERSION
    with pytest.raises(env.EnvRefused, match="already holds an environment"):
        env.install(tmp_path / "home", yes=True, run=fake_run)


def test_a_cuda_build_of_torch_is_refused(tmp_path):
    def fake_run(command, **kwargs):
        if command[1:2] == ["venv"]:
            (Path(command[-1]) / "bin").mkdir(parents=True)
            (Path(command[-1]) / "bin" / "python").write_text("")
        stdout = json.dumps({"torch": "2.14.0+cu130", "torch_cuda": "13.0"}) if "-c" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    with pytest.raises(env.EnvRefused, match="not a CPU build"):
        env.install(tmp_path / "home", yes=True, run=fake_run)


def test_the_plan_changes_nothing(tmp_path, capsys):
    assert env.main(["plan", "--home", str(tmp_path / "home")]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["installed"] is False and len(plan["commands"]) == 3 and not (tmp_path / "home").exists()
    assert env.main(["install", "--home", str(tmp_path / "home")]) == 2


def test_ra1s_detector_frames_and_cpu_estimate_match_the_readiness_doc():
    manifest = {c["incident_id"]: c["duration_seconds"] for c in json.loads(
        (EVAL_DIR / "eval_reproducibility" / "dataset-manifest-v1.json").read_text())["clips"]}
    split = json.loads((EVAL_DIR / "eval_reproducibility" / "split-v2.json").read_text())
    dev, test = (frames_needed({c: manifest[c] for c in split[half]}) for half in ("dev", "test"))
    assert (dev["frames"], test["frames"]) == (3209, 6817)
    assert (dev["generator"], dev["validator"], test["generator"], test["validator"]) == (1612, 1597, 3424, 3393)
    estimate = env.estimate(env.MEASURED_SECONDS_PER_FRAME, ["dev", "test"])
    assert estimate["halves"]["dev"]["cpu_hours"] == 8.0 and estimate["halves"]["test"]["cpu_hours"] == 17.0
    doc = (EVAL_DIR / "methodology" / "v2-judge-detector-readiness.md").read_text()
    for figure in ("3,209", "6,817", "10,026", "9.0 s"):
        assert figure in doc, figure
