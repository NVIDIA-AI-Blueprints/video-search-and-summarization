#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for release_set.py. Run directly:

    python3 .github/scripts/test_release_set.py

Uses a synthetic mini-repository (temp dir with compose files + inventory) so
every behavior is exercised hermetically, plus a closing test against the real
repository inventory.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_set as rs  # noqa: E402

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
TREE_SHA = "c" * 40
COMMIT_SHA = "d" * 40


def make_repo(
    tmp: str,
    compose: dict[str, str],
    inventory_images: list[dict],
    roots: list[str] | None = None,
) -> Path:
    """Materialize a mini repo: deploy/docker compose files + inventory."""
    root = Path(tmp)
    for rel, text in compose.items():
        path = root / "deploy/docker" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    inventory = {
        "schema_version": 1,
        "first_party_registry_roots": roots
        or ["nvcr.io/nvidia/vss-core", "nvcr.io/nvstaging/vss-core"],
        "images": inventory_images,
    }
    (root / "deploy/docker/container-inventory.json").write_text(json.dumps(inventory))
    return root


AGENT_ENTRY = {
    "name": "vss-agent",
    "strategy": "build",
    "ghcr_build": True,
    "source_path": "services/agent",
    "platforms": ["linux/amd64", "linux/arm64"],
    "compose_image_names": ["vss-agent"],
}
MIRROR_ENTRY = {
    "name": "vss-configurator",
    "strategy": "mirror",
    "platforms": ["linux/amd64"],
    "compose_image_names": ["vss-configurator"],
}
EXTERNAL_ENTRY = {
    "name": "sdr-mw-l",
    "strategy": "external-pin",
    "platforms": ["linux/amd64"],
    "compose_image_names": ["sdr-mw-l"],
}

AGENT_COMPOSE = """services:
  vss-agent:
    image: ${VSS_AGENT_IMAGE:-nvcr.io/nvstaging/vss-core/vss-agent}:${VSS_AGENT_TAG:-1.0}
"""
CONFIGURATOR_COMPOSE = """services:
  configurator:
    image: ${VSS_CONFIGURATOR_IMAGE:-nvcr.io/nvidia/vss-core/vss-configurator}:${VSS_CONFIGURATOR_TAG:-3.2.1}
"""


class ClosureTest(unittest.TestCase):
    def run_closure(self, root: Path) -> int:
        args = type("Args", (), {"repo_root": root})()
        return rs.cmd_closure(args)

    def test_all_classified_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {"a/compose.yml": AGENT_COMPOSE, "b/compose.yml": CONFIGURATOR_COMPOSE},
                [AGENT_ENTRY, MIRROR_ENTRY],
            )
            self.assertEqual(self.run_closure(root), 0)

    def test_unclassified_first_party_ref_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {
                    "a/compose.yml": AGENT_COMPOSE
                    + "  rogue:\n    image: nvcr.io/nvidia/vss-core/vss-rogue:1.0\n"
                },
                [AGENT_ENTRY],
            )
            self.assertEqual(self.run_closure(root), 1)

    def test_third_party_refs_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {
                    "a/compose.yml": AGENT_COMPOSE
                    + "  db:\n    image: postgres:16\n"
                    + "  nim:\n    image: nvcr.io/nim/meta/llama:1.0\n"
                },
                [AGENT_ENTRY],
            )
            self.assertEqual(self.run_closure(root), 0)

    def test_alias_compose_name_matches(self):
        entry = dict(AGENT_ENTRY, compose_image_names=["vss-agent", "vss-agent-alias"])
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {
                    "a/compose.yml": "services:\n  x:\n"
                    "    image: nvcr.io/nvidia/vss-core/vss-agent-alias:1.0\n"
                },
                [entry],
            )
            self.assertEqual(self.run_closure(root), 0)


