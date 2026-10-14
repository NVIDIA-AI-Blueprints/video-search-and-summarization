#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CHECKER = Path(__file__).with_name("check_copyright_headers.py")
SPDX_HEADER = "# SPDX-License-Identifier: Apache-2.0\n"


class CopyrightHeaderCheckerTest(unittest.TestCase):
    def run_checker(self, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            for relative_path, content in files.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "--", *files], cwd=root, check=True)
            return subprocess.run(
                [sys.executable, str(CHECKER)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_rejects_yaml_and_dockerfiles_without_spdx(self) -> None:
        files = {
            "config.yml": "enabled: true\n",
            "config.yaml": "enabled: true\n",
            "Dockerfile": "FROM scratch\n",
            "Dockerfile.dev": "FROM scratch\n",
            "Dockerfile.dockerignore": ".git\n",
        }

        result = self.run_checker(files)

        self.assertEqual(result.returncode, 1)
        for path in files:
            self.assertIn(path, result.stdout)

    def test_accepts_yaml_and_dockerfiles_with_spdx(self) -> None:
        files = {
            "config.yml": SPDX_HEADER,
            "config.yaml": SPDX_HEADER,
            "Dockerfile": SPDX_HEADER,
            "Dockerfile.dev": SPDX_HEADER,
            "Dockerfile.dockerignore": SPDX_HEADER,
        }

        result = self.run_checker(files)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ignores_unscoped_and_excluded_files(self) -> None:
        result = self.run_checker(
            {
                "config.json": "{}\n",
                "services/ui/config.yaml": "enabled: true\n",
                "services/ui/Dockerfile": "FROM scratch\n",
            }
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
