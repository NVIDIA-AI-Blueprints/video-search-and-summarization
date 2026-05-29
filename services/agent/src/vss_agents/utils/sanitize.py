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

"""
Sanitizers for untrusted (user-controlled) data.

These helpers neutralize the security findings flagged by SonarQube under
``security-cwetop25`` when request-derived values (sensor names, video IDs,
filenames, URLs built from them) flow into logs, HTTP URL paths, or the
filesystem:

* :func:`scrub_log` — strip line breaks / control characters before logging
  (CWE-117, log injection / log forging).
* :func:`quote_path_segment` — percent-encode a value used as a single URL
  path segment (URL path injection).
* :func:`safe_basename` / :func:`confine_to_base` — keep filesystem writes
  inside an intended directory (CWE-22/23, path traversal).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote


def scrub_log(value: object) -> str:
    """Return ``value`` as a single-line string safe to write to a log.

    Removes carriage returns and line feeds (the log-forging vector) and any
    remaining C0 control characters except tab, so an attacker cannot inject
    forged log records or terminal escape sequences via user-controlled input.
    """
    text = str(value).replace("\r", "").replace("\n", "")
    return "".join(ch for ch in text if ch == "\t" or ord(ch) >= 0x20)


def quote_path_segment(value: str) -> str:
    """Percent-encode ``value`` for safe use as a single URL path segment.

    Encoding ``/`` and other reserved characters (``safe=""``) prevents a
    user-controlled identifier from altering the URL's path structure
    (e.g. ``../`` traversal or injecting extra path segments).
    """
    return quote(str(value), safe="")


def safe_basename(name: str) -> str:
    """Return the final path component of ``name``, rejecting traversal.

    Strips any directory portion so a user-controlled file name cannot escape
    its intended directory. Raises :class:`ValueError` for empty or
    traversal-only components (``""``, ``"."``, ``".."``).
    """
    base = os.path.basename(str(name))
    if base in ("", ".", ".."):
        raise ValueError(f"Unsafe path component: {name!r}")
    return base


def confine_to_base(base_dir: str | os.PathLike[str], *parts: str) -> Path:
    """Join ``parts`` under ``base_dir`` and verify the result stays inside it.

    Both the base and the candidate are fully resolved (symlinks and ``..``
    collapsed) before the containment check, so the returned path is
    guaranteed to live within ``base_dir``. Raises :class:`ValueError` when
    the joined path would escape the base directory.
    """
    base = Path(base_dir).resolve()
    candidate = base.joinpath(*parts).resolve()
    if candidate != base and not candidate.is_relative_to(base):
        raise ValueError(f"Resolved path {candidate} escapes base directory {base}")
    return candidate
