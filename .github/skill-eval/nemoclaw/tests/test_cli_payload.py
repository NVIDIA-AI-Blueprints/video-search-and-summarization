# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load_module(
    "nemoclaw_prepare_cli_payload",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "prepare_cli_payload.py",
)
install = load_module(
    "nemoclaw_install_cli_payload",
    REPO_ROOT / ".github" / "skill-eval" / "nemoclaw" / "install_cli_payload.py",
)


class CoordinatorPayloadTest(unittest.TestCase):
    def test_release_and_runtime_assets_are_immutable_pins(self):
        version, revision = prepare.RELEASES["v0.0.103"]
        self.assertEqual(version, "0.0.103")
        self.assertEqual(revision, "db31c286129e878c3356eed49f76ab259561e47e")
        self.assertEqual(prepare.SCHEMA_VERSION, 3)
        self.assertEqual(prepare.NODE_VERSION, "22.22.1")
        self.assertEqual(
            prepare.NODE_ASSET_SHA256,
            "9a6bc82f9b491279147219f6a18add1e18424dce90d41d2a5fcd69d4924ba3aa",
        )
        self.assertEqual(
            prepare.OPENSHELL_ASSETS,
            {
                "openshell-x86_64-unknown-linux-musl.tar.gz": (
                    "openshell",
                    "078fa086f506832c3d47d992e6109f26074bdd55916ce268e47c3971423459eb",
                ),
                "openshell-gateway-x86_64-unknown-linux-gnu.tar.gz": (
                    "openshell-gateway",
                    "718cc9f942f88565cacb13c39717b128d6acc8d336212d42d26243f36ab19ece",
                ),
                "openshell-sandbox-x86_64-unknown-linux-gnu.tar.gz": (
                    "openshell-sandbox",
                    "94306f057d862cd5c34a0daa7692491733bc5ca528a7b92f9f62f717fb70a9be",
                ),
            },
        )

    def test_cache_key_is_schema_scoped_and_reuses_verified_archive(self):
        calls = []
        digest = hashlib.sha256(b"trusted-payload").hexdigest()

        def fake_build(**kwargs):
            calls.append(kwargs)
            kwargs["archive"].write_bytes(b"trusted-payload")
            return digest

        validation_results = [None, digest]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_one = root / "one.tar.gz"
            output_two = root / "two.tar.gz"
            with (
                mock.patch.object(prepare, "_build_archive", side_effect=fake_build),
                mock.patch.object(
                    prepare,
                    "_validate_cached_archive",
                    side_effect=validation_results,
                ),
            ):
                first = prepare.prepare_payload(
                    ref="v0.0.103",
                    output=output_one,
                    cache_root=root / "cache",
                )
                second = prepare.prepare_payload(
                    ref="v0.0.103",
                    output=output_two,
                    cache_root=root / "cache",
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(first[1:], second[1:])
            self.assertEqual(output_one.read_bytes(), b"trusted-payload")
            self.assertEqual(output_two.read_bytes(), b"trusted-payload")
            cached_names = {path.name for path in (root / "cache").iterdir()}
            self.assertTrue(
                any(
                    name.startswith("nemoclaw-cli-schema3-0.0.103-")
                    for name in cached_names
                )
            )

    def test_archive_modes_do_not_depend_on_coordinator_umask(self):
        directory = tarfile.TarInfo("source/bin")
        directory.type = tarfile.DIRTYPE
        directory.mode = 0o700
        executable = tarfile.TarInfo("source/bin/nemoclaw.js")
        executable.mode = 0o700
        regular = tarfile.TarInfo("source/package.json")
        regular.mode = 0o600

        self.assertEqual(prepare._archive_filter(directory).mode, 0o755)
        self.assertEqual(prepare._archive_filter(executable).mode, 0o755)
        self.assertEqual(prepare._archive_filter(regular).mode, 0o644)


class WorkerPayloadInstallTest(unittest.TestCase):
    VERSION = "0.0.103"
    REF = "v0.0.103"
    REVISION = "db31c286129e878c3356eed49f76ab259561e47e"

    @staticmethod
    def _single_member_archive(path: Path, member: str, content: bytes, mode: str) -> None:
        with tarfile.open(path, mode) as output:
            info = tarfile.TarInfo(member)
            info.mode = 0o755
            info.size = len(content)
            output.addfile(info, io.BytesIO(content))

    def _payload(self, root: Path) -> tuple[Path, str, dict[str, object]]:
        source = root / "build" / "source"
        required = (
            source / "bin" / "nemoclaw.js",
            source / "bin" / "nemohermes.js",
            source / "dist" / "lib" / "cli" / "logger.js",
            source / "dist" / "lib" / "onboard" / "preflight.js",
            source / "dist" / "lib" / "tunnel" / "gateway-port-release.js",
            source / "nemoclaw" / "dist" / "index.js",
        )
        for path in required:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// payload\n", encoding="utf-8")
        (source / "package.json").write_text(
            json.dumps({"name": "nemoclaw"}) + "\n",
            encoding="utf-8",
        )
        (source / "dist" / "build-identity.json").write_text(
            json.dumps(
                {
                    "nemoclawVersion": self.VERSION,
                    "sourceRevision": self.REVISION,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assets = source / install.RUNTIME_ASSET_DIRECTORY
        assets.mkdir()
        node_content = (
            b"#!/bin/sh\n"
            b"printf '%s\\n' \"$*\" >> \"${FAKE_NODE_LOG:-/dev/null}\"\n"
            b"if [ \"${1:-}\" = --version ]; then printf 'v22.22.1\\n'; exit 0; fi\n"
            b"if [ \"${2:-}\" = --version ]; then printf 'nemoclaw v0.0.103\\n'; exit 0; fi\n"
            b"exit 64\n"
        )
        node_archive = assets / install.NODE_ASSET_NAME
        self._single_member_archive(
            node_archive,
            install.NODE_ASSET_MEMBER,
            node_content,
            "w:xz",
        )
        openshell: dict[str, tuple[str, str, str]] = {}
        for asset_name, member in (
            ("openshell-x86_64-unknown-linux-musl.tar.gz", "openshell"),
            (
                "openshell-gateway-x86_64-unknown-linux-gnu.tar.gz",
                "openshell-gateway",
            ),
            (
                "openshell-sandbox-x86_64-unknown-linux-gnu.tar.gz",
                "openshell-sandbox",
            ),
        ):
            content = (
                "#!/bin/sh\n"
                f"printf '{member} 0.0.85\\n'\n"
            ).encode()
            asset = assets / asset_name
            self._single_member_archive(asset, member, content, "w:gz")
            openshell[asset_name] = (
                member,
                hashlib.sha256(asset.read_bytes()).hexdigest(),
                hashlib.sha256(content).hexdigest(),
            )
        pins: dict[str, object] = {
            "NODE_ASSET_SHA256": hashlib.sha256(node_archive.read_bytes()).hexdigest(),
            "NODE_BINARY_SHA256": hashlib.sha256(node_content).hexdigest(),
            "OPENSHELL_ASSETS": openshell,
        }
        with mock.patch.multiple(install, **pins):
            manifest = {
                "build": {
                    "kind": "nemoclaw-source",
                    "repository": install.SOURCE_URL,
                    "revision": self.REVISION,
                },
                "payload_kind": install.PAYLOAD_KIND,
                "ref": self.REF,
                "revision": self.REVISION,
                "schema_version": install.SCHEMA_VERSION,
                "version": self.VERSION,
            }
            manifest.update(install._runtime_manifest())
        (source / ".skill-eval-payload.json").write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        archive = root / "payload.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            output.add(source, arcname="source")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        return archive, digest, pins

    @staticmethod
    def _seed_previous_runtime(home: Path) -> dict[Path, bytes]:
        previous: dict[Path, bytes] = {}
        source = home / ".nemoclaw" / "source"
        source.mkdir(parents=True)
        marker = source / "partial.txt"
        marker.write_bytes(b"old source\n")
        previous[marker] = marker.read_bytes()
        old_node = home / ".nemoclaw" / "runtime" / install.NODE_RUNTIME_NAME / "old"
        old_node.parent.mkdir(parents=True)
        old_node.write_bytes(b"old node\n")
        previous[old_node] = old_node.read_bytes()
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        for name in (*install.CLI_SCRIPTS, "openshell", "openshell-gateway", "openshell-sandbox"):
            path = bin_dir / name
            path.write_bytes(f"old {name}\n".encode())
            previous[path] = path.read_bytes()
        return previous

    def test_installs_bundled_node_wrappers_and_openshell_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            self._seed_previous_runtime(home)
            (home / ".nemoclaw").chmod(0o755)
            archive, digest, pins = self._payload(root)
            node_log = root / "node.log"
            with (
                mock.patch.multiple(install, **pins),
                mock.patch.dict(
                    os.environ,
                    {"PATH": "/usr/bin:/bin", "FAKE_NODE_LOG": str(node_log)},
                ),
            ):
                destination = install.install_payload(
                    archive=archive,
                    sha256=digest,
                    ref=self.REF,
                    version=self.VERSION,
                    revision=self.REVISION,
                    home=home,
                )

            node = home / ".nemoclaw" / "runtime" / install.NODE_RUNTIME_NAME / "bin" / "node"
            self.assertEqual(destination, home / ".nemoclaw" / "source")
            self.assertFalse((destination / "partial.txt").exists())
            self.assertTrue(node.is_file())
            self.assertEqual((home / ".nemoclaw").stat().st_mode & 0o777, 0o700)
            for name in install.CLI_SCRIPTS:
                launcher = home / ".local" / "bin" / name
                self.assertTrue(launcher.is_file())
                self.assertFalse(launcher.is_symlink())
                body = launcher.read_text(encoding="utf-8")
                self.assertIn(f"exec {node}", body)
                self.assertNotIn("/usr/bin/env node", body)
                self.assertIn("OPENSHELL_DOCKER_SUPERVISOR_BIN", body)
            for _, (member, _, binary_sha) in pins["OPENSHELL_ASSETS"].items():
                binary = home / ".local" / "bin" / member
                self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), binary_sha)
                self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
            invocations = node_log.read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(len(invocations), 4)
            self.assertTrue(all("curl" not in line and "npm" not in line for line in invocations))

    def test_transaction_failure_restores_source_node_launchers_and_openshell(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            previous = self._seed_previous_runtime(home)
            archive, digest, pins = self._payload(root)
            with (
                mock.patch.multiple(install, **pins),
                mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}),
                mock.patch.object(
                    install,
                    "_validate_installed_runtime",
                    side_effect=install.PayloadInstallError("forced_verify_failure"),
                ),
                self.assertRaisesRegex(install.PayloadInstallError, "forced_verify_failure"),
            ):
                install.install_payload(
                    archive=archive,
                    sha256=digest,
                    ref=self.REF,
                    version=self.VERSION,
                    revision=self.REVISION,
                    home=home,
                )

            for path, content in previous.items():
                self.assertEqual(path.read_bytes(), content, path)
            installed_node = (
                home
                / ".nemoclaw"
                / "runtime"
                / install.NODE_RUNTIME_NAME
                / "bin"
                / "node"
            )
            self.assertFalse(installed_node.exists())

    def test_checksum_mismatch_preserves_partial_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            previous = self._seed_previous_runtime(home)
            archive, _, _ = self._payload(root)
            with self.assertRaisesRegex(install.PayloadInstallError, "payload_checksum_mismatch"):
                install.install_payload(
                    archive=archive,
                    sha256="0" * 64,
                    ref=self.REF,
                    version=self.VERSION,
                    revision=self.REVISION,
                    home=home,
                )
            for path, content in previous.items():
                self.assertEqual(path.read_bytes(), content, path)

    def test_symlink_escape_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            home.mkdir()
            archive = root / "escape.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                source = tarfile.TarInfo("source")
                source.type = tarfile.DIRTYPE
                source.mode = 0o755
                output.addfile(source)
                escape = tarfile.TarInfo("source/escape")
                escape.type = tarfile.SYMTYPE
                escape.linkname = "../../outside"
                output.addfile(escape)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaisesRegex(install.PayloadInstallError, "payload_symlink_unsafe"):
                install.install_payload(
                    archive=archive,
                    sha256=digest,
                    ref=self.REF,
                    version=self.VERSION,
                    revision=self.REVISION,
                    home=home,
                )
            self.assertFalse((root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
