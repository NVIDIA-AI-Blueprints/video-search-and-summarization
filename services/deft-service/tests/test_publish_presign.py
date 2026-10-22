"""Credential-free publish via S3 presigned POST (app/execution/publish.py).

No boto3/moto: the presigned-POST path never touches boto3 (it uploads via
httpx, already a service dependency). The happy-path test runs a real local
HTTP server so the httpx multipart upload is exercised end to end.

Run:  uv run --with httpx pytest tests/test_publish_presign.py
"""

from __future__ import annotations

import email
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.execution import publish as pub


@pytest.fixture
def artifacts(tmp_path):
    ckpt = tmp_path / "weights.pt"
    ckpt.write_bytes(b"trained-weights" * 100_000)  # ~1.4 MB -> exercises chunking
    spec = tmp_path / "baseline_spec.yaml"
    spec.write_text("train: {}\n")
    return ckpt, spec


# --------------------------------------------------------------------------- #
# _resolve_published_uri — the durable s3:// locator that round-trips to reads
# --------------------------------------------------------------------------- #


class TestResolveUri:
    def test_prefers_explicit_uri(self):
        uri = pub._resolve_published_uri(
            {"uri": "s3://b/explicit/model.pt"}, "http://h:8333/b", {"key": "ignored"}, "model.pt"
        )
        assert uri == "s3://b/explicit/model.pt"

    def test_derives_bucket_from_pathstyle_url_and_key(self):
        uri = pub._resolve_published_uri(
            {}, "http://h:8333/aoi-artifacts", {"key": "deft/run-1/model.pt"}, "model.pt"
        )
        assert uri == "s3://aoi-artifacts/deft/run-1/model.pt"

    def test_substitutes_filename_variable(self):
        uri = pub._resolve_published_uri(
            {}, "http://h:8333/bkt", {"key": "deft/run-1/${filename}"}, "weights.pt"
        )
        assert uri == "s3://bkt/deft/run-1/weights.pt"

    def test_explicit_bucket_wins_over_url(self):
        uri = pub._resolve_published_uri(
            {"bucket": "real-bucket"}, "http://h:8333/wrong", {"key": "k/model.pt"}, "model.pt"
        )
        assert uri == "s3://real-bucket/k/model.pt"

    def test_missing_key_raises(self):
        with pytest.raises(pub.PublishError, match="cannot derive"):
            pub._resolve_published_uri({}, "http://h:8333/bkt", {}, "model.pt")


# --------------------------------------------------------------------------- #
# Orchestration — dispatch, failure modes (HTTP stubbed at the seam)
# --------------------------------------------------------------------------- #


