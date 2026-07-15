#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Regenerate all id fields in ci/perf-configs.yaml using perf_config_id_utils."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

from perf_config_id_utils import generate_perf_config_id_from_config

ID_LINE_RE = re.compile(r"^(\s*)-\s+id:\s*.*$")
KEY_VALUE_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"([^"]*)"\s*,?\s*$')
COMPOSE_COMMA_RE = re.compile(r'^(\s*composePath:\s*"[^"]*")\s*,\s*$')
LLM_GPUS_RE = re.compile(r'^(\s*)llmGpus:\s*"[^"]*"\s*,?\s*$')


def _parse_config_block(lines: list[str]) -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in lines:
        match = KEY_VALUE_RE.match(line)
        if match:
            cfg[match.group(1)] = match.group(2)
    return cfg


def regenerate_ids(
    text: str,
    default_vision_input_tokens: str,
) -> tuple[str, int, int, list[str]]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if ID_LINE_RE.match(line)]
    if not starts:
        return text, 0, 0, []

    updated = 0
    inserted = 0
    generated_ids: list[str] = []

    # Walk blocks in reverse so line insertions do not invalidate start indices.
    for idx in reversed(range(len(starts))):
        start = starts[idx]
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)

        cfg = _parse_config_block(lines[start + 1 : end])
        token_value = cfg.get("vision_input_tokens", default_vision_input_tokens)
        cfg["vision_input_tokens"] = token_value

        # Ensure each block has the vision_input_tokens field.
        if "vision_input_tokens" not in _parse_config_block(lines[start + 1 : end]):
            for i in range(start + 1, end):
                llm_match = LLM_GPUS_RE.match(lines[i])
                if llm_match:
                    indent = llm_match.group(1)
                    lines.insert(i + 1, f'{indent}vision_input_tokens: "{token_value}"')
                    inserted += 1
                    end += 1
                    break

        new_id = generate_perf_config_id_from_config(cfg, default_vision_input_tokens)
        generated_ids.append(new_id)

        indent_match = ID_LINE_RE.match(lines[start])
        indent = indent_match.group(1) if indent_match else ""
        lines[start] = f'{indent}- id: "{new_id}"'
        updated += 1

        # Normalize known malformed composePath lines that ended with a trailing comma.
        for i in range(start + 1, end):
            compose_match = COMPOSE_COMMA_RE.match(lines[i])
            if compose_match:
                lines[i] = compose_match.group(1)

    duplicate_ids = [id_value for id_value, count in Counter(generated_ids).items() if count > 1]

    output = "\n".join(lines)
    if text.endswith("\n"):
        output += "\n"
    return output, updated, inserted, duplicate_ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate id fields in ci/perf-configs.yaml")
    parser.add_argument("path", type=Path, nargs="?", default=Path("services/video-summarization/ci/perf-configs.yaml"))
    parser.add_argument(
        "--default-vision-input-tokens",
        default="9k",
        help="Fallback vision input tokens if vision_input_tokens is absent",
    )
    args = parser.parse_args()

    original = args.path.read_text()
    updated_text, count, inserted, duplicate_ids = regenerate_ids(
        original,
        args.default_vision_input_tokens,
    )
    args.path.write_text(updated_text)
    print(f"Updated {count} id fields in {args.path}")
    print(f"Inserted vision_input_tokens into {inserted} config block(s)")
    if duplicate_ids:
        print("Warning: duplicate ids detected:")
        for id_value in duplicate_ids:
            print(f"  - {id_value}")


if __name__ == "__main__":
    main()
