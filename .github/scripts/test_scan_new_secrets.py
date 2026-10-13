#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scan_new_secrets.py. Needs detect-secrets installed. Run directly:

python3 .github/scripts/test_scan_new_secrets.py
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scan_new_secrets as sns  # noqa: E402

OLD = 'db_password = "old-committed-value-1234"\n'  # pragma: allowlist secret
NEW = 'api_password = "this-is-a-new-secret-value"\n'  # pragma: allowlist secret
FIXTURE_ID = "a1b2c3d4e5" * 4  # pragma: allowlist secret


class ScanNewSecretsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.git("init", "-q", "-b", "develop")
        self.git("config", "user.email", "ci@example.com")
        self.git("config", "user.name", "ci")
        self.write("config.py", "import os\n" + OLD)
        self.write(".gitattributes", "*.bin filter=lfs diff=lfs merge=lfs -text\n")
        self.base = self.commit("base")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, path: str, text: str) -> None:
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text(text)

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def scan(self) -> int:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = sns.scan_new_secrets(self.repo, self.base)
        self.output = out.getvalue()
        return rc

    def assert_flags(self, path: str) -> None:
        """Exit 1 alone also means the hook crashed; require the finding itself."""
        self.assertEqual(self.scan(), 1)
        self.assertIn(f"Location:    {path}:", self.output)

    def test_no_change_passes(self) -> None:
        self.commit("empty")
        self.assertEqual(self.scan(), 0)

    def test_moved_existing_secret_passes(self) -> None:
        self.write("config.py", "# new first line\nimport os\n" + OLD)
        self.commit("shift")
        self.assertEqual(self.scan(), 0)

    def test_new_secret_in_existing_file_fails(self) -> None:
        self.write("config.py", "import os\n" + OLD + NEW)
        self.commit("add")
        self.assert_flags("config.py")

    def test_new_file_with_secret_fails(self) -> None:
        self.write("pkg/new.py", NEW)
        self.commit("add")
        self.assert_flags("pkg/new.py")

    def test_removed_secret_passes(self) -> None:
        self.write("config.py", "import os\n")
        self.commit("remove")
        self.assertEqual(self.scan(), 0)

    def test_renamed_file_keeps_its_secrets(self) -> None:
        self.git("mv", "config.py", "settings.py")
        self.commit("rename")
        self.assertEqual(self.scan(), 0)

    def test_copied_secret_in_new_file_fails(self) -> None:
        # copy.py also shadows the stdlib module if the tree is on sys.path.
        self.write("copy.py", OLD)
        self.commit("copy")
        self.assert_flags("copy.py")

    def test_lfs_and_symlink_paths_are_skipped(self) -> None:
        self.write("blob.bin", NEW)
        (self.repo / "link.py").symlink_to("config.py")
        self.commit("lfs and link")
        self.assertEqual(sns.changed_files(self.repo, self.base), [])
        self.assertEqual(self.scan(), 0)

    def test_fixture_object_ids_are_ignored(self) -> None:
        self.write("fixture.json", '{\n  "Id": "' + FIXTURE_ID + '",\n}\n')
        self.commit("fixture")
        self.assertEqual(self.scan(), 0)


if __name__ == "__main__":
    unittest.main()