class FragmentTest(unittest.TestCase):
    INVENTORY = {
        "schema_version": 1,
        "first_party_registry_roots": ["nvcr.io/nvidia/vss-core"],
        "images": [AGENT_ENTRY, MIRROR_ENTRY],
    }

    def good_kwargs(self):
        return dict(
            name="vss-agent",
            image="ghcr.io/org/vss-agent",
            tag="develop-abc123def456",
            digest=DIGEST_A,
            platforms=["linux/amd64", "linux/arm64"],
            strategy="build",
            source_tree_sha=TREE_SHA,
        )

    def test_valid_build_fragment(self):
        fragment = rs.build_fragment(self.INVENTORY, **self.good_kwargs())
        self.assertEqual(fragment["name"], "vss-agent")
        self.assertEqual(fragment["source_path"], "services/agent")
        self.assertEqual(fragment["platforms"], ["linux/amd64", "linux/arm64"])
        self.assertEqual(fragment["tag_suffix"], "")

    def test_variant_fragment_uses_shared_repository_and_suffix(self):
        variant = {
            **AGENT_ENTRY,
            "name": "vss-agent-sbsa",
            "repository": "vss-agent",
            "tag_suffix": "-sbsa",
            "platforms": ["linux/arm64"],
        }
        inventory = {**self.INVENTORY, "images": [variant]}
        fragment = rs.build_fragment(
            inventory,
            **{
                **self.good_kwargs(),
                "name": "vss-agent-sbsa",
                "tag": "develop-abc123def456-sbsa",
                "platforms": ["linux/arm64"],
            },
        )
        self.assertEqual(fragment["image"], "ghcr.io/org/vss-agent")
        self.assertEqual(fragment["tag_suffix"], "-sbsa")

    def test_variant_fragment_rejects_wrong_repository_or_unsuffixed_tag(self):
        variant = {
            **AGENT_ENTRY,
            "name": "vss-agent-sbsa",
            "repository": "vss-agent",
            "tag_suffix": "-sbsa",
            "platforms": ["linux/arm64"],
        }
        inventory = {**self.INVENTORY, "images": [variant]}
        with self.assertRaisesRegex(ValueError, "repository"):
            rs.build_fragment(
                inventory,
                **{
                    **self.good_kwargs(),
                    "name": "vss-agent-sbsa",
                    "image": "ghcr.io/org/vss-agent-sbsa",
                    "platforms": ["linux/arm64"],
                },
            )
        with self.assertRaisesRegex(ValueError, "tag .*suffix"):
            rs.build_fragment(
                inventory,
                **{
                    **self.good_kwargs(),
                    "name": "vss-agent-sbsa",
                    "platforms": ["linux/arm64"],
                },
            )

    def test_unknown_name_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown image name"):
            rs.build_fragment(self.INVENTORY, **{**self.good_kwargs(), "name": "nope"})

    def test_malformed_digest_rejected(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            rs.build_fragment(
                self.INVENTORY, **{**self.good_kwargs(), "digest": "sha256:short"}
            )

    def test_missing_required_platform_rejected(self):
        with self.assertRaisesRegex(ValueError, "platforms.*missing"):
            rs.build_fragment(
                self.INVENTORY,
                **{**self.good_kwargs(), "platforms": ["linux/amd64"]},
            )

    def test_build_fragment_requires_tree_sha(self):
        with self.assertRaisesRegex(ValueError, "source_tree_sha"):
            rs.build_fragment(
                self.INVENTORY, **{**self.good_kwargs(), "source_tree_sha": None}
            )

    def test_commit_sha_is_not_a_valid_tree_sha_format_only(self):
        # The gate compares TREE hashes; the fragment can only enforce shape,
        # but the shape check still rejects obviously-wrong values.
        with self.assertRaisesRegex(ValueError, "source_tree_sha"):
            rs.build_fragment(
                self.INVENTORY,
                **{**self.good_kwargs(), "source_tree_sha": "${{ github.sha }}"},
            )

    def test_build_fragment_for_mirror_image_rejected(self):
        with self.assertRaisesRegex(ValueError, "not allowed"):
            rs.build_fragment(
                self.INVENTORY, **{**self.good_kwargs(), "name": "vss-configurator"}
            )

    def test_mirror_fragment_requires_upstream_digest(self):
        kwargs = {
            **self.good_kwargs(),
            "name": "vss-configurator",
            "image": "ghcr.io/org/vss-configurator",
            "strategy": "mirror",
            "source_tree_sha": None,
            "platforms": ["linux/amd64"],
        }
        with self.assertRaisesRegex(ValueError, "upstream_digest"):
            rs.build_fragment(self.INVENTORY, **kwargs)
        fragment = rs.build_fragment(
            self.INVENTORY, **{**kwargs, "upstream_digest": DIGEST_B}
        )
        self.assertEqual(fragment["strategy"], "mirror")

    def test_image_with_tag_rejected(self):
        with self.assertRaisesRegex(ValueError, "without tag"):
            rs.build_fragment(
                self.INVENTORY,
                **{**self.good_kwargs(), "image": "ghcr.io/org/vss-agent:bad"},
            )


class ReuseEntriesTest(unittest.TestCase):
    def test_unbuilt_in_scope_images_are_carried_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {"a/compose.yml": AGENT_COMPOSE, "b/compose.yml": CONFIGURATOR_COMPOSE},
                [AGENT_ENTRY, MIRROR_ENTRY, EXTERNAL_ENTRY],
            )
            inventory = rs.load_inventory(root)
            entries, problems = rs.reuse_entries(root, inventory, {"vss-agent"})
        self.assertEqual(problems, [])
        self.assertEqual(len(entries), 1)  # external-pin is out of scope
        entry = entries[0]
        self.assertEqual(entry["name"], "vss-configurator")
        self.assertEqual(entry["strategy"], "reuse-pinned")
        self.assertEqual(entry["image"], "nvcr.io/nvidia/vss-core/vss-configurator")
        self.assertEqual(entry["tag"], "3.2.1")

    def test_unbuilt_variant_without_compose_ref_uses_its_moving_alias(self):
        variant = {
            **AGENT_ENTRY,
            "name": "vss-agent-sbsa",
            "repository": "vss-agent",
            "tag_suffix": "-sbsa",
            "platforms": ["linux/arm64"],
            "compose_image_names": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {"a/compose.yml": AGENT_COMPOSE},
                [AGENT_ENTRY, variant],
                roots=[
                    "nvcr.io/nvidia/vss-core",
                    "ghcr.io/nvidia-ai-blueprints/vss",
                ],
            )
            inventory = rs.load_inventory(root)
            entries, problems = rs.reuse_entries(root, inventory, {"vss-agent"})
        self.assertEqual(problems, [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "vss-agent-sbsa")
        self.assertEqual(
            entries[0]["image"],
            "ghcr.io/nvidia-ai-blueprints/vss/vss-agent",
        )
        self.assertEqual(entries[0]["tag"], "develop-latest-sbsa")
        self.assertEqual(entries[0]["tag_suffix"], "-sbsa")

    def test_profile_env_resolves_required_pinned_tag(self):
        compose = """services:
  vss-agent:
    image: nvcr.io/nvstaging/vss-core/vss-agent:${VSS_AGENT_VERSION}
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {"services/agent/compose.yml": compose},
                [AGENT_ENTRY],
            )
            profile_env = root / "deploy/docker/developer-profiles/base/.env"
            profile_env.parent.mkdir(parents=True)
            profile_env.write_text("VSS_AGENT_VERSION=3.3.0-deadbeef\n")
            inventory = rs.load_inventory(root)
            entries, problems = rs.reuse_entries(root, inventory, set())
        self.assertEqual(problems, [])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tag"], "3.3.0-deadbeef")

    def test_ambiguous_coordinates_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {
                    "a/compose.yml": CONFIGURATOR_COMPOSE,
                    "b/compose.yml": CONFIGURATOR_COMPOSE.replace("3.2.1", "9.9.9"),
                },
                [MIRROR_ENTRY],
            )
            inventory = rs.load_inventory(root)
            entries, problems = rs.reuse_entries(root, inventory, set())
        self.assertEqual(entries, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("ambiguous", problems[0])

    def test_missing_coordinate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(
                tmp,
                {"a/compose.yml": AGENT_COMPOSE},
                [
                    AGENT_ENTRY,
                    MIRROR_ENTRY,
                ],
            )
            inventory = rs.load_inventory(root)
            entries, problems = rs.reuse_entries(root, inventory, {"vss-agent"})
        self.assertEqual(entries, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("no resolvable compose coordinate", problems[0])


class AssembleAndValidateTest(unittest.TestCase):
    def assemble(self, root: Path, fragments_dir: Path | None) -> tuple[int, Path]:
        out = root / "release-set.json"
        args = type(
            "Args",
            (),
            {
                "repo_root": root,
                "fragments": fragments_dir,
                "repository": "org/repo",
                "commit": COMMIT_SHA,
                "ref": "develop",
                "workflow_run": "https://example/run/1",
                "out": out,
            },
        )()
        return rs.cmd_assemble(args), out

    def make_full_repo(self, tmp: str) -> Path:
        return make_repo(
            tmp,
            {"a/compose.yml": AGENT_COMPOSE, "b/compose.yml": CONFIGURATOR_COMPOSE},
            [AGENT_ENTRY, MIRROR_ENTRY, EXTERNAL_ENTRY],
        )

    def write_agent_fragment(self, root: Path) -> Path:
        inventory = rs.load_inventory(root)
        fragment = rs.build_fragment(
            inventory,
            name="vss-agent",
            image="ghcr.io/org/vss-agent",
            tag="develop-abc123def456",
            digest=DIGEST_A,
            platforms=["linux/amd64", "linux/arm64"],
            strategy="build",
            source_tree_sha=TREE_SHA,
        )
        fragments_dir = root / "fragments"
        fragments_dir.mkdir()
        (fragments_dir / "vss-agent.json").write_text(json.dumps(fragment))
        return fragments_dir

    def test_assemble_produces_valid_complete_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_full_repo(tmp)
            fragments_dir = self.write_agent_fragment(root)
            code, out = self.assemble(root, fragments_dir)
            self.assertEqual(code, 0)
            release_set = json.loads(out.read_text())
            inventory = rs.load_inventory(root)
            self.assertEqual(rs.validate_release_set(release_set, inventory), [])
            names = {item["name"]: item for item in release_set["images"]}
            self.assertEqual(
                set(names), {"vss-agent", "vss-configurator"}
            )  # external-pin excluded
            self.assertEqual(names["vss-agent"]["strategy"], "build")
            self.assertEqual(names["vss-configurator"]["strategy"], "reuse-pinned")

    def test_release_set_id_is_deterministic_and_tamper_evident(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_full_repo(tmp)
            fragments_dir = self.write_agent_fragment(root)
            _, out = self.assemble(root, fragments_dir)
            release_set = json.loads(out.read_text())
            recomputed = rs.compute_release_set_id(release_set)
            self.assertEqual(release_set["release_set_id"], recomputed)
            inventory = rs.load_inventory(root)
            tampered = json.loads(out.read_text())
            tampered["images"][0]["tag"] = "something-else"
            errors = rs.validate_release_set(tampered, inventory)
            self.assertTrue(any("release_set_id mismatch" in e for e in errors))

    def test_validate_rejects_missing_in_scope_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_full_repo(tmp)
            fragments_dir = self.write_agent_fragment(root)
            _, out = self.assemble(root, fragments_dir)
            release_set = json.loads(out.read_text())
            release_set["images"] = [
                item
                for item in release_set["images"]
                if item["name"] != "vss-configurator"
            ]
            release_set["release_set_id"] = rs.compute_release_set_id(release_set)
            inventory = rs.load_inventory(root)
            errors = rs.validate_release_set(release_set, inventory)
            self.assertTrue(any("incomplete set" in e for e in errors))

    def test_validate_rejects_build_entry_without_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_full_repo(tmp)
            fragments_dir = self.write_agent_fragment(root)
            _, out = self.assemble(root, fragments_dir)
            release_set = json.loads(out.read_text())
            for item in release_set["images"]:
                if item["name"] == "vss-agent":
                    item["digest"] = None
            release_set["release_set_id"] = rs.compute_release_set_id(release_set)
            inventory = rs.load_inventory(root)
            errors = rs.validate_release_set(release_set, inventory)
            self.assertTrue(any("immutable sha256 digest" in e for e in errors))

    def test_validate_rejects_unresolved_reuse_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_full_repo(tmp)
            fragments_dir = self.write_agent_fragment(root)
            _, out = self.assemble(root, fragments_dir)
            release_set = json.loads(out.read_text())
            for item in release_set["images"]:
                if item["strategy"] == "reuse-pinned":
                    item["tag"] = "${VSS_AGENT_VERSION}"
            release_set["release_set_id"] = rs.compute_release_set_id(release_set)
            inventory = rs.load_inventory(root)
            errors = rs.validate_release_set(release_set, inventory)
            self.assertTrue(any("unresolved variable" in e for e in errors))

    def test_validate_rejects_duplicates_and_bad_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_full_repo(tmp)
            fragments_dir = self.write_agent_fragment(root)
            _, out = self.assemble(root, fragments_dir)
            release_set = json.loads(out.read_text())
            release_set["images"].append(dict(release_set["images"][0]))
            release_set["source"]["commit"] = "not-a-sha"
            release_set["release_set_id"] = rs.compute_release_set_id(release_set)
            inventory = rs.load_inventory(root)
            errors = " | ".join(rs.validate_release_set(release_set, inventory))
            self.assertIn("duplicate entry", errors)
            self.assertIn("source.commit", errors)


class SplitRefTest(unittest.TestCase):
    def test_plain_tag(self):
        self.assertEqual(
            rs._split_ref("nvcr.io/nvidia/vss-core/img:3.2.1"),
            ("nvcr.io/nvidia/vss-core/img", "3.2.1"),
        )

    def test_unresolved_tag_variable_is_preserved(self):
        self.assertEqual(
            rs._split_ref("nvcr.io/nvidia/vss-core/img:${TAG}"),
            ("nvcr.io/nvidia/vss-core/img", "${TAG}"),
        )

    def test_no_tag(self):
        self.assertEqual(
            rs._split_ref("nvcr.io/nvidia/vss-core/img"),
            ("nvcr.io/nvidia/vss-core/img", ""),
        )


class RealRepositoryTest(unittest.TestCase):
    """The actual repository must satisfy its own closure invariant."""

    def test_repo_closure_passes(self):
        repo_root = Path(__file__).resolve().parents[2]
        args = type("Args", (), {"repo_root": repo_root})()
        self.assertEqual(rs.cmd_closure(args), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
