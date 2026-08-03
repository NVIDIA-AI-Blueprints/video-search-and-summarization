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

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_entrypoint() -> ModuleType:
    entrypoint_path = Path(__file__).resolve().parents[4] / "docker" / "entrypoint.py"
    spec = importlib.util.spec_from_file_location("vss_agent_container_entrypoint", entrypoint_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_openai_api_key_gets_local_compatibility_placeholder(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", value)

    entrypoint = _load_entrypoint()
    entrypoint._ensure_openai_api_key()

    assert entrypoint.os.environ["OPENAI_API_KEY"] == "NOAPIKEYSET"


def test_operator_openai_api_key_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "operator-provided-key")  # pragma: allowlist secret

    entrypoint = _load_entrypoint()
    entrypoint._ensure_openai_api_key()

    assert entrypoint.os.environ["OPENAI_API_KEY"] == "operator-provided-key"


def test_main_exports_placeholder_before_starting_nat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("INSTALL_PROPRIETARY_CODECS", raising=False)
    entrypoint = _load_entrypoint()
    exec_args: list[object] = []
    monkeypatch.setattr(entrypoint.os, "execv", lambda *args: exec_args.extend(args))
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint.py", "serve"])

    entrypoint.main()

    assert entrypoint.os.environ["OPENAI_API_KEY"] == "NOAPIKEYSET"
    assert exec_args == [entrypoint.NAT_BIN, [entrypoint.NAT_BIN, "serve"]]
