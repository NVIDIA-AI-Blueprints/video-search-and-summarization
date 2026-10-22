# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""`vision-ai vlm`: job recording, settings precedence, exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import completion
from conftest import run
import httpx
import pytest

from nvidia_vision_ai.memory import JobQuery
from nvidia_vision_ai_cli.store import SqliteJobStore

MEDIA = "https://example.com/clip.mp4"


def store(home: Path) -> SqliteJobStore:
    return SqliteJobStore(home / "memory" / "main.db")


def configure(home: Path, **values: Any) -> None:
    (home / "vlm").mkdir(parents=True, exist_ok=True)
    (home / "vlm" / "config.json").write_text(json.dumps(values))


def test_success_records_completed_job(home: Path, vlm_server: Any) -> None:
    configure(home, base_url="http://vlm:8000", model="m", api_key="secret")  # pragma: allowlist secret
    seen = vlm_server(lambda r: httpx.Response(200, json=completion()))
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 0, result.output
    assert out["status"] == "completed"
    assert out["type"] == "vlm"
    assert out["job_id"].startswith("vlm-")
    assert out["result"]["answer"] == "a person walks"
    assert out["input"]["prompt"] == "q"
    assert "api_key" not in out["input"]
    assert seen[0].headers["Authorization"] == "Bearer secret"
    assert store(home).get(out["job_id"]).model_dump(mode="json") == out


def test_precedence_flags_env_file_defaults(home: Path, vlm_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home, base_url="http://vlm:8000", model="file-m", fps=1, timeout=50)
    monkeypatch.setenv("VISION_AI_VLM_MODEL", "env-m")
    monkeypatch.setenv("VISION_AI_VLM_FPS", "2")
    seen = vlm_server(lambda r: httpx.Response(200, json=completion()))
    _, out = run("vlm", "--prompt", "q", "--media-url", MEDIA, "--fps", "4")
    body = json.loads(seen[0].content)
    assert body["model"] == "env-m"
    assert body["media_io_kwargs"] == {"video": {"fps": 4.0}}
    assert out["input"]["timeout"] == 50
    assert out["input"]["backend"] == "rt_vlm"


def test_model_defaults_to_the_only_listed_model(home: Path, vlm_server: Any) -> None:
    configure(home, base_url="http://vlm:8000")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "only-m"}]})
        return httpx.Response(200, json=completion())

    seen = vlm_server(handler)
    result, _ = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 0
    assert json.loads(seen[1].content)["model"] == "only-m"


def test_ambiguous_model_is_configuration_failure(home: Path, vlm_server: Any) -> None:
    configure(home, base_url="http://vlm:8000")
    vlm_server(lambda r: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}))
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 3
    assert out["status"] == "failed"
    assert out["error"]["code"] == "configuration"
    assert "lists 2 models" in out["error"]["message"]


def no_jobs(home: Path) -> bool:
    return not (home / "memory" / "main.db").exists() or store(home).list(JobQuery()) == []


def test_missing_base_url_is_rejected_before_the_job(home: Path) -> None:
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 3
    assert out["error"]["code"] == "configuration"
    assert "base_url is not set" in out["error"]["message"]
    assert no_jobs(home)


@pytest.mark.parametrize(
    ("response", "code", "token"),
    [
        (httpx.Response(400, text="bad"), 6, "backend_error"),
        (httpx.Response(200, json=completion("")), 6, "backend_error"),
    ],
)
def test_backend_failures(home: Path, vlm_server: Any, response: httpx.Response, code: int, token: str) -> None:
    configure(home, base_url="http://vlm:8000", model="m")
    vlm_server(lambda r: response)
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == code
    assert (out["status"], out["error"]["code"]) == ("failed", token)
    assert store(home).list(JobQuery(type="vlm", status="failed"))[0].job_id == out["job_id"]


def test_unreachable_backend_exits_4(home: Path, vlm_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from tenacity import AsyncRetrying
    from tenacity import retry_if_exception_type
    from tenacity import stop_after_attempt

    from nvidia_vision_ai.vlm import client as client_mod

    monkeypatch.setattr(
        client_mod,
        "create_retry_strategy",
        lambda retries, delay=2, *, exceptions: AsyncRetrying(
            retry=retry_if_exception_type(exceptions), stop=stop_after_attempt(retries), reraise=True
        ),
    )
    configure(home, base_url="http://vlm:8000", model="m")

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    vlm_server(refuse)
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 4
    assert out["error"]["code"] == "backend_unreachable"


def test_invalid_input_has_no_job(home: Path) -> None:
    configure(home, base_url="http://vlm:8000", model="m")
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA, "--fps", "0")
    assert result.exit_code == 2
    assert out["error"]["code"] == "invalid_input"
    assert "fps" in out["error"]["message"]
    assert no_jobs(home)


