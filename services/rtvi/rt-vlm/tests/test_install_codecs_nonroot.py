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

import os
import re
import subprocess
from pathlib import Path


INSTALLER = Path(__file__).parents[1] / "src" / "scripts" / "install_codecs_nonroot.sh"


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _run_installer(
    tmp_path: Path, *, ldd_output: str = "", gst_exit: int = 0, create_libav: bool = True
) -> tuple[subprocess.CompletedProcess[str], Path, str]:
    fake_bin = tmp_path / "bin"
    install_dir = tmp_path / "codecs"
    call_log = tmp_path / "calls.log"
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "apt-get",
        """#!/usr/bin/env bash
set -eu
downloading=false
index=0
for argument in "$@"; do
    if [ "$downloading" = true ]; then
        : > "fake-${index}.deb"
        index=$((index + 1))
    elif [ "$argument" = download ]; then
        downloading=true
    fi
done
""",
    )
    _write_executable(
        fake_bin / "dpkg",
        """#!/usr/bin/env bash
set -eu
if [ "${1:-}" = --print-architecture ]; then
    echo amd64
    exit 0
fi
if [ "${1:-}" = -x ] && [ "${FAKE_CREATE_LIBAV:-true}" = true ]; then
    plugin_dir="$3/usr/lib/x86_64-linux-gnu/gstreamer-1.0"
    mkdir -p "$plugin_dir"
    printf plugin > "$plugin_dir/libgstlibav.so"
fi
""",
    )
    _write_executable(
        fake_bin / "ldd",
        """#!/usr/bin/env bash
set -eu
printf 'ldd:%s\n' "$1" >> "$FAKE_CALL_LOG"
printf '%s\n' "${FAKE_LDD_OUTPUT:-}"
""",
    )
    _write_executable(
        fake_bin / "python3",
        """#!/usr/bin/env bash
set -eu
if [ "${1:-}" = -m ]; then
    printf 'pip\n' >> "$FAKE_CALL_LOG"
    exit 0
fi
payload=$(cat)
printf 'gstreamer\n' >> "$FAKE_CALL_LOG"
grep -F 'Gst.ElementFactory.find("avdec_aac")' <<< "$payload" >/dev/null
exit "${FAKE_GST_EXIT:-0}"
""",
    )

    environment = os.environ.copy()
    environment.update(
        {
            "CODEC_INSTALL_DIR": str(install_dir),
            "FAKE_CALL_LOG": str(call_log),
            "FAKE_CREATE_LIBAV": str(create_libav).lower(),
            "FAKE_GST_EXIT": str(gst_exit),
            "FAKE_LDD_OUTPUT": ldd_output,
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{environment['PATH']}",
        }
    )
    result = subprocess.run(
        ["bash", str(INSTALLER)],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )
    calls = call_log.read_text(encoding="utf-8") if call_log.exists() else ""
    return result, install_dir, calls


def test_installer_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)


def test_libav_runtime_dependency_is_downloaded() -> None:
    text = _installer_text()
    package_block = re.search(r"PACKAGES=\(\n(?P<body>.*?)\n\)", text, re.DOTALL)

    assert package_block is not None
    packages = {
        line.strip()
        for line in package_block.group("body").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "gstreamer1.0-libav" in packages
    assert "libsodium23" in packages


def test_installer_rejects_missing_libav_plugin(tmp_path: Path) -> None:
    result, install_dir, calls = _run_installer(tmp_path, create_libav=False)
    call_lines = calls.splitlines()

    assert result.returncode != 0
    assert "GStreamer libav plugin is missing" in result.stderr
    assert not (install_dir / ".installed").exists()
    assert "ldd:" not in calls
    assert "gstreamer" not in call_lines


def test_installer_rejects_unresolved_libav_dependency(tmp_path: Path) -> None:
    result, install_dir, calls = _run_installer(
        tmp_path, ldd_output="libsodium.so.23 => not found"
    )
    call_lines = calls.splitlines()

    assert result.returncode != 0
    assert "unresolved runtime dependencies" in result.stderr
    assert "libsodium.so.23" in result.stderr
    assert not (install_dir / ".installed").exists()
    assert "ldd:" in calls
    assert "gstreamer" not in call_lines


def test_installer_rejects_unavailable_aac_factory(tmp_path: Path) -> None:
    result, install_dir, calls = _run_installer(tmp_path, gst_exit=1)
    call_lines = calls.splitlines()

    assert result.returncode != 0
    assert "could not load the avdec_aac decoder" in result.stderr
    assert not (install_dir / ".installed").exists()
    assert "ldd:" in calls
    assert "gstreamer" in call_lines


def test_installer_marks_success_after_runtime_checks(tmp_path: Path) -> None:
    result, install_dir, calls = _run_installer(tmp_path)
    call_lines = calls.splitlines()

    assert result.returncode == 0, result.stderr
    assert (install_dir / ".installed").is_file()
    assert "ldd:" in calls
    assert "gstreamer" in call_lines
