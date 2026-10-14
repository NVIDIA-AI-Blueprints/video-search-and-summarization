# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ensure the production model downloader accepts NGC CLI 4.37's interface."""

import json
import os
import subprocess
from pathlib import Path


def test_fully_qualified_reference_needs_no_removed_org_option(tmp_path):
    script = (
        Path(__file__).resolve().parents[2]
        / "deploy/docker/services/rtvi/rtvi-cv/download-models.sh"
    )
    definitions = script.read_text().rsplit('\nmain "$@"', 1)[0]
    fake = tmp_path / "ngc"
    fake.write_text(
        "#!/usr/bin/env python3\nimport json,os,sys\n"
        "assert '--org' not in sys.argv, 'NGC 4.37 rejects --org'\n"
        "open(os.environ['CAPTURE'],'w').write(json.dumps(sys.argv[1:]))\n"
    )
    fake.chmod(0o755)
    capture = tmp_path / "args.json"
    subprocess.run(
        [
            "bash",
            "-c",
            definitions + '\ndownload_package "nvidia/tao/rtdetr:version" "nvidia"\n',
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CAPTURE": str(capture),
        },
        check=True,
        capture_output=True,
    )
    args = json.loads(capture.read_text())
    assert args[:4] == [
        "registry",
        "model",
        "download-version",
        "nvidia/tao/rtdetr:version",
    ]
    assert "--dest" in args
