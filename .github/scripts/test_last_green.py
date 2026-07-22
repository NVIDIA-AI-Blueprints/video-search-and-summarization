#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for last_green.py. Run directly:

python3 .github/scripts/test_last_green.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import last_green as lg  # noqa: E402
from release_set import compute_release_set_id  # noqa: E402

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
COMMIT = "d" * 40
TS = "2026-07-14T00:00:00Z"


def make_release_set(digest=DIGEST_A) -> dict:
    release_set = {
        "schema_version": 1,
        "release_set_id": "",
        "source": {"repository": "org/repo", "commit": COMMIT},
        "images": [
            {
                "name": "vss-agent",
                "strategy": "build",
                "image": "ghcr.io/org/vss-agent",
                "tag": "develop-abc123def456",
                "digest": digest,
                "platforms": ["linux/amd64", "linux/arm64"],
                "source_path": "services/agent",
                "source_tree_sha": "c" * 40,
                "upstream_digest": None,
            },
            {
                "name": "vss-configurator",
                "strategy": "reuse-pinned",
                "image": "nvcr.io/nvidia/vss-core/vss-configurator",
                "tag": "3.2.1",
                "digest": None,
                "platforms": ["linux/amd64"],
                "source_path": None,
                "source_tree_sha": None,
                "upstream_digest": None,
            },
        ],
    }
    release_set["release_set_id"] = compute_release_set_id(release_set)
    return release_set


def make_result(release_set: dict, result="PASS", digest=DIGEST_A, **overrides) -> dict:
    payload = {
        "schema_version": 1,
        "release_set_id": release_set["release_set_id"],
        "result": result,
        "failure_class": None if result == "PASS" else "product",
        "tested_images": [
            {
                "name": "vss-agent",
                "image": "ghcr.io/org/vss-agent",
                "tag": "develop-abc123def456",
                "digest": digest,
            }
        ],
        "profile_matrix": ["base", "lvs", "search", "alerts"],
        "evidence_url": "https://example/evidence",
        "pipeline_url": "https://example/pipeline/1",
    }
    payload.update(overrides)
    return payload


class ValidateResultTest(unittest.TestCase):
    def test_valid_pass(self):
        release_set = make_release_set()
        self.assertEqual(lg.validate_result(make_result(release_set)), [])

    def test_missing_fields(self):
        problems = lg.validate_result({"schema_version": 1})
        self.assertTrue(any("release_set_id" in p for p in problems))
        self.assertTrue(any("pipeline_url" in p for p in problems))

    def test_bad_result_value(self):
        release_set = make_release_set()
        problems = lg.validate_result(make_result(release_set, result="MAYBE"))
        self.assertTrue(any("PASS or FAIL" in p for p in problems))

    def test_fail_requires_failure_class(self):
        release_set = make_release_set()
        payload = make_result(release_set, result="FAIL", failure_class=None)
        problems = lg.validate_result(payload)
        self.assertTrue(any("failure_class" in p for p in problems))

    def test_pass_requires_tested_images(self):
        release_set = make_release_set()
        payload = make_result(release_set, tested_images=[])
        problems = lg.validate_result(payload)
        self.assertTrue(any("tested images" in p for p in problems))

    def test_malformed_tested_digest(self):
        release_set = make_release_set()
        payload = make_result(release_set)
        payload["tested_images"][0]["digest"] = "not-a-digest"
        problems = lg.validate_result(payload)
        self.assertTrue(any("malformed digest" in p for p in problems))


