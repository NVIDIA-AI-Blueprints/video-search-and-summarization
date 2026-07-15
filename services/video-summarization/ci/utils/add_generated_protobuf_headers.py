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

"""Add the NVIDIA Apache-2.0 SPDX header to checked-in protobuf outputs."""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATED_PROTOBUF_FILES = (
    REPO_ROOT / "src/protos/nv_pb2.py",
    REPO_ROOT / "docker/logstash/pb_definitions/nv_pb.rb",
)

SPDX_COPYRIGHT_RE = re.compile(
    r"^(?P<prefix># SPDX-FileCopyrightText: Copyright \(c\) )"
    r"(?P<start_year>\d{4})(?:-\d{4})?"
    r"(?P<suffix> NVIDIA CORPORATION & AFFILIATES\. All rights reserved\.)$",
    re.MULTILINE,
)
SPDX_LICENSE_LINE = "# SPDX-License-Identifier: Apache-2.0"


def copyright_year_text(start_year: int, current_year: int) -> str:
    if start_year < current_year:
        return f"{start_year}-{current_year}"
    return str(start_year)


def header(current_year: Optional[int] = None) -> str:
    current_year = current_year or date.today().year
    return (
        "# SPDX-FileCopyrightText: Copyright (c) "
        f"{current_year} NVIDIA CORPORATION & AFFILIATES. All rights reserved.\n"
        f"{SPDX_LICENSE_LINE}\n"
        "#\n"
        '# Licensed under the Apache License, Version 2.0 (the "License");\n'
        "# you may not use this file except in compliance with the License.\n"
        "# You may obtain a copy of the License at\n"
        "#\n"
        "# http://www.apache.org/licenses/LICENSE-2.0\n"
        "#\n"
        "# Unless required by applicable law or agreed to in writing, software\n"
        '# distributed under the License is distributed on an "AS IS" BASIS,\n'
        "# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
        "# See the License for the specific language governing permissions and\n"
        "# limitations under the License.\n"
        "\n"
    )


def has_header(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return bool(SPDX_COPYRIGHT_RE.match(text)) and SPDX_LICENSE_LINE in text.splitlines()[:5]


def update_existing_header(text: str, current_year: int) -> tuple[str, bool]:
    match = SPDX_COPYRIGHT_RE.search(text)
    if not match:
        return text, False

    start_year = int(match.group("start_year"))
    updated_line = (
        f"{match.group('prefix')}"
        f"{copyright_year_text(start_year, current_year)}"
        f"{match.group('suffix')}"
    )
    updated_text = f"{text[:match.start()]}{updated_line}{text[match.end():]}"
    return updated_text, updated_text != text


def add_header(path: Path, current_year: Optional[int] = None) -> bool:
    current_year = current_year or date.today().year
    text = path.read_text(encoding="utf-8")
    updated_text, updated = update_existing_header(text, current_year)
    if updated:
        path.write_text(updated_text, encoding="utf-8")
        return True

    if has_header(path):
        return False

    path.write_text(f"{header(current_year)}{text}", encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that generated protobuf files already have the SPDX header.",
    )
    args = parser.parse_args()

    if args.check:
        missing = [path for path in GENERATED_PROTOBUF_FILES if not has_header(path)]
        if missing:
            for path in missing:
                print(f"missing SPDX header: {path.relative_to(REPO_ROOT)}")
            return 1
        return 0

    for path in GENERATED_PROTOBUF_FILES:
        if add_header(path):
            print(f"added SPDX header: {path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
