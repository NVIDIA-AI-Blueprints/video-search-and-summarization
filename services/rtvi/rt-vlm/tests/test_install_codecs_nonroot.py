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

import re
import subprocess
from pathlib import Path


INSTALLER = Path(__file__).parents[1] / "src" / "scripts" / "install_codecs_nonroot.sh"


def _installer_text() -> str:
    return INSTALLER.read_text(encoding="utf-8")


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


def test_aac_decoder_is_verified_before_success_marker() -> None:
    text = _installer_text()

    verify_plugin = text.index('ldd "$LIBAV_PLUGIN"')
    verify_factory = text.index('Gst.ElementFactory.find("avdec_aac")')
    success_marker = text.index('touch "$INSTALL_DIR/.installed"')

    assert verify_plugin < success_marker
    assert verify_factory < success_marker