class AdvanceTest(unittest.TestCase):
    def test_pass_advances_empty_lock(self):
        release_set = make_release_set()
        action, lock, problems = lg.advance_lock(
            lg.empty_lock(), make_result(release_set), release_set, TS
        )
        self.assertEqual((action, problems), ("advance", []))
        self.assertEqual(lock["release_set_id"], release_set["release_set_id"])
        self.assertEqual(lock["source_commit"], COMMIT)
        self.assertEqual(lock["advanced_at"], TS)
        self.assertEqual(lock["history"], [])

    def test_fail_holds_channel_unchanged(self):
        release_set = make_release_set()
        original = lg.empty_lock()
        original["release_set_id"] = "sha256:" + "9" * 64
        action, lock, problems = lg.advance_lock(
            original, make_result(release_set, result="FAIL"), release_set, TS
        )
        self.assertEqual((action, problems), ("hold", []))
        self.assertIs(lock, original)

    def test_digest_mismatch_rejects(self):
        release_set = make_release_set(digest=DIGEST_A)
        payload = make_result(release_set, digest=DIGEST_B)
        action, _, problems = lg.advance_lock(lg.empty_lock(), payload, release_set, TS)
        self.assertEqual(action, "reject")
        self.assertTrue(any("tested digest" in p for p in problems))

    def test_wrong_release_set_id_rejects(self):
        release_set = make_release_set()
        payload = make_result(release_set, release_set_id="sha256:" + "e" * 64)
        action, _, problems = lg.advance_lock(lg.empty_lock(), payload, release_set, TS)
        self.assertEqual(action, "reject")
        self.assertTrue(any("ID mismatch" in p for p in problems))

    def test_tampered_manifest_rejects(self):
        release_set = make_release_set()
        payload = make_result(release_set)
        release_set["images"][0]["tag"] = "tampered"
        action, _, problems = lg.advance_lock(lg.empty_lock(), payload, release_set, TS)
        self.assertEqual(action, "reject")
        self.assertTrue(any("integrity" in p for p in problems))

    def test_untested_pinned_image_rejects(self):
        release_set = make_release_set()
        payload = make_result(
            release_set,
            tested_images=[
                {
                    "name": "vss-other",
                    "image": "ghcr.io/org/vss-other",
                    "tag": "t",
                    "digest": DIGEST_A,
                }
            ],
        )
        action, _, problems = lg.advance_lock(lg.empty_lock(), payload, release_set, TS)
        self.assertEqual(action, "reject")
        self.assertTrue(any("never tested" in p for p in problems))

    def test_repeat_pass_is_idempotent_hold(self):
        release_set = make_release_set()
        payload = make_result(release_set)
        _, lock, _ = lg.advance_lock(lg.empty_lock(), payload, release_set, TS)
        action, again, problems = lg.advance_lock(lock, payload, release_set, TS)
        self.assertEqual((action, problems), ("hold", []))
        self.assertIs(again, lock)

    def test_advance_pushes_previous_to_history(self):
        first = make_release_set(digest=DIGEST_A)
        _, lock, _ = lg.advance_lock(lg.empty_lock(), make_result(first), first, TS)
        second = make_release_set(digest=DIGEST_B)
        action, lock2, _ = lg.advance_lock(
            lock, make_result(second, digest=DIGEST_B), second, TS
        )
        self.assertEqual(action, "advance")
        self.assertEqual(len(lock2["history"]), 1)
        self.assertEqual(lock2["history"][0]["release_set_id"], first["release_set_id"])
        self.assertEqual(
            lock2["history"][0]["acceptance"]["evidence_url"],
            "https://example/evidence",
        )

    def test_history_is_bounded(self):
        lock = lg.empty_lock()
        for index in range(lg.HISTORY_LIMIT + 3):
            digest = "sha256:" + format(index, "x").rjust(64, "0")
            release_set = make_release_set(digest=digest)
            _, lock, _ = lg.advance_lock(
                lock, make_result(release_set, digest=digest), release_set, TS
            )
        self.assertEqual(len(lock["history"]), lg.HISTORY_LIMIT)


class RollbackTest(unittest.TestCase):
    def test_rollback_restores_previous(self):
        first = make_release_set(digest=DIGEST_A)
        _, lock, _ = lg.advance_lock(lg.empty_lock(), make_result(first), first, TS)
        second = make_release_set(digest=DIGEST_B)
        _, lock, _ = lg.advance_lock(
            lock, make_result(second, digest=DIGEST_B), second, TS
        )
        restored, problems = lg.rollback_lock(lock)
        self.assertEqual(problems, [])
        self.assertEqual(restored["release_set_id"], first["release_set_id"])
        self.assertEqual(
            restored["acceptance"]["pipeline_url"],
            "https://example/pipeline/1",
        )
        self.assertEqual(restored["history"], [])

    def test_rollback_with_empty_history_fails(self):
        restored, problems = lg.rollback_lock(lg.empty_lock())
        self.assertEqual(len(problems), 1)
        self.assertIn("history is empty", problems[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
