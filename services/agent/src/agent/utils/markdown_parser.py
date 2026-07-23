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

import re
from typing import Any

_time_marker = re.compile(r"\[\s*(?:\d+\.\d{1,2}s?|\d{1,2}:\d{2}s?)(?:\s*-\s*(?:\d+\.\d{1,2}s?|\d{1,2}:\d{2}s?))?\s*\]")

_img_tag_marker = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def parse_table_or_blocktext(
    table_lines: list[str],
    textblock_lines: list[str] | None = None,
) -> dict[str, str | list[str]] | str:
    """Parse markdown table lines into a dictionary, or joined block text when no table."""
    if textblock_lines is None:
        textblock_lines = []
    result: dict[str, str | list[str]] = {}

    if not table_lines:
        if textblock_lines:
            cleaned_lines = []
            for text in textblock_lines:
                clean_txt = _img_tag_marker.sub("", text).strip()
                if clean_txt:
                    cleaned_lines.append(clean_txt)

            return _time_marker.sub("", "".join(cleaned_lines)).strip()

        return result

    for line in table_lines:
        line = line.strip()
        if not line or line.startswith("|---") or line == "|":
            continue
        parts = [p.strip().strip("*") for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) >= 2 and parts[0].lower() != "field":
            result[parts[0]] = parts[1] if len(parts) == 2 else parts[1:]
    return result


def parse_markdown_to_json(content: str) -> dict[str, Any]:
    """Parse markdown content into a structured JSON format."""
    lines = content.split("\n")
    result: dict[str, Any] = {}
    current_section: str | None = None
    current_subsection: str | None = None
    table_lines: list[str] = []
    textblock_lines: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("# "):
            result["title"] = line[2:].strip()
        elif line.startswith("## "):
            if (table_lines or textblock_lines) and current_section:
                if current_subsection:
                    if current_section not in result:
                        result[current_section] = {}
                    result[current_section][current_subsection] = parse_table_or_blocktext(table_lines, textblock_lines)
                else:
                    result[current_section] = parse_table_or_blocktext(table_lines, textblock_lines)
                table_lines = []
                textblock_lines = []

            current_section = line[3:].strip()
            current_subsection = None
        elif line.startswith("### "):
            if (table_lines or textblock_lines) and current_section:
                if current_subsection:
                    if current_section not in result:
                        result[current_section] = {}
                    result[current_section][current_subsection] = parse_table_or_blocktext(table_lines, textblock_lines)
                else:
                    if current_section is not None and current_section not in result:
                        result[current_section] = {}
                table_lines = []
                textblock_lines = []

            current_subsection = line[4:].strip()
            if current_section is not None and current_section not in result:
                result[current_section] = {}
        elif line.startswith("|"):
            table_lines.append(line)
        elif line.startswith("**Incident Snapshot:**"):
            if "Resources" not in result:
                result["Resources"] = {}
            # Try to find URL in parentheses on current line
            match = re.search(r"\((http[^)]+)\)", line)
            if match:
                result["Resources"]["Incident Snapshot"] = match.group(1)
            # Otherwise check next line for plain URL
            elif i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                url_match = re.match(r"(https?://\S+)", next_line)
                if url_match:
                    result["Resources"]["Incident Snapshot"] = url_match.group(1)
        elif line.startswith("**Incident Video:**"):
            if "Resources" not in result:
                result["Resources"] = {}
            # Try to find URL in parentheses on current line
            match = re.search(r"\((http[^)]+)\)", line)
            if match:
                result["Resources"]["Incident Video"] = match.group(1)
            # Otherwise check next non-empty line for plain URL.
            # FIX: Looks up to 2 lines ahead and skips blank lines, because the
            # URL may be separated from the label by a blank line (paragraph break
            # added to prevent PDF justify-spacing issues).
            else:
                for j in range(i + 1, min(i + 3, len(lines))):
                    next_line = lines[j].strip()
                    if not next_line:
                        continue
                    url_match = re.match(r"(https?://\S+)", next_line)
                    if url_match:
                        result["Resources"]["Incident Video"] = url_match.group(1)
                        break  # only exit once we've found a URL; non-URL lines are skipped so we keep scanning
        elif current_section == "Analysis Results" and line:
            textblock_lines.append(line.strip())

        i += 1

    # Handle remaining table at the end
    if (table_lines or textblock_lines) and current_section:
        if current_subsection:
            if current_section not in result:
                result[current_section] = {}
            result[current_section][current_subsection] = parse_table_or_blocktext(table_lines, textblock_lines)
        else:
            result[current_section] = parse_table_or_blocktext(table_lines, textblock_lines)

    return result
