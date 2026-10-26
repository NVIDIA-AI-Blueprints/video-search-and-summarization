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

"""Resolution and validation for local video source locators."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from urllib.request import url2pathname
from uuid import uuid4

from .contracts import SourceInput, SourceRef
from .exceptions import UnsupportedSourceError


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """Validated absolute path and operation-local source identity."""

    path: Path
    source_id: str


def resolve_source(source: SourceInput, source_id: str | None = None) -> ResolvedSource:
    """Resolve a path, local file URI, or structural ``url`` source.

    Credentials and query strings are deliberately excluded from diagnostics.
    A missing file raises ``FileNotFoundError`` before any decoder is opened.
    """
    if source_id is not None and (
        not isinstance(source_id, str) or not source_id.strip()
    ):
        raise ValueError("source_id must be a nonempty string when supplied")
    supplied_id = source_id
    if isinstance(source, (str, Path)):
        locator = str(source)
    elif hasattr(source, "url"):
        locator = cast(SourceRef, source).url
        if supplied_id is None:
            raw_id = getattr(source, "source_id", None)
            if raw_id is not None:
                if not isinstance(raw_id, str) or not raw_id.strip():
                    raise ValueError("structural source_id must be a nonempty string")
                supplied_id = raw_id
    else:
        raise UnsupportedSourceError("Source must be a local path or file:// URI")

    if not isinstance(locator, str) or not locator:
        raise UnsupportedSourceError("Source does not contain a usable local locator")
    # Path objects and Windows drive-letter strings are local paths, not URI
    # schemes (urlsplit interprets ``C:\\`` as scheme ``c``).
    is_windows_drive = (
        len(locator) >= 3 and locator[0].isalpha() and locator[1:3] in (":\\", ":/")
    )
    try:
        parsed = (
            urlsplit(locator)
            if not isinstance(source, Path) and not is_windows_drive
            else None
        )
    except ValueError:
        raise UnsupportedSourceError("Malformed local source locator") from None
    if parsed is not None and parsed.scheme:
        if parsed.scheme.lower() != "file" or parsed.netloc not in ("", "localhost"):
            scheme = parsed.scheme.lower()
            raise UnsupportedSourceError(f"Unsupported source locator scheme: {scheme}")
        if parsed.query or parsed.fragment:
            raise UnsupportedSourceError(
                "Local file URI must not contain query or fragment data"
            )
        try:
            path = Path(url2pathname(parsed.path))
        except (UnicodeDecodeError, ValueError):
            raise UnsupportedSourceError("Malformed local file URI") from None
    else:
        path = Path(locator).expanduser()

    try:
        absolute = path.resolve(strict=True)
    except FileNotFoundError:
        raise FileNotFoundError(path) from None
    if not absolute.is_file():
        raise UnsupportedSourceError("Source path must identify a local file")
    return ResolvedSource(absolute, supplied_id or str(uuid4()))