@pytest.mark.parametrize("extra", [("--file", "BOTH"), (), ("--prompt", "")])
def test_media_and_prompt_are_validated_before_model_discovery(home: Path, tmp_path: Path, extra: Any) -> None:
    # No model configured and no server: validation must fail first (exit 2), not discovery (exit 4).
    configure(home, base_url="http://127.0.0.1:9")
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    args = ["vlm", "--prompt", "q"]
    if extra == ("--file", "BOTH"):
        args += ["--media-url", MEDIA, "--file", str(clip)]
    elif extra:
        args += ["--media-url", MEDIA, *extra]
    result, out = run(*args)
    assert result.exit_code == 2, out
    assert out["error"]["code"] == "invalid_input"
    assert no_jobs(home)


def test_missing_file_is_invalid_input(home: Path, tmp_path: Path) -> None:
    configure(home, base_url="http://vlm:8000", model="m")
    result, out = run("vlm", "--prompt", "q", "--file", str(tmp_path / "nope.mp4"))
    assert result.exit_code == 2
    assert "does not exist" in out["error"]["message"]


def test_flag_overrides_an_invalid_env_value(home: Path, vlm_server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(home, base_url="http://vlm:8000", model="m")
    monkeypatch.setenv("VISION_AI_VLM_FPS", "not-a-number")
    vlm_server(lambda r: httpx.Response(200, json=completion()))
    result, _ = run("vlm", "--prompt", "q", "--media-url", MEDIA, "--fps", "2")
    assert result.exit_code == 0
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 3
    assert "VISION_AI_VLM_*" in out["error"]["message"]


@pytest.mark.parametrize("backend", ["rt_vlm", "cosmos_reason_nim"])
def test_explicit_max_frames_replaces_inherited_fps(home: Path, vlm_server: Any, backend: str) -> None:
    configure(home, base_url="http://vlm:8000", model="m", backend=backend, fps=2)
    seen = vlm_server(lambda r: httpx.Response(200, json=completion()))
    run("vlm", "--prompt", "q", "--media-url", MEDIA, "--max-frames", "64")
    assert json.loads(seen[0].content)["media_io_kwargs"] == {"video": {"num_frames": 64}}
    run("vlm", "--prompt", "q", "--media-url", MEDIA, "--max-frames", "64", "--fps", "1")
    assert json.loads(seen[1].content)["media_io_kwargs"] == {"video": {"fps": 1.0}}


def test_echoed_api_key_is_redacted(home: Path, vlm_server: Any) -> None:
    configure(home, base_url="http://vlm:8000", model="m", api_key="sk-top-secret")  # pragma: allowlist secret
    vlm_server(lambda r: httpx.Response(401, text="Invalid API key: sk-top-secret"))
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 6
    assert "sk-top-secret" not in result.output
    assert "sk-top-secret" not in json.dumps(store(home).get(out["job_id"]).model_dump(mode="json"))
    assert "***" in out["error"]["message"]


def test_api_key_with_newline_is_a_configuration_error(home: Path) -> None:
    configure(home, base_url="http://vlm:8000", model="m", api_key="sk-secret\r\n")  # pragma: allowlist secret
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 3
    assert "sk-secret" not in result.output
    assert "printable ASCII" in out["error"]["message"]


def test_interrupt_records_failed_job(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nvidia_vision_ai_cli.tools import vlm as vlm_tool

    async def interrupted(request: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(vlm_tool, "analyze", interrupted)
    configure(home, base_url="http://vlm:8000", model="m")
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 1
    assert (out["status"], out["error"]["message"]) == ("failed", "interrupted")
    assert store(home).get(out["job_id"]).status == "failed"


def test_invalid_config_file_exits_3(home: Path) -> None:
    configure(home, base_url="http://vlm:8000", sensor="cam")
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA)
    assert result.exit_code == 3
    assert "sensor" in out["error"]["message"]


@pytest.mark.parametrize(
    ("args", "match"),
    [
        (("vlm", "--media-url", MEDIA), "prompt"),
        (("vlm", "--prompt", "q", "--timeout", "abc"), "abc"),
        (("vlm", "--prompt", "q", "--backend", "nope"), "nope"),
        (("vlm", "--prompt", "q", "--bogus"), "--bogus"),
        (("memory", "get"), "JOB_ID"),
        (("memory", "list", "--limit", "0"), "0"),
    ],
)
def test_usage_errors_print_one_json_error(home: Path, args: tuple[str, ...], match: str) -> None:
    result, out = run(*args)
    assert result.exit_code == 2
    assert out["error"]["code"] == "invalid_input"
    assert match in out["error"]["message"]


def test_api_key_is_not_a_flag(home: Path) -> None:
    result, out = run("vlm", "--prompt", "q", "--media-url", MEDIA, "--api-key", "x")
    assert result.exit_code == 2
    assert "No such option" in out["error"]["message"]


def test_help_shows_typed_flags_and_upstream_negative_flag(home: Path) -> None:
    from click.testing import CliRunner

    from nvidia_vision_ai_cli.app import cli

    text = CliRunner().invoke(cli, ["vlm", "--help"]).stdout
    assert "--fps FLOAT" in text
    assert "--max-frames INTEGER" in text
    assert "--enable-reasoning / --disable-reasoning" in text
    assert "[rt_vlm|vllm|cosmos_reason_nim|openai]" in text