class TestPublishOrchestration:
    @pytest.fixture
    def presign_configured(self, monkeypatch):
        monkeypatch.setattr(pub, "PUBLISH_PRESIGN_URL", "http://sb/v1/get_presigned_post_url")
        monkeypatch.setattr(pub, "PUBLISH_PREFIX", None)

    def test_enabled_with_presign_url(self, monkeypatch):
        monkeypatch.setattr(pub, "PUBLISH_PRESIGN_URL", "http://sb/x")
        monkeypatch.setattr(pub, "PUBLISH_PREFIX", None)
        assert pub.publish_enabled() is True

    def test_presign_takes_precedence_over_boto3(self, presign_configured, monkeypatch, artifacts):
        monkeypatch.setattr(pub, "PUBLISH_PREFIX", "s3://b/p")  # both set
        seen = {}

        def fake_presign(ct, folder, file_name):
            seen["folder"] = folder
            return {"url": "http://h:8333/aoi", "fields": {"key": f"upload/{folder}/{file_name}"}}

        monkeypatch.setattr(pub, "_request_presigned_post", fake_presign)
        monkeypatch.setattr(pub, "_upload_multipart", lambda *a, **k: 204)
        out = pub.publish_artifacts("run-1", {"best_checkpoint": artifacts[0]}, None)
        assert out["best_checkpoint"] == "s3://aoi/upload/run-1/weights.pt"
        assert seen["folder"] == "run-1"  # single-segment run id; boto3 path never touched

    def test_non_2xx_upload_fails_loud(self, presign_configured, monkeypatch, artifacts):
        monkeypatch.setattr(
            pub, "_request_presigned_post",
            lambda *a: {"url": "http://h/aoi", "fields": {"key": "k/model.pt"}},
        )
        monkeypatch.setattr(pub, "_upload_multipart", lambda *a, **k: 403)
        with pytest.raises(pub.PublishError, match="HTTP 403"):
            pub.publish_artifacts("run-2", {"best_checkpoint": artifacts[0]}, None)

    def test_missing_best_checkpoint_fails_loud(self, presign_configured, monkeypatch, tmp_path):
        monkeypatch.setattr(
            pub, "_request_presigned_post",
            lambda *a: {"url": "http://h/aoi", "fields": {"key": "k/x"}},
        )
        monkeypatch.setattr(pub, "_upload_multipart", lambda *a, **k: 204)
        with pytest.raises(pub.PublishError, match="best checkpoint"):
            pub.publish_artifacts("run-3", {"best_checkpoint": tmp_path / "nope.pt"}, None)

    def test_bad_presign_response_fails_loud(self, presign_configured, monkeypatch, artifacts):
        monkeypatch.setattr(pub, "_request_presigned_post", lambda *a: {"nope": 1})
        with pytest.raises(pub.PublishError, match="missing url/fields"):
            pub.publish_artifacts("run-4", {"best_checkpoint": artifacts[0]}, None)

    def test_disabled_without_any_target(self, monkeypatch, artifacts):
        monkeypatch.setattr(pub, "PUBLISH_PRESIGN_URL", None)
        monkeypatch.setattr(pub, "PUBLISH_PREFIX", None)
        assert pub.publish_enabled() is False
        with pytest.raises(pub.PublishError, match="neither"):
            pub.publish_artifacts("run-5", {"best_checkpoint": artifacts[0]}, None)


# --------------------------------------------------------------------------- #
# End-to-end against a real local HTTP server (streaming upload exercised)
# --------------------------------------------------------------------------- #

BUCKET = "aoi-artifacts"


class _FakeStorageBackend(BaseHTTPRequestHandler):
    received: dict[str, bytes] = {}  # key -> uploaded file bytes

    def log_message(self, *a):  # silence
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path == "/v1/get_presigned_post_url":
            req = json.loads(body)
            # Server owns the upload prefix; folder is a single segment (run id).
            key = f"upload/{req['folder']}/{req['file_name']}"
            self._json(200, {
                "url": f"http://{self.headers['Host']}/{BUCKET}",
                "fields": {"key": key, "policy": "POLICY", "x-amz-signature": "SIG",
                           "Content-Type": req["content_type"]},
            })
            return
        if self.path == f"/{BUCKET}":
            # Parse the multipart body: pull the key field + the file bytes.
            msg = email.message_from_bytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body
            )
            key, filedata = None, None
            for part in msg.get_payload():
                name = part.get_param("name", header="content-disposition")
                if name == "key":
                    key = part.get_payload(decode=True).decode()
                elif name == "file":
                    filedata = part.get_payload(decode=True)
            _FakeStorageBackend.received[key] = filedata
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


class TestPresignedPostEndToEnd:
    def test_full_roundtrip_streams_files_and_returns_uris(self, monkeypatch, artifacts):
        ckpt, spec = artifacts
        _FakeStorageBackend.received = {}
        server = HTTPServer(("127.0.0.1", 0), _FakeStorageBackend)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            monkeypatch.setattr(
                pub, "PUBLISH_PRESIGN_URL", f"http://127.0.0.1:{port}/v1/get_presigned_post_url"
            )
            monkeypatch.setattr(pub, "PUBLISH_PREFIX", None)
            out = pub.publish_artifacts(
                "run-9", {"best_checkpoint": ckpt, "training_spec": spec}, None
            )
        finally:
            server.shutdown()

        assert out["best_checkpoint"] == f"s3://{BUCKET}/upload/run-9/weights.pt"
        assert out["training_spec"] == f"s3://{BUCKET}/upload/run-9/baseline_spec.yaml"
        # The bytes actually arrived at the server, intact, via the streamed POST.
        assert _FakeStorageBackend.received["upload/run-9/weights.pt"] == ckpt.read_bytes()
        assert _FakeStorageBackend.received["upload/run-9/baseline_spec.yaml"] == spec.read_bytes()
