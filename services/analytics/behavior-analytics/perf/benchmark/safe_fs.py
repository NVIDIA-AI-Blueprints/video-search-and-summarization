# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write benchmark files without following an existing symlink."""

from __future__ import annotations

import os
from pathlib import Path


def safe_write_text(path: Path, text: str) -> Path:
    """Create or replace ``path`` as a regular file. Unlink a symlink first."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    tmp.replace(dest)
    return dest
