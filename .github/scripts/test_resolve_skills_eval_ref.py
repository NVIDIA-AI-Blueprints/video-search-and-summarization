# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for resolve-skills-eval-ref.sh."""

from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("resolve-skills-eval-ref.sh")


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


class ResolveSkillsEvalRefTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.remote = root / "remote.git"
        self.worktree = root / "worktree"

        run("git", "init", "--bare", str(self.remote), cwd=root)
        run("git", "init", "-b", "develop", str(self.worktree), cwd=root)
        run("git", "config", "user.name", "Skills Eval Test", cwd=self.worktree)
        run("git", "config", "user.email", "skills-eval@example.com", cwd=self.worktree)
        (self.worktree / "fixture.txt").write_text("fixture\n", encoding="utf-8")
        run("git", "add", "fixture.txt", cwd=self.worktree)
        run("git", "commit", "-m", "test fixture", cwd=self.worktree)
        run("git", "remote", "add", "origin", str(self.remote), cwd=self.worktree)
        run("git", "push", "-u", "origin", "develop", cwd=self.worktree)
        self.develop_sha = run(
            "git", "rev-parse", "HEAD", cwd=self.worktree
        ).stdout.strip()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def resolve(self, date_suffix: str) -> subprocess.CompletedProcess[str]:
        return run(str(SCRIPT), date_suffix, cwd=self.worktree)

    def test_resolves_exact_annotated_daily_tag_to_commit_sha(self) -> None:
        run(
            "git",
            "tag",
            "-a",
            "nightly-20260806",
            "-m",
            "nightly fixture",
            cwd=self.worktree,
        )
        run("git", "push", "origin", "nightly-20260806", cwd=self.worktree)

        result = self.resolve("20260806")

        self.assertEqual(result.stdout.strip(), self.develop_sha)
        self.assertIn("using daily tag", result.stderr)

    def test_falls_back_to_develop_commit_sha_when_tag_is_absent(self) -> None:
        # A neighboring daily tag must not be treated as today's exact tag.
        run("git", "tag", "nightly-20260806", cwd=self.worktree)
        run("git", "push", "origin", "nightly-20260806", cwd=self.worktree)

        result = self.resolve("20260807")

        self.assertEqual(result.stdout.strip(), self.develop_sha)
        self.assertIn("is unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main()
