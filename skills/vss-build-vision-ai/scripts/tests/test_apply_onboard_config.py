# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Contract for .openclaw/apply-onboard-config.py.

`onboard --from` rewrites ARGs in the custom Dockerfile and expects NemoClaw's
config generator to run again; a custom image cannot, so these values must be
applied to the inherited openclaw.json at build. Two live failures motivated it:
the sandbox kept the base image's model and limits, and kept a loopback-only
`allowedOrigins` so the Agent UI rejected the Brev link.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "apply_onboard_config", REPO_ROOT / ".openclaw" / "apply-onboard-config.py"
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)

BAKED = "inference/nvidia/nemotron-3-super-120b-a12b"


def base_config():
    return {
        "agents": {"defaults": {"model": {"primary": BAKED}}},
        "models": {"providers": {"inference": {"models": [
            {"id": "nvidia/nemotron-3-super-120b-a12b", "name": BAKED,
             "contextWindow": 131072, "maxTokens": 4096}]}}},
        "gateway": {"port": 18789, "controlUi": {
            "allowInsecureAuth": True, "dangerouslyDisableDeviceAuth": False,
            "allowedOrigins": ["http://127.0.0.1:18789"]}},
    }


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "openclaw.json"
    p.write_text(json.dumps(base_config(), indent=2))
    return p


def read(p):
    return json.loads(Path(p).read_text())


# --- model identity (the reported "sandbox keeps nemotron" bug) -----------------

def test_primary_model_ref_becomes_the_primary(cfg):
    mod.apply(str(cfg), {"NEMOCLAW_PRIMARY_MODEL_REF": "aws/anthropic/bedrock-claude-opus-5"})
    d = read(cfg)
    assert d["agents"]["defaults"]["model"]["primary"] == "inference/aws/anthropic/bedrock-claude-opus-5"
    m = d["models"]["providers"]["inference"]["models"][0]
    assert m["id"] == "aws/anthropic/bedrock-claude-opus-5"
    assert m["name"] == "inference/aws/anthropic/bedrock-claude-opus-5"


def test_nemoclaw_model_is_the_fallback(cfg):
    mod.apply(str(cfg), {"NEMOCLAW_MODEL": "inference/some/model"})
    assert read(cfg)["agents"]["defaults"]["model"]["primary"] == "inference/some/model"


def test_session_limits_are_applied(cfg):
    mod.apply(str(cfg), {"NEMOCLAW_MODEL": "m/x", "NEMOCLAW_CONTEXT_WINDOW": "200000",
                         "NEMOCLAW_MAX_TOKENS": "32000"})
    m = read(cfg)["models"]["providers"]["inference"]["models"][0]
    assert m["contextWindow"] == 200000 and m["maxTokens"] == 32000


def test_baked_limits_dropped_when_session_supplies_none(cfg):
    # A limit baked for a different model is worse than no limit: OpenClaw's own
    # catalog is right more often than the previous model's numbers.
    mod.apply(str(cfg), {"NEMOCLAW_MODEL": "aws/anthropic/bedrock-claude-opus-5"})
    m = read(cfg)["models"]["providers"]["inference"]["models"][0]
    assert "contextWindow" not in m and "maxTokens" not in m


def test_baked_limits_kept_when_the_model_is_unchanged(cfg):
    # Onboard forwards the model but not its limits; when the session model is the
    # baked one, the baked limits are right for it and must survive.
    changes = mod.apply(str(cfg), {"NEMOCLAW_MODEL": "nvidia/nemotron-3-super-120b-a12b"})
    m = read(cfg)["models"]["providers"]["inference"]["models"][0]
    assert m["contextWindow"] == 131072 and m["maxTokens"] == 4096
    assert not [c for c in changes if "dropped" in c]


def test_no_model_arg_leaves_model_untouched(cfg):
    mod.apply(str(cfg), {})
    assert read(cfg)["agents"]["defaults"]["model"]["primary"] == BAKED


# --- control UI origins (the reported "Browser origin not allowed" bug) ---------

def test_chat_ui_url_adds_the_remote_origin(cfg):
    mod.apply(str(cfg), {"CHAT_UI_URL": "https://chat.example.brevlab.com"})
    ui = read(cfg)["gateway"]["controlUi"]
    assert ui["allowedOrigins"] == ["http://127.0.0.1:18789", "https://chat.example.brevlab.com"]
    assert ui["allowInsecureAuth"] is False          # https
    assert ui["dangerouslyDisableDeviceAuth"] is True  # non-loopback UI host


