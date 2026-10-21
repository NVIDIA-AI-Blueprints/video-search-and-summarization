# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Live-image verification contracts using Docker responses, without a daemon."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_evaluation_embed",
    Path(__file__).parents[1] / "scripts/verify_evaluation_embed.py",
)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
SHA = "a" * 40


def fixture():
    return {
        "Id": "container-actual-id",
        "Name": "/actual-embed",
        "State": {"Running": True, "StartedAt": "2026-10-20T01:00:00Z"},
        "Image": "sha256:actual-image",
        "Config": {
            "Image": "vss-rt-embed-eval:" + SHA,
            "Env": [
                "COSMOS_FILE_INDEX_SAMPLING=true",
                "COSMOS_FILE_TORCHVISION_RESIZE=true",
                "COSMOS_FILE_MAX_RETAINED_FRAMES=1024",
                "COSMOS_FILE_MAX_RETAINED_BYTES=536870912",
                "NUM_VLM_PROCS=1",
                "VLM_BATCH_SIZE=1",
                "MODEL_PATH=Cosmos-Embed1-448p",
                "NVIDIA_API_KEY=do-not-print",
            ],
        },
    }


LOG = "Cosmos file preprocessing version=index-linspace-round-aa-v1 index_sampling=True antialias_resize=True cache_resize_before_retention=True retained_frames=1024 retained_bytes=536870912 per_decoder=true; additional memory needed"


def install(monkeypatch, instance=None, image=None, log=LOG, final=None):
    instance = instance or fixture()
    image = image or {
        "Id": instance["Image"],
        "Config": {"Labels": {verify.REVISION_LABEL: SHA}},
    }
    calls = []

    def docker(*args):
        calls.append(args)
        if args[0] == "logs":
            return SimpleNamespace(stdout="", stderr=log)
        if args[0] == "image":
            body = image
        else:
            body = final if final and args[2] == instance["Id"] else instance
        return SimpleNamespace(stdout=json.dumps([body]), stderr="")

    monkeypatch.setattr(verify, "docker", docker)
    return calls


def test_verifies_current_image_effective_settings_and_log_start(monkeypatch):
    calls = install(monkeypatch)
    receipt = verify.verify("actual-from-docker-ps", SHA)
    assert receipt["verified"]
    assert receipt["revision"] == SHA
    assert receipt["image_id"] == "sha256:actual-image"
    assert receipt["settings"]["COSMOS_FILE_MAX_RETAINED_BYTES"] == 536870912
    assert (
        "logs",
        "--since",
        "2026-10-20T01:00:00Z",
        "--tail",
        "10000",
        "container-actual-id",
    ) in calls
    assert "do-not-print" not in json.dumps(receipt)


@pytest.mark.parametrize(
    "log",
    [
        "",
        LOG.replace("index-linspace-round-aa-v1", "old-version"),
        LOG.replace("index_sampling=True", "index_sampling=False"),
        LOG.replace("retained_frames=1024", "retained_frames=2000"),
        LOG + "\n" + LOG.replace("retained_bytes=536870912", "retained_bytes=1"),
    ],
)
def test_startup_log_must_agree_with_effective_settings(monkeypatch, log):
    install(monkeypatch, log=log)
    with pytest.raises(ValueError, match="startup|Startup"):
        verify.verify("actual", SHA)


@pytest.mark.parametrize(
    "key,value",
    [
        ("COSMOS_FILE_INDEX_SAMPLING", "false"),
        ("COSMOS_FILE_MAX_RETAINED_BYTES", "0"),
        ("COSMOS_FILE_MAX_RETAINED_FRAMES", "invalid"),
        ("NUM_VLM_PROCS", "2"),
        ("VLM_BATCH_SIZE", "2"),
    ],
)
def test_rejects_unsafe_or_missing_settings(monkeypatch, key, value):
    instance = fixture()
    instance["Config"]["Env"] = [
        entry for entry in instance["Config"]["Env"] if not entry.startswith(key + "=")
    ]
    instance["Config"]["Env"].append(key + "=" + value)
    install(monkeypatch, instance=instance)
    with pytest.raises(ValueError, match=key):
        verify.verify("actual", SHA)


def test_rejects_wrong_revision_label(monkeypatch):
    install(
        monkeypatch,
        image={
            "Id": "sha256:actual-image",
            "Config": {"Labels": {verify.REVISION_LABEL: "b" * 40}},
        },
    )
    with pytest.raises(ValueError, match="revision"):
        verify.verify("actual", SHA)


def test_rejects_moved_tag(monkeypatch):
    install(monkeypatch)
    original = verify.docker

    def moved(*args):
        result = original(*args)
        if args[0] == "image" and args[2].startswith("vss-"):
            body = json.loads(result.stdout)
            body[0]["Id"] = "sha256:new-image"
            result.stdout = json.dumps(body)
        return result

    monkeypatch.setattr(verify, "docker", moved)
    with pytest.raises(ValueError, match="tag"):
        verify.verify("actual", SHA)


def test_rejects_container_restart_during_verification(monkeypatch):
    final = copy.deepcopy(fixture())
    final["State"]["StartedAt"] = "2026-10-20T02:00:00Z"
    install(monkeypatch, final=final)
    with pytest.raises(ValueError, match="restarted"):
        verify.verify("actual", SHA)


def test_failure_emits_json_with_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        verify,
        "verify",
        lambda *a: (_ for _ in ()).throw(ValueError("missing startup record")),
    )
    assert verify.main(["--container", "actual", "--revision", SHA]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "verified": False,
        "error": "missing startup record",
    }
