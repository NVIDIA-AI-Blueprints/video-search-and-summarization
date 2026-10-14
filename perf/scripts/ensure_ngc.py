#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Locate NGC CLI or install the official amd64 distribution under runtime data."""

import os
import shutil
import urllib.request
import zipfile
from pathlib import Path


def ensure_ngc(data: Path) -> str:
    existing = shutil.which("ngc")
    if existing:
        return existing
    tools = data / "tools"
    binary = tools / "ngc-cli/ngc"
    if binary.is_file():
        return str(binary)
    tools.mkdir(parents=True, exist_ok=True)
    archive = tools / "ngccli.zip"
    urllib.request.urlretrieve(
        "https://ngc.nvidia.com/downloads/ngccli_linux.zip", archive
    )
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = (tools / member.filename).resolve()
            if not target.is_relative_to(tools.resolve()):
                raise ValueError("Unsafe path in NGC CLI archive")
        source.extractall(tools)
    binary.chmod(0o755)
    return str(binary)


if __name__ == "__main__":
    print(ensure_ngc(Path(os.environ["VSS_DATA_DIR"])))