def test_explicit_port_also_yields_the_portless_origin(cfg):
    mod.apply(str(cfg), {"CHAT_UI_URL": "https://host.example.com:8443"})
    assert read(cfg)["gateway"]["controlUi"]["allowedOrigins"] == [
        "http://127.0.0.1:18789", "https://host.example.com:8443", "https://host.example.com"]


def test_loopback_chat_url_keeps_device_auth(cfg):
    mod.apply(str(cfg), {"CHAT_UI_URL": "http://127.0.0.1:18789"})
    ui = read(cfg)["gateway"]["controlUi"]
    assert ui["allowedOrigins"] == ["http://127.0.0.1:18789"]
    assert ui["dangerouslyDisableDeviceAuth"] is False
    assert ui["allowInsecureAuth"] is True


def test_gateway_port_drives_the_loopback_origin(cfg):
    d = base_config(); d["gateway"]["port"] = 19999
    Path(cfg).write_text(json.dumps(d))
    mod.apply(str(cfg), {"CHAT_UI_URL": "https://x.example.com"})
    assert read(cfg)["gateway"]["controlUi"]["allowedOrigins"][0] == "http://127.0.0.1:19999"


def test_no_chat_url_leaves_origins_untouched(cfg):
    mod.apply(str(cfg), {"NEMOCLAW_MODEL": "m/x"})
    assert read(cfg)["gateway"]["controlUi"]["allowedOrigins"] == ["http://127.0.0.1:18789"]


def test_malformed_chat_url_is_ignored(cfg):
    mod.apply(str(cfg), {"CHAT_UI_URL": "not-a-url"})
    assert read(cfg)["gateway"]["controlUi"]["allowedOrigins"] == ["http://127.0.0.1:18789"]


# --- both together, the real onboard shape -------------------------------------

def test_full_onboard_arg_set(cfg):
    changes = mod.apply(str(cfg), {
        "NEMOCLAW_PRIMARY_MODEL_REF": "aws/anthropic/bedrock-claude-opus-5",
        "CHAT_UI_URL": "https://chat.example.brevlab.com",
    })
    d = read(cfg)
    assert d["agents"]["defaults"]["model"]["primary"] == "inference/aws/anthropic/bedrock-claude-opus-5"
    assert "https://chat.example.brevlab.com" in d["gateway"]["controlUi"]["allowedOrigins"]
    assert changes  # reported to the build log


# --- how .openclaw/Dockerfile delivers the values to this script ---------------

ONBOARD_ARGS = ("NEMOCLAW_PRIMARY_MODEL_REF", "NEMOCLAW_MODEL", "NEMOCLAW_CONTEXT_WINDOW",
                "NEMOCLAW_MAX_TOKENS", "CHAT_UI_URL")
DOCKERFILE = (REPO_ROOT / ".openclaw" / "Dockerfile").read_text()


def test_onboard_rewrites_the_global_arg_for_every_value():
    # onboard's patcher rewrites only the first `ARG <name>=` in the file
    # (/m, no /g). An `ARG <name>=` added to an earlier stage would absorb that
    # rewrite and leave the global default empty, and onboard warns about nothing.
    first_from = re.search(r"^FROM ", DOCKERFILE, re.M).start()
    for name in ONBOARD_ARGS:
        first_arg = re.search(rf"^ARG {name}=", DOCKERFILE, re.M)
        assert first_arg, f"no global `ARG {name}=` for onboard to rewrite"
        assert first_arg.start() < first_from, (
            f"the first `ARG {name}=` is inside a build stage; onboard would rewrite "
            "it instead of the global default")


def test_every_value_reaches_this_script_as_a_file():
    # The base image exports all five as ENV and the legacy builder lets that ENV
    # win over a same-named ARG, so the final RUN has to read the onboard-args
    # stage's files; from the environment it would read the base image's values.
    for name in ONBOARD_ARGS:
        assert re.search(rf'{name}="\$\(cat /etc/vss-onboard-args/{name}\)"', DOCKERFILE), (
            f"{name} is not read from /etc/vss-onboard-args/ into "
            "vss-apply-onboard-config — the base image's ENV would shadow it")
