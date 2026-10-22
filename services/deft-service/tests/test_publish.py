"""Object-store artifact publishing (app/execution/publish.py) — moto-mocked S3.

Run with the extra deps:  uv run --with boto3 --with moto pytest tests/test_publish.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

boto3 = pytest.importorskip("boto3")
pytest.importorskip("moto")

from moto import mock_aws  # noqa: E402

from app.execution import publish as pub  # noqa: E402
from app.schemas.fine_tune_models import (  # noqa: E402
    S3TemporaryCredentials,
    TemporaryCredentialsStorageAccess,
)

BUCKET = "aoi-artifacts"
PREFIX = f"s3://{BUCKET}/deft-runs"


def _storage_access(expires_in_s: int) -> TemporaryCredentialsStorageAccess:
    return TemporaryCredentialsStorageAccess(
        type="temporary_credentials",
        endpoint_url=None,
        region="us-east-1",
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_s),
        credentials=S3TemporaryCredentials(
            access_key_id="AKIATEST", secret_access_key="secret", session_token="token"
        ),
    )


@pytest.fixture
def s3_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        yield s3


@pytest.fixture
def publish_prefix(monkeypatch):
    monkeypatch.setattr(pub, "PUBLISH_PREFIX", PREFIX)


@pytest.fixture
def artifacts(tmp_path):
    ckpt = tmp_path / "weights.pt"
    ckpt.write_bytes(b"trained-weights")
    spec = tmp_path / "baseline_spec.yaml"
    spec.write_text("train: {}\n")
    return ckpt, spec


class TestPublish:
    def test_uploads_and_returns_s3_uris(self, s3_bucket, publish_prefix, artifacts):
        ckpt, spec = artifacts
        out = pub.publish_artifacts(
            "run-1", {"best_checkpoint": ckpt, "training_spec": spec}, _storage_access(3600)
        )
        assert out["best_checkpoint"] == f"{PREFIX}/run-1/weights.pt"
        assert out["training_spec"] == f"{PREFIX}/run-1/baseline_spec.yaml"
        body = s3_bucket.get_object(Bucket=BUCKET, Key="deft-runs/run-1/weights.pt")["Body"].read()
        assert body == b"trained-weights"

    def test_expired_storage_access_falls_back_to_default_chain(
        self, s3_bucket, publish_prefix, artifacts
    ):
        # Temporary creds often expire during a long training — publish must
        # not fail on that alone (moto also intercepts the default chain).
        ckpt, spec = artifacts
        out = pub.publish_artifacts(
            "run-2", {"best_checkpoint": ckpt}, _storage_access(expires_in_s=-60)
        )
        assert out["best_checkpoint"].startswith(f"{PREFIX}/run-2/")

    def test_missing_checkpoint_fails_loud(self, s3_bucket, publish_prefix, tmp_path):
        with pytest.raises(pub.PublishError, match="best checkpoint"):
            pub.publish_artifacts(
                "run-3", {"best_checkpoint": tmp_path / "nope.pt"}, _storage_access(3600)
            )

    def test_missing_optional_artifact_is_skipped(self, s3_bucket, publish_prefix, artifacts, tmp_path):
        ckpt, _ = artifacts
        out = pub.publish_artifacts(
            "run-4",
            {"best_checkpoint": ckpt, "training_spec": tmp_path / "absent.yaml"},
            _storage_access(3600),
        )
        assert "training_spec" not in out
        assert "best_checkpoint" in out

    def test_disabled_without_prefix(self, monkeypatch, artifacts):
        monkeypatch.setattr(pub, "PUBLISH_PREFIX", None)
        monkeypatch.setattr(pub, "PUBLISH_PRESIGN_URL", None)
        assert pub.publish_enabled() is False
        with pytest.raises(pub.PublishError, match="neither"):
            pub.publish_artifacts("run-5", {"best_checkpoint": artifacts[0]}, None)
