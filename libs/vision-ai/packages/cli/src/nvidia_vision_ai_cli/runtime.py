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

"""The CLI Runtime: where config and memory live, and how a tool's settings resolve.

Layout under ``VISION_AI_HOME`` (default ``~/.vision-ai``)::

    <tool>/config.json    one file per tool, edited by `vision-ai configure <tool>`
    memory/main.db        the job store
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import TYPE_CHECKING
from typing import Any

from pydantic import BaseModel
from pydantic import SecretStr
from pydantic import ValidationError

from nvidia_vision_ai.tool import secret_fields

from .store import SqliteJobStore

if TYPE_CHECKING:
    from nvidia_vision_ai.tool import Tool

HOME_ENV = "VISION_AI_HOME"


class ConfigError(Exception):
    """The config file or environment for a tool is unreadable or invalid (exit 3)."""


class InputError(Exception):
    """Flags supplied for this call are invalid (exit 2)."""


def env_name(tool: str, field: str) -> str:
    return f"VISION_AI_{tool.upper()}_{field.upper()}"


@dataclass(frozen=True)
class Runtime:
    """Injects each tool's parameters from its config file, env and flags."""

    home: Path

    @classmethod
    def from_env(cls) -> Runtime:
        raw = os.environ.get(HOME_ENV, "").strip()
        return cls(Path(raw).expanduser() if raw else Path.home() / ".vision-ai")

    def config_path(self, tool: str) -> Path:
        return self.home / tool / "config.json"

    def memory_path(self) -> Path:
        return self.home / "memory" / "main.db"

    def _ensure_home(self) -> None:
        """``~/.vision-ai`` holds API keys and job records: create it owner-only."""
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)

    def store(self) -> SqliteJobStore:
        try:
            self._ensure_home()
            return SqliteJobStore(self.memory_path())
        except (OSError, sqlite3.Error) as exc:
            raise ConfigError(f"cannot open the job store at {self.memory_path()}: {exc}") from exc

    def read_config(self, tool: str) -> dict[str, Any]:
        path = self.config_path(tool)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path} must contain a JSON object")
        return data

    def write_config(self, tool: str, values: dict[str, Any]) -> Path:
        """Replace the tool's config file atomically and durably, readable by the owner only.

        Raises ``ConfigError`` if the directory or file cannot be written.
        """
        path = self.config_path(tool)
        try:
            self._ensure_home()
            path.parent.mkdir(mode=0o700, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".json")
        except OSError as exc:
            raise ConfigError(f"cannot write {path}: {exc}") from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(values, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            Path(tmp).chmod(0o600)
            Path(tmp).replace(path)
        except OSError as exc:
            Path(tmp).unlink(missing_ok=True)
            raise ConfigError(f"cannot write {path}: {exc}") from exc
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path

    def env_values(self, tool: Tool) -> dict[str, str]:
        values: dict[str, str] = {}
        for field in tool.Settings.model_fields:
            raw = os.environ.get(env_name(tool.name, field), "")
            if raw.strip():
                values[field] = raw.strip()
        return values

    def settings(self, tool: Tool, flags: dict[str, Any] | None = None) -> BaseModel:
        """Resolve flags > env > config file > defaults into ``tool.Settings``."""
        flags = flags or {}
        stored = {**self.read_config(tool.name), **self.env_values(tool)}
        try:
            return tool.Settings.model_validate({**stored, **flags})
        except ValidationError as exc:
            # Blame the source of the failing fields: a bad flag is this call's
            # mistake (exit 2); a bad file or env value is configuration (exit 3).
            failing = {err["loc"][0] for err in exc.errors() if err["loc"]}
            if failing and failing <= set(flags):
                raise InputError(validation_message(exc)) from exc
            raise ConfigError(
                f"invalid {tool.name} configuration ({self.config_path(tool.name)} or "
                f"{env_name(tool.name, '*')}): {validation_message(exc)}"
            ) from exc


def storable(settings: BaseModel, keys: set[str]) -> dict[str, Any]:
    """JSON for ``keys`` of ``settings`` as written to config.json (secrets in clear, mode 0600)."""
    data = settings.model_dump(mode="json", include=keys)
    for name in secret_fields(type(settings)) & keys:
        value = getattr(settings, name)
        data[name] = value.get_secret_value() if isinstance(value, SecretStr) else value
    return data


def validation_message(exc: ValidationError) -> str:
    """One line per pydantic error, joined: ``field: message; ...``."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or 'value'}: {err['msg']}" for err in exc.errors(include_url=False)
    )
