# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ensure the production model downloader accepts NGC CLI 4.37's interface."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class ModelDownloadTests(unittest.TestCase):
    def test_fully_qualified_reference_needs_no_removed_org_option(self):
        repository = Path(__file__).resolve().parents[2]
        script = repository / "deploy/docker/services/rtvi/rtvi-cv/download-models.sh"
        definitions = script.read_text().rsplit('\nmain "$@"', 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / "ngc"
            fake.write_text(
                "#!/usr/bin/env python3\nimport json,os,sys\n"
                "assert '--org' not in sys.argv, 'NGC 4.37 rejects --org'\n"
                "open(os.environ['CAPTURE'],'w').write(json.dumps(sys.argv[1:]))\n"
            )
            fake.chmod(0o755)
            capture = root / "args.json"
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "CAPTURE": str(capture),
            }
            command = (
                definitions
                + '\ndownload_package "nvidia/tao/rtdetr:version" "nvidia"\n'
            )
            subprocess.run(
                ["bash", "-c", command], env=env, check=True, capture_output=True
            )
            args = json.loads(capture.read_text())
            self.assertEqual(
                args[:4],
                ["registry", "model", "download-version", "nvidia/tao/rtdetr:version"],
            )
            self.assertIn("--dest", args)


if __name__ == "__main__":
    unittest.main()
