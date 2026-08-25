# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic client-layer tests for pinned Hugging Face acquisition."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import urllib.parse
from contextlib import contextmanager, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REVISION = "1" * 40
ROOT = Path(__file__).resolve().parents[3]
DOWNLOADER_PATHS = (
    ROOT / "services/rtvi/rt-embed/src/vlm_pipeline/ngc_model_downloader.py",
    ROOT / "services/rtvi/rt-vlm/src/vlm_pipeline/ngc_model_downloader.py",
)
SINGLE_FILE_SCRIPT = ROOT / "deploy/docker/scripts/download_hf_file.py"
EXEC_SCRIPT = ROOT / "deploy/docker/scripts/exec_with_hf_hub.py"
HF_AUTH_ENV_VARS = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGINGFACE_TOKEN",
    "HF_TOKEN_PATH",
)
HF_HTTP_APPROVAL_ENV = "HF_HUB_APPROVED_HTTP_ORIGINS"
SECRET_SENTINEL = "synthetic-secret-must-not-appear"


class _Logger:
    def info(self, *_args: object, **_kwargs: object) -> None:
        pass


def load_downloader(path: Path, index: int) -> types.ModuleType:
    common = types.ModuleType("common")
    logger_module = types.ModuleType("common.logger")
    logger_module.logger = _Logger()
    sys.modules.setdefault("common", common)
    sys.modules["common.logger"] = logger_module
    spec = importlib.util.spec_from_file_location(f"hf_downloader_{index}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeHub(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), FakeHubHandler)
        self.files = {
            "config.json": b'{"model": "fake"}\n',
            "nested/weights.bin": b"deterministic-model-bytes",
        }
        self.requests: list[tuple[str, str, str]] = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class FakeHubHandler(BaseHTTPRequestHandler):
    server: FakeHub

    def log_message(self, *_args: object) -> None:
        pass

    def _record(self) -> None:
        self.server.requests.append(
            (self.command, self.path, self.headers.get("Authorization", ""))
        )

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        split = urllib.parse.urlsplit(self.path)
        if split.path == f"/api/models/owner/repo/revision/{REVISION}":
            siblings = []
            for filename, body in self.server.files.items():
                siblings.append(
                    {
                        "rfilename": filename,
                        "size": len(body),
                        "lfs": {
                            "sha256": hashlib.sha256(body).hexdigest(),
                            "size": len(body),
                            "pointerSize": 128,
                        },
                    }
                )
            self._json(
                {
                    "id": "owner/repo",
                    "modelId": "owner/repo",
                    "sha": REVISION,
                    "private": False,
                    "siblings": siblings,
                }
            )
            return
        filename = self._resolve_filename(split.path)
        if filename is None:
            self.send_error(404)
            return
        body = self.server.files[filename]
        self.send_response(200)
        self._file_headers(body)
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()
        filename = self._resolve_filename(urllib.parse.urlsplit(self.path).path)
        if filename is None:
            self.send_error(404)
            return
        body = self.server.files[filename]
        self.send_response(200)
        self._file_headers(body)
        self.end_headers()

    def _resolve_filename(self, path: str) -> str | None:
        prefix = f"/owner/repo/resolve/{REVISION}/"
        if not path.startswith(prefix):
            return None
        filename = urllib.parse.unquote(path.removeprefix(prefix))
        return filename if filename in self.server.files else None

    def _file_headers(self, body: bytes) -> None:
        self.send_header("X-Repo-Commit", REVISION)
        self.send_header("ETag", hashlib.sha256(body).hexdigest())
        self.send_header("Content-Length", str(len(body)))

    def _json(self, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def environment(**updates: str | None):
    previous = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class HuggingFaceModelAcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        import huggingface_hub

        if huggingface_hub.__version__ != "0.36.2":
            raise unittest.SkipTest("tests require huggingface_hub==0.36.2")
        cls.downloaders = tuple(
            load_downloader(path, index) for index, path in enumerate(DOWNLOADER_PATHS)
        )

    def setUp(self) -> None:
        self.hub = FakeHub()

    def tearDown(self) -> None:
        self.hub.stop()

    def test_snapshot_honors_endpoint_revision_layout_auth_and_warm_destination(
        self,
    ) -> None:
        for downloader in self.downloaders:
            with (
                self.subTest(module=downloader.__file__),
                tempfile.TemporaryDirectory() as root,
                tempfile.TemporaryDirectory() as hf_home,
            ):
                self.hub.requests.clear()
                with environment(
                    HF_ENDPOINT=self.hub.endpoint,
                    HF_HOME=hf_home,
                    HF_TOKEN=None,
                    HUGGING_FACE_HUB_TOKEN=None,
                    HUGGINGFACE_TOKEN=None,
                    HF_TOKEN_PATH=None,
                    HF_HUB_APPROVED_HTTP_ORIGINS=self.hub.endpoint,
                    HF_HUB_DISABLE_XET="1",
                    HF_HUB_DISABLE_IMPLICIT_TOKEN=None,
                ):
                    model_dir = Path(
                        downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                    )
                    self.assertEqual(os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"], "1")
                    first_request_count = len(self.hub.requests)
                    with environment(HF_HUB_OFFLINE="1"):
                        self.assertEqual(
                            downloader.download_model_hf(
                                f"owner/repo@{REVISION}", root
                            ),
                            str(model_dir),
                        )
                self.assertGreater(first_request_count, 0)
                self.assertEqual(len(self.hub.requests), first_request_count)
                self.assertEqual(
                    (model_dir / "config.json").read_bytes(),
                    self.hub.files["config.json"],
                )
                self.assertEqual(
                    (model_dir / "nested/weights.bin").read_bytes(),
                    self.hub.files["nested/weights.bin"],
                )
                self.assertEqual(
                    (model_dir / ".hf-revision").read_text().strip(), REVISION
                )
                self.assertEqual(model_dir.stat().st_mode & 0o777, 0o755)
                self.assertFalse((model_dir / ".cache").exists())
                self.assertFalse(
                    any(path.is_symlink() for path in model_dir.rglob("*"))
                )
                self.assertTrue(
                    all(
                        f"/{REVISION}" in path or f"/{REVISION}/" in path
                        for _, path, _ in self.hub.requests
                    )
                )
                self.assertTrue(all(not auth for _, _, auth in self.hub.requests))
                self.assertEqual(os.environ["HF_HUB_DISABLE_XET"], "1")

    def test_missing_revision_and_unverifiable_warm_directory_fail_closed(self) -> None:
        for downloader in self.downloaders:
            with (
                self.subTest(module=downloader.__file__),
                tempfile.TemporaryDirectory() as root,
            ):
                with self.assertRaisesRegex(ValueError, "immutable commit"):
                    downloader.download_model_hf("owner/repo", root)
                Path(root, "repo").mkdir()
                with self.assertRaisesRegex(
                    RuntimeError, "no verifiable immutable revision"
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                self.assertEqual(self.hub.requests, [])

    def test_unsupported_endpoint_and_client_fail_before_network(self) -> None:
        for downloader in self.downloaders:
            with (
                self.subTest(module=downloader.__file__),
                tempfile.TemporaryDirectory() as root,
            ):
                with (
                    environment(
                        HF_ENDPOINT="ftp://cache.invalid",
                        HF_TOKEN=None,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                    ),
                    self.assertRaisesRegex(ValueError, "HTTP"),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                with (
                    environment(
                        HF_ENDPOINT="https://cache.invalid/unsupported",
                        HF_TOKEN=None,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                    ),
                    self.assertRaisesRegex(ValueError, "origin"),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                with (
                    environment(
                        HF_ENDPOINT=self.hub.endpoint,
                        HF_TOKEN=None,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                    ),
                    self.assertRaisesRegex(ValueError, "approved"),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                with (
                    environment(
                        HF_ENDPOINT="http://8.8.8.8",
                        HF_TOKEN=None,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                        HF_HUB_APPROVED_HTTP_ORIGINS="http://8.8.8.8",
                    ),
                    self.assertRaisesRegex(ValueError, "approved"),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                with (
                    environment(
                        HF_ENDPOINT=self.hub.endpoint,
                        HF_TOKEN=None,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                        HF_HUB_APPROVED_HTTP_ORIGINS=self.hub.endpoint,
                    ),
                    mock.patch.object(downloader, "version", return_value="0.35.0"),
                    self.assertRaisesRegex(RuntimeError, "expected 0.36.2"),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                self.assertEqual(self.hub.requests, [])

    def test_approved_http_denies_every_auth_env_before_network(self) -> None:
        for downloader in self.downloaders:
            for auth_name in HF_AUTH_ENV_VARS:
                with (
                    self.subTest(module=downloader.__file__, auth=auth_name),
                    tempfile.TemporaryDirectory() as root,
                    tempfile.TemporaryDirectory() as hf_home,
                ):
                    updates = {name: None for name in HF_AUTH_ENV_VARS} | {
                        "HF_ENDPOINT": self.hub.endpoint,
                        "HF_HOME": hf_home,
                        HF_HTTP_APPROVAL_ENV: self.hub.endpoint,
                        auth_name: SECRET_SENTINEL,
                    }
                    with environment(**updates):
                        with self.assertRaisesRegex(
                            ValueError, "authentication is not permitted"
                        ) as raised:
                            downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                    self.assertNotIn(SECRET_SENTINEL, str(raised.exception))
                    self.assertEqual(self.hub.requests, [])

    def test_approved_http_denies_token_config_before_network(self) -> None:
        for downloader in self.downloaders:
            for filename in ("token", "stored_tokens"):
                with (
                    self.subTest(module=downloader.__file__, config=filename),
                    tempfile.TemporaryDirectory() as root,
                    tempfile.TemporaryDirectory() as hf_home,
                ):
                    Path(hf_home, filename).touch()
                    with (
                        environment(
                            HF_ENDPOINT=self.hub.endpoint,
                            HF_HOME=hf_home,
                            HF_TOKEN=None,
                            HUGGING_FACE_HUB_TOKEN=None,
                            HUGGINGFACE_TOKEN=None,
                            HF_TOKEN_PATH=None,
                            HF_HUB_APPROVED_HTTP_ORIGINS=self.hub.endpoint,
                        ),
                        self.assertRaisesRegex(
                            ValueError, "authentication is not permitted"
                        ),
                    ):
                        downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                    self.assertEqual(self.hub.requests, [])

    def test_approved_http_with_auth_denies_warm_private_model_reuse(self) -> None:
        for downloader in self.downloaders:
            with (
                self.subTest(module=downloader.__file__),
                tempfile.TemporaryDirectory() as root,
                tempfile.TemporaryDirectory() as hf_home,
            ):
                model_dir = Path(root, "repo")
                model_dir.mkdir()
                Path(model_dir, ".hf-revision").write_text(f"{REVISION}\n")
                with (
                    environment(
                        HF_ENDPOINT=self.hub.endpoint,
                        HF_HOME=hf_home,
                        HF_TOKEN=SECRET_SENTINEL,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                        HF_HUB_APPROVED_HTTP_ORIGINS=self.hub.endpoint,
                    ),
                    self.assertRaisesRegex(
                        ValueError, "authentication is not permitted"
                    ),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                self.assertEqual(self.hub.requests, [])

    def test_userinfo_and_path_endpoints_fail_without_secret_disclosure(self) -> None:
        endpoints = (
            f"http://user:{SECRET_SENTINEL}@127.0.0.1:{self.hub.server_port}",
            f"{self.hub.endpoint}/unsupported",
        )
        for downloader in self.downloaders:
            for endpoint in endpoints:
                with (
                    self.subTest(module=downloader.__file__, endpoint=endpoint),
                    tempfile.TemporaryDirectory() as root,
                    environment(
                        HF_ENDPOINT=endpoint,
                        HF_TOKEN=None,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                        HF_HUB_APPROVED_HTTP_ORIGINS=endpoint,
                    ),
                ):
                    with self.assertRaisesRegex(ValueError, "origin") as raised:
                        downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                    self.assertNotIn(SECRET_SENTINEL, str(raised.exception))
                    self.assertEqual(self.hub.requests, [])

    def test_official_endpoint_direct_mode_is_forwarded_without_rewriting(self) -> None:
        for downloader in self.downloaders:
            with (
                self.subTest(module=downloader.__file__),
                tempfile.TemporaryDirectory() as root,
            ):
                captured: dict[str, object] = {}

                def fake_snapshot_download(**kwargs: object) -> str:
                    captured.update(kwargs)
                    Path(str(kwargs["local_dir"]), "config.json").write_text("{}\n")
                    return str(kwargs["local_dir"])

                with (
                    environment(
                        HF_ENDPOINT="https://huggingface.co",
                        HF_TOKEN=SECRET_SENTINEL,
                        HUGGING_FACE_HUB_TOKEN=None,
                        HUGGINGFACE_TOKEN=None,
                        HF_TOKEN_PATH=None,
                        HF_HUB_DISABLE_XET="1",
                    ),
                    mock.patch(
                        "huggingface_hub.snapshot_download", fake_snapshot_download
                    ),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                self.assertEqual(captured["endpoint"], "https://huggingface.co")
                self.assertEqual(captured["revision"], REVISION)
                self.assertEqual(captured["token"], SECRET_SENTINEL)
                self.assertNotEqual(captured["cache_dir"], os.environ.get("HF_HOME"))
                self.assertFalse(Path(str(captured["cache_dir"])).exists())

    def test_https_accepts_each_supported_token_environment_shape(self) -> None:
        for downloader in self.downloaders:
            for auth_name in HF_AUTH_ENV_VARS[:3]:
                with (
                    self.subTest(module=downloader.__file__, auth=auth_name),
                    tempfile.TemporaryDirectory() as root,
                ):
                    captured: dict[str, object] = {}

                    def fake_snapshot_download(**kwargs: object) -> str:
                        captured.update(kwargs)
                        Path(str(kwargs["local_dir"]), "config.json").write_text("{}\n")
                        return str(kwargs["local_dir"])

                    updates = {name: None for name in HF_AUTH_ENV_VARS} | {
                        "HF_ENDPOINT": "https://huggingface.co",
                        auth_name: SECRET_SENTINEL,
                    }
                    with (
                        environment(**updates),
                        mock.patch(
                            "huggingface_hub.snapshot_download",
                            fake_snapshot_download,
                        ),
                    ):
                        downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                    self.assertEqual(captured["token"], SECRET_SENTINEL)
                    self.assertEqual(captured["endpoint"], "https://huggingface.co")

    def test_empty_compose_endpoint_restores_official_default_before_import(
        self,
    ) -> None:
        for downloader in self.downloaders:
            with (
                self.subTest(module=downloader.__file__),
                tempfile.TemporaryDirectory() as root,
            ):
                captured: dict[str, object] = {}

                def fake_snapshot_download(**kwargs: object) -> str:
                    captured.update(kwargs)
                    Path(str(kwargs["local_dir"]), "config.json").write_text("{}\n")
                    return str(kwargs["local_dir"])

                with (
                    environment(HF_ENDPOINT="", HF_HUB_DISABLE_XET="1"),
                    mock.patch(
                        "huggingface_hub.snapshot_download", fake_snapshot_download
                    ),
                ):
                    downloader.download_model_hf(f"owner/repo@{REVISION}", root)
                    self.assertNotIn("HF_ENDPOINT", os.environ)
                self.assertIsNone(captured["endpoint"])

    def test_single_file_helper_uses_hf_hub_download_with_exact_revision(self) -> None:
        helper = load_module(SINGLE_FILE_SCRIPT, "download_hf_file_test")
        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as hf_home,
            environment(
                HF_ENDPOINT=self.hub.endpoint,
                HF_TOKEN=None,
                HUGGING_FACE_HUB_TOKEN=None,
                HUGGINGFACE_TOKEN=None,
                HF_TOKEN_PATH=None,
                HF_HOME=hf_home,
                HF_HUB_APPROVED_HTTP_ORIGINS=self.hub.endpoint,
                HF_HUB_DISABLE_XET="1",
                HF_HUB_DISABLE_IMPLICIT_TOKEN=None,
            ),
            mock.patch.object(
                sys,
                "argv",
                [
                    str(SINGLE_FILE_SCRIPT),
                    "--repo-id",
                    "owner/repo",
                    "--revision",
                    REVISION,
                    "--filename",
                    "config.json",
                    "--local-dir",
                    root,
                ],
            ),
        ):
            self.assertEqual(helper.main(), 0)
            self.assertEqual(os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"], "1")
            self.assertEqual(
                Path(root, "config.json").read_bytes(), self.hub.files["config.json"]
            )
        self.assertTrue(
            any(
                f"/{REVISION}/config.json" in path and not auth
                for _, path, auth in self.hub.requests
            )
        )

    def test_single_file_helper_denies_http_auth_before_network(self) -> None:
        helper = load_module(SINGLE_FILE_SCRIPT, "download_hf_file_denial_test")
        for auth_name in HF_AUTH_ENV_VARS:
            with (
                self.subTest(auth=auth_name),
                tempfile.TemporaryDirectory() as root,
                tempfile.TemporaryDirectory() as hf_home,
            ):
                updates = {name: None for name in HF_AUTH_ENV_VARS} | {
                    "HF_ENDPOINT": self.hub.endpoint,
                    "HF_HOME": hf_home,
                    HF_HTTP_APPROVAL_ENV: self.hub.endpoint,
                    auth_name: SECRET_SENTINEL,
                }
                stderr = io.StringIO()
                with (
                    environment(**updates),
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            str(SINGLE_FILE_SCRIPT),
                            "--repo-id",
                            "owner/repo",
                            "--revision",
                            REVISION,
                            "--filename",
                            "config.json",
                            "--local-dir",
                            root,
                        ],
                    ),
                    redirect_stderr(stderr),
                    self.assertRaises(SystemExit),
                ):
                    helper.main()
                self.assertNotIn(SECRET_SENTINEL, stderr.getvalue())
                self.assertEqual(self.hub.requests, [])

    def test_single_file_helper_accepts_https_token_shapes(self) -> None:
        helper = load_module(SINGLE_FILE_SCRIPT, "download_hf_file_https_test")
        for auth_name in HF_AUTH_ENV_VARS[:3]:
            with (
                self.subTest(auth=auth_name),
                tempfile.TemporaryDirectory() as root,
                tempfile.TemporaryDirectory() as hf_home,
            ):
                captured: dict[str, object] = {}

                def fake_hf_hub_download(**kwargs: object) -> str:
                    captured.update(kwargs)
                    destination = Path(
                        str(kwargs["local_dir"]), str(kwargs["filename"])
                    )
                    destination.write_text("{}\n")
                    return str(destination)

                updates = {name: None for name in HF_AUTH_ENV_VARS} | {
                    "HF_ENDPOINT": "https://huggingface.co",
                    "HF_HOME": hf_home,
                    auth_name: SECRET_SENTINEL,
                }
                with (
                    environment(**updates),
                    mock.patch(
                        "huggingface_hub.hf_hub_download",
                        fake_hf_hub_download,
                    ),
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            str(SINGLE_FILE_SCRIPT),
                            "--repo-id",
                            "owner/repo",
                            "--revision",
                            REVISION,
                            "--filename",
                            "config.json",
                            "--local-dir",
                            root,
                        ],
                    ),
                ):
                    self.assertEqual(helper.main(), 0)
                self.assertEqual(captured["token"], SECRET_SENTINEL)

    def test_single_file_helper_clears_empty_compose_endpoint(self) -> None:
        helper = load_module(SINGLE_FILE_SCRIPT, "download_hf_file_empty_test")
        captured: dict[str, object] = {}

        def fake_hf_hub_download(**kwargs: object) -> str:
            captured.update(kwargs)
            destination = Path(str(kwargs["local_dir"]), str(kwargs["filename"]))
            destination.write_text("{}\n")
            return str(destination)

        with (
            tempfile.TemporaryDirectory() as root,
            tempfile.TemporaryDirectory() as hf_home,
            environment(
                HF_ENDPOINT="",
                HF_HOME=hf_home,
                HF_HUB_DISABLE_XET="1",
            ),
            mock.patch("huggingface_hub.hf_hub_download", fake_hf_hub_download),
            mock.patch.object(
                sys,
                "argv",
                [
                    str(SINGLE_FILE_SCRIPT),
                    "--repo-id",
                    "owner/repo",
                    "--revision",
                    REVISION,
                    "--filename",
                    "config.json",
                    "--local-dir",
                    root,
                ],
            ),
        ):
            self.assertEqual(helper.main(), 0)
            self.assertNotIn("HF_ENDPOINT", os.environ)
        self.assertIsNone(captured["endpoint"])

    def test_exec_wrapper_restores_official_endpoint_for_empty_compose_value(
        self,
    ) -> None:
        env = {
            **os.environ,
            "HF_ENDPOINT": "",
            "HF_HUB_DISABLE_XET": "1",
        }
        result = subprocess.run(
            [
                sys.executable,
                str(EXEC_SCRIPT),
                sys.executable,
                "-c",
                "from huggingface_hub import constants; print(constants.ENDPOINT)",
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "https://huggingface.co")

    def test_exec_wrapper_normalizes_custom_endpoint_and_rejects_paths(self) -> None:
        command = [
            sys.executable,
            str(EXEC_SCRIPT),
            sys.executable,
            "-c",
            "import os; from huggingface_hub import constants; "
            "print(constants.ENDPOINT); "
            "print(os.environ.get('HF_HUB_DISABLE_IMPLICIT_TOKEN'))",
        ]
        with tempfile.TemporaryDirectory() as hf_home:
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in HF_AUTH_ENV_VARS
            } | {
                "HF_ENDPOINT": f"{self.hub.endpoint}/",
                "HF_HOME": hf_home,
                HF_HTTP_APPROVAL_ENV: self.hub.endpoint,
                "HF_HUB_DISABLE_XET": "1",
            }
            result = subprocess.run(
                command, env=env, check=True, capture_output=True, text=True
            )
            self.assertEqual(result.stdout.splitlines(), [self.hub.endpoint, "1"])
            env["HF_ENDPOINT"] = f"{self.hub.endpoint}/unsupported"
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("origin", result.stderr)

    def test_exec_wrapper_denies_arbitrary_or_authenticated_http(self) -> None:
        command = [
            sys.executable,
            str(EXEC_SCRIPT),
            sys.executable,
            "-c",
            "raise SystemExit('child command must not execute')",
        ]
        with tempfile.TemporaryDirectory() as hf_home:
            base_env = {
                key: value
                for key, value in os.environ.items()
                if key not in HF_AUTH_ENV_VARS
            } | {
                "HF_ENDPOINT": self.hub.endpoint,
                "HF_HOME": hf_home,
                "HF_HUB_DISABLE_XET": "1",
            }
            result = subprocess.run(
                command, env=base_env, capture_output=True, text=True
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("approved", result.stderr)
            for auth_name in HF_AUTH_ENV_VARS:
                env = {
                    **base_env,
                    HF_HTTP_APPROVAL_ENV: self.hub.endpoint,
                    auth_name: SECRET_SENTINEL,
                }
                result = subprocess.run(
                    command, env=env, capture_output=True, text=True
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("authentication is not permitted", result.stderr)
                self.assertNotIn(SECRET_SENTINEL, result.stderr)

    def test_exec_wrapper_accepts_https_token_shapes(self) -> None:
        command = [
            sys.executable,
            str(EXEC_SCRIPT),
            sys.executable,
            "-c",
            "print('command-ran')",
        ]
        for auth_name in HF_AUTH_ENV_VARS[:3]:
            env = {
                key: value
                for key, value in os.environ.items()
                if key not in HF_AUTH_ENV_VARS
            } | {
                "HF_ENDPOINT": "https://huggingface.co",
                "HF_HUB_DISABLE_XET": "1",
                auth_name: SECRET_SENTINEL,
            }
            result = subprocess.run(
                command, env=env, check=True, capture_output=True, text=True
            )
            with self.subTest(auth=auth_name):
                self.assertEqual(result.stdout.strip(), "command-ran")

    def test_redirect_library_strips_auth_on_cross_origin_or_downgrade(self) -> None:
        import requests

        session = requests.Session()
        self.assertTrue(
            session.should_strip_auth(
                "https://huggingface.co/model",
                "http://cdn.example.invalid/model",
            )
        )
        self.assertTrue(
            session.should_strip_auth(
                "https://huggingface.co/model",
                "https://other.example.invalid/model",
            )
        )

    def test_active_acquisition_configs_have_no_hardcoded_hub_transport(self) -> None:
        active_paths = (
            ROOT / "deploy/docker/scripts/dev-profile.sh",
            ROOT / "deploy/docker/developer-profiles/dev-profile-search/.env",
            ROOT
            / "deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml",
            ROOT
            / "deploy/docker/services/nim/nvidia-nemotron-nano-9b-v2-fp8/compose.yml",
            ROOT / "deploy/docker/services/nim/qwen3-vl-8b-instruct/compose.yml",
            ROOT / "services/rtvi/rt-embed/docker/compose.yaml",
            ROOT / "services/rtvi/rt-vlm/docker/compose.yaml",
        )
        for path in active_paths:
            content = path.read_text()
            self.assertNotIn("git:https://huggingface.co/", content, path)
            self.assertNotIn("/resolve/main/", content, path)

    def test_active_acquisitions_include_reviewed_immutable_pins(self) -> None:
        expected = {
            ROOT / "deploy/docker/developer-profiles/dev-profile-search/.env": (
                "3b1455ed97c7b1d5419c0c3129b7199ca4cd9382",
            ),
            ROOT / "deploy/docker/scripts/dev-profile.sh": (
                "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b",
            ),
            ROOT / "deploy/docker/services/nim/qwen3-vl-8b-instruct/compose.yml": (
                "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b",
            ),
            ROOT
            / "deploy/docker/services/nim/nvidia-nemotron-nano-9b-v2-fp8/compose.yml": (
                "8bc5eece2eb5514c4bca7f2ec655b91eb554f4c0",
                "6533e8de2c68e4536bf7c411d7a3ce5734111476",
            ),
            ROOT / "services/rtvi/rt-embed/src/scripts/start_rtvi_embed.sh": (
                "f60ec73636eb7c9cc25267367713b7b1b0cffaf3",
            ),
        }
        for path, revisions in expected.items():
            content = path.read_text()
            for revision in revisions:
                self.assertIn(revision, content, path)

    def test_service_downloaders_remain_identical(self) -> None:
        self.assertEqual(
            DOWNLOADER_PATHS[0].read_bytes(), DOWNLOADER_PATHS[1].read_bytes()
        )


if __name__ == "__main__":
    unittest.main()
