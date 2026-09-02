# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import base64
import hashlib
import json
import unittest
from pathlib import Path

from vss_agent_gateway.capabilities import (
    REQUIRED_VSS_SKILLS,
    CapabilityError,
    decode_receipt,
    encode_receipt,
)


def valid_receipt() -> dict[str, object]:
    return {
        "schema_version": 1,
        "attached_at": "2026-09-02T00:00:00Z",
        "sandbox": "my-agent",
        "harness": "openclaw",
        "identity_mode": "preserve",
        "vss_origin": "http://host.openshell.internal:7777",
        "runtime": {
            "repo_root": "/sandbox/video-search-and-summarization",
            "commit": "a" * 40,
        },
        "skills": sorted(REQUIRED_VSS_SKILLS),
        "ui_artifacts": {
            "version": "1.0",
            "envelope": "vss-ui-artifact",
            "kinds": ["vss.search.results", "vss.alert.incidents"],
        },
    }


class CapabilityReceiptTest(unittest.TestCase):
    def test_round_trips_a_complete_receipt(self) -> None:
        encoded, digest = encode_receipt(valid_receipt())

        receipt = decode_receipt(encoded, digest)

        self.assertEqual(receipt.harness, "openclaw")
        self.assertEqual(receipt.runtime_commit, "a" * 40)
        self.assertEqual(
            receipt.public_summary()["skill_count"], len(REQUIRED_VSS_SKILLS)
        )

    def test_required_catalog_matches_every_repository_skill(self) -> None:
        root = Path(__file__).resolve().parents[3]
        discovered = {path.parent.name for path in (root / "skills").rglob("SKILL.md")}

        self.assertEqual(REQUIRED_VSS_SKILLS, discovered)

    def test_rejects_a_digest_mismatch(self) -> None:
        encoded, _ = encode_receipt(valid_receipt())

        with self.assertRaisesRegex(CapabilityError, "digest does not match"):
            decode_receipt(encoded, "0" * 64)

    def test_rejects_missing_runtime_skills(self) -> None:
        payload = valid_receipt()
        payload["skills"] = ["vss-search-archive"]
        encoded, digest = encode_receipt(payload)

        with self.assertRaisesRegex(CapabilityError, "missing required VSS skills"):
            decode_receipt(encoded, digest)

    def test_dedicated_agent_may_attach_before_the_vss_origin_exists(self) -> None:
        payload = valid_receipt()
        payload["identity_mode"] = "dedicated"
        payload["vss_origin"] = ""
        encoded, digest = encode_receipt(payload)

        receipt = decode_receipt(encoded, digest)

        self.assertTrue(receipt.public_summary()["ready"])
        self.assertTrue(receipt.public_summary()["attached"])
        self.assertEqual(receipt.vss_origin, "")

    def test_preserved_agent_requires_an_operational_origin(self) -> None:
        payload = valid_receipt()
        payload["vss_origin"] = ""
        encoded, digest = encode_receipt(payload)

        with self.assertRaisesRegex(CapabilityError, "requires vss_origin"):
            decode_receipt(encoded, digest)

    def test_rejects_noncanonical_contract_fields(self) -> None:
        for path, value, message in (
            (("schema_version",), 2, "schema_version"),
            (("vss_origin",), "file:///tmp/vss", "vss_origin"),
            (("runtime", "repo_root"), "/tmp/runtime", "repo_root"),
            (("runtime", "commit"), "develop", "commit"),
            (("ui_artifacts", "version"), "2.0", "artifact version"),
        ):
            with self.subTest(path=path):
                payload = json.loads(json.dumps(valid_receipt()))
                target = payload
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                encoded, digest = encode_receipt(payload)
                with self.assertRaisesRegex(CapabilityError, message):
                    decode_receipt(encoded, digest)

    def test_rejects_invalid_base64_and_oversized_payload(self) -> None:
        with self.assertRaisesRegex(CapabilityError, "strict base64"):
            decode_receipt("%%%", "0" * 64)

        raw = b"x" * 256_001
        encoded = base64.b64encode(raw).decode("ascii")
        with self.assertRaisesRegex(CapabilityError, "1..256000"):
            decode_receipt(encoded, hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    unittest.main()
