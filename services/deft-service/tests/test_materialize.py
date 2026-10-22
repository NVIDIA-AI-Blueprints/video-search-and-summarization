"""Object-store materialization (app/execution/materialize.py) — moto-mocked S3.

Run with the extra deps:  uv run --with boto3 --with moto pytest tests/test_materialize.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from moto import mock_aws  # noqa: E402

from app.execution import materialize as mat  # noqa: E402
from app.execution.workspace import Workspace  # noqa: E402
from app.schemas.config_models import (  # noqa: E402
    DataPoolConfig,
    ExperimentConfig,
    KpiConfig,
    ModelConfig,
)
from app.schemas.fine_tune_models import (  # noqa: E402
    S3TemporaryCredentials,
    TemporaryCredentialsStorageAccess,
)

BUCKET = "aoi-artifacts"
SNAP_PREFIX = "snapshots/snap-777/"
#: Minimal DEFT workspace layout, as the Storage Backend would materialize it.
SNAPSHOT_OBJECTS = {
    "kpi/testing_set.csv": "input_path,golden_path,label\na.jpg,g.jpg,PASS\n",
    "kpi/images/a.jpg": "jpegbytes",
    "train/base/training_set.csv": "input_path,golden_path,label\na.jpg,g.jpg,PASS\n",
    "train/base/validation_set.csv": "input_path,golden_path,label\na.jpg,g.jpg,PASS\n",
    "augmentation/mining_pool/mining_pool.csv": "input_path,golden_path\np.jpg,g.jpg\n",
    "specs/baseline_spec.yaml": "train: {}\n",
    "specs/baseline_inference_spec.yaml": "inference: {}\n",
}
CKPT_KEY = "models/changenet/1.0.0/weights.pt"
#: Multi-file (HF-style) model directory: config + sharded weights.
MODEL_DIR_PREFIX = "models/siglip/2.0.0/"
MODEL_DIR_OBJECTS = {
    "config.json": '{"architectures": ["SiglipModel"]}',
    "model-00001-of-00002.safetensors": "shard-one",
    "model-00002-of-00002.safetensors": "shard-two",
    "preprocessor_config.json": '{"size": 384}',
}
KPI_SET_KEY = "kpi_sets/kpi-42/testing_set.csv"


def _storage_access(expires_in_s: int = 3600) -> TemporaryCredentialsStorageAccess:
    return TemporaryCredentialsStorageAccess(
        type="temporary_credentials",
        endpoint_url=None,  # moto intercepts the default endpoint
        region="us-east-1",
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_s),
        credentials=S3TemporaryCredentials(
            access_key_id="AKIATEST", secret_access_key="secret", session_token="token"
        ),
    )


def _config(**overrides) -> ExperimentConfig:
    fields = {
        "model": ModelConfig(name="changenet-aoi", checkpoint=f"s3://{BUCKET}/{CKPT_KEY}"),
        "data_pool": DataPoolConfig(path=f"s3://{BUCKET}/{SNAP_PREFIX}"),
        "kpi": KpiConfig(path=f"s3://{BUCKET}/{KPI_SET_KEY}"),
    }
    fields.update(overrides)
    return ExperimentConfig(**fields)


@pytest.fixture
def s3_bucket():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        for rel, body in SNAPSHOT_OBJECTS.items():
            s3.put_object(Bucket=BUCKET, Key=SNAP_PREFIX + rel, Body=body.encode())
        s3.put_object(Bucket=BUCKET, Key=CKPT_KEY, Body=b"weights")
        for rel, body in MODEL_DIR_OBJECTS.items():
            s3.put_object(Bucket=BUCKET, Key=MODEL_DIR_PREFIX + rel, Body=body.encode())
        s3.put_object(Bucket=BUCKET, Key=KPI_SET_KEY, Body=b"kpi,csv\n")
        yield s3


@pytest.fixture
def mat_root(tmp_path, monkeypatch):
    monkeypatch.setattr(mat, "MATERIALIZE_ROOT", str(tmp_path))
    return tmp_path


class TestNeedsMaterialization:
    def test_local_config_is_a_noop(self, tmp_path):
        cfg = _config(
            model=ModelConfig(name="m", checkpoint="checkpoints/w.pt"),
            data_pool=DataPoolConfig(path=str(tmp_path)),
            kpi=KpiConfig(path=None),
        )
        assert mat.needs_materialization(cfg) is False
        out = mat.materialize(cfg, None, "run-x")
        assert out.data_pool.path == str(tmp_path)  # untouched

    def test_any_s3_ref_triggers(self):
        assert mat.needs_materialization(_config()) is True


class TestMaterialize:
    def test_snapshot_mirrored_and_workspace_resolves(self, s3_bucket, mat_root):
        out = mat.materialize(_config(), _storage_access(), "run-1")

        ws = Workspace.resolve(out.data_pool.path)  # would raise on s3:// / missing dir
        assert ws.train_csv.is_file()
        assert ws.val_csv.is_file()
        assert (ws.root / "kpi/testing_set.csv").is_file()
        assert (ws.root / "kpi/images/a.jpg").read_bytes() == b"jpegbytes"
        assert str(ws.root).startswith(str(mat_root))

    def test_checkpoint_lands_inside_workspace_as_relative_path(self, s3_bucket, mat_root):
        out = mat.materialize(_config(), _storage_access(), "run-2")
        ws = Workspace.resolve(out.data_pool.path)

        assert out.model.checkpoint == "checkpoints/weights.pt"  # workspace-relative
        ckpt = ws.host(out.model.checkpoint)
        assert ckpt.read_bytes() == b"weights"
        # Inside the workspace => mappable into the container mount.
        assert ws.container(ckpt).startswith("/")

    def test_model_directory_checkpoint_mirrored_inside_workspace(self, s3_bucket, mat_root):
        # A TRAILING-SLASH checkpoint URI is a multi-file model directory
        # (HF-style): mirrored recursively into workspace/checkpoints/<name>/
        # and rewritten to the workspace-relative directory path.
        cfg = _config(
            model=ModelConfig(name="siglip", checkpoint=f"s3://{BUCKET}/{MODEL_DIR_PREFIX}")
        )
        out = mat.materialize(cfg, _storage_access(), "run-dir-1")
        ws = Workspace.resolve(out.data_pool.path)

        assert out.model.checkpoint == "checkpoints/2.0.0"
        model_dir = ws.host(out.model.checkpoint)
        assert model_dir.is_dir()
        for rel, body in MODEL_DIR_OBJECTS.items():
            assert (model_dir / rel).read_text() == body
        # Inside the workspace => mappable into the container mount.
        assert ws.container(model_dir).startswith("/")

    def test_empty_model_directory_fails_loud(self, s3_bucket, mat_root):
        cfg = _config(
            model=ModelConfig(name="m", checkpoint=f"s3://{BUCKET}/models/absent/1.0/")
        )
        with pytest.raises(mat.MaterializeError, match="no objects"):
            mat.materialize(cfg, _storage_access(), "run-dir-2")

    def test_kpi_set_lands_inside_workspace(self, s3_bucket, mat_root):
        out = mat.materialize(_config(), _storage_access(), "run-3")
        ws = Workspace.resolve(out.data_pool.path)

        assert out.kpi.path == "kpi_sets/testing_set.csv"
        assert ws.host(out.kpi.path).read_bytes() == b"kpi,csv\n"

    def test_original_config_not_mutated(self, s3_bucket, mat_root):
        cfg = _config()
        mat.materialize(cfg, _storage_access(), "run-4")
        assert cfg.data_pool.path.startswith("s3://")

    def test_expired_credentials_fail_before_download(self, s3_bucket, mat_root):
        with pytest.raises(mat.MaterializeError, match="expired"):
            mat.materialize(_config(), _storage_access(expires_in_s=-60), "run-5")

    def test_empty_prefix_fails_loud(self, s3_bucket, mat_root):
        cfg = _config(data_pool=DataPoolConfig(path=f"s3://{BUCKET}/snapshots/missing/"))
        with pytest.raises(mat.MaterializeError, match="no objects"):
            mat.materialize(cfg, _storage_access(), "run-6")

    def test_default_credential_chain_when_no_storage_access(self, s3_bucket, mat_root):
        # moto also intercepts the default provider chain.
        out = mat.materialize(_config(), None, "run-7")
        assert Workspace.resolve(out.data_pool.path).train_csv.is_file()


# ---- Presigned http(s) references (POST /v1/get_presigned_urls) -------------

import io  # noqa: E402


class _FakeHTTPResponse(io.BytesIO):
    """Minimal stand-in for urllib.request.urlopen's response (a context manager)."""

    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_needs_materialization_true_for_presigned_http():
    cfg = _config(
        data_pool=DataPoolConfig(path="/already/local/ws"),
        model=ModelConfig(
            name="m",
            checkpoint="https://s3.internal/aoi-artifacts/model.pth?X-Amz-Signature=abc",
        ),
        kpi=KpiConfig(path=None),
    )
    assert mat.needs_materialization(cfg) is True


def test_presigned_checkpoint_downloads_via_http_no_boto3(tmp_path, monkeypatch):
    # A presigned checkpoint URL is a single object -> plain GET into the
    # workspace. data_pool is already local, so NO s3:// ref exists and boto3
    # is never imported (pure-presigned path).
    presigned = (
        "https://s3.internal/aoi-artifacts/models/changenet/1.0.0/model.pth"
        "?X-Amz-Signature=sig&Expires=123"
    )
    captured = {}

    def fake_urlopen(url, timeout=None):
        captured["url"] = url
        return _FakeHTTPResponse(b"changenet-weights")

    monkeypatch.setattr("app.execution.materialize.urllib.request.urlopen", fake_urlopen)

    cfg = _config(
        data_pool=DataPoolConfig(path=str(tmp_path)),
        model=ModelConfig(name="m", checkpoint=presigned),
        kpi=KpiConfig(path=None),
    )
    out = mat.materialize(cfg, None, "run-http")

    assert out.model.checkpoint == "checkpoints/model.pth"  # basename from URL path, query ignored
    assert (tmp_path / "checkpoints" / "model.pth").read_bytes() == b"changenet-weights"
    assert captured["url"] == presigned  # the full signed URL (query intact) was used


def test_presigned_data_pool_is_rejected():
    # A snapshot is a PREFIX of many objects; a single presigned URL can't
    # represent it -> fail loudly (needs an s3:// prefix or a per-object manifest).
    cfg = _config(
        data_pool=DataPoolConfig(
            path="https://s3.internal/aoi-artifacts/snapshots/snap-1/?X-Amz-Signature=sig"
        ),
        kpi=KpiConfig(path=None),
    )
    with pytest.raises(mat.MaterializeError, match="cannot mirror a prefix"):
        mat.materialize(cfg, None, "run-x")


def test_presigned_snapshot_manifest_downloads_all_data_points(tmp_path, monkeypatch):
    # The full snapshot delivered as a per-object presigned manifest: EVERY data
    # point is downloaded to workspace/<path>, reconstructing the layout. No
    # s3:// ref -> no boto3.
    from app.schemas.config_models import PresignedFile

    manifest_bytes = {
        "kpi/testing_set.csv": b"input_path,golden_path,label\na.jpg,g.jpg,PASS\n",
        "kpi/images/a.jpg": b"jpegbytes-a",
        "train/base/training_set.csv": b"input_path,golden_path,label\na.jpg,g.jpg,PASS\n",
        "specs/baseline_spec.yaml": b"train: {}\n",
    }

    def fake_urlopen(url, timeout=None):
        # URL path basename maps back to which object this is (deterministic fixture).
        from urllib.parse import urlparse as _up

        key = _up(url).path.lstrip("/")  # e.g. "signed/kpi/images/a.jpg"
        rel = key.split("signed/", 1)[-1]
        return _FakeHTTPResponse(manifest_bytes[rel])

    monkeypatch.setattr("app.execution.materialize.urllib.request.urlopen", fake_urlopen)

    files = [
        PresignedFile(path=rel, url=f"https://s3.internal/signed/{rel}?X-Amz-Signature=s")
        for rel in manifest_bytes
    ]
    cfg = _config(
        data_pool=DataPoolConfig(path=f"s3://{BUCKET}/{SNAP_PREFIX}", files=files),
        model=ModelConfig(name="m", checkpoint="checkpoints/w.pt"),  # local, already staged
        kpi=KpiConfig(path=None),
    )
    assert mat.needs_materialization(cfg) is True
    out = mat.materialize(cfg, None, "run-manifest")

    ws = tmp_path  # not used; workspace is under MATERIALIZE_ROOT/run-manifest
    ws_root = out.data_pool.path
    from pathlib import Path as _P

    for rel, content in manifest_bytes.items():
        assert (_P(ws_root) / rel).read_bytes() == content  # every data point present
    assert out.data_pool.files is None  # consumed


def test_presigned_manifest_path_traversal_is_skipped_not_written(monkeypatch):
    # A traversal entry must be SKIPPED (never written outside the workspace) —
    # and, being fail-soft, it does not stop materialization.
    from app.schemas.config_models import PresignedFile

    calls = []
    monkeypatch.setattr(
        "app.execution.materialize.urllib.request.urlopen",
        lambda url, timeout=None: calls.append(url) or _FakeHTTPResponse(b"x"),
    )
    cfg = _config(
        data_pool=DataPoolConfig(
            path="snap",
            files=[PresignedFile(path="../escape.txt", url="https://s3.internal/x?sig=1")],
        ),
        model=ModelConfig(name="m", checkpoint="checkpoints/w.pt"),
        kpi=KpiConfig(path=None),
    )
    out = mat.materialize(cfg, None, "run-evil")  # no raise
    assert calls == []  # the escaping entry was never downloaded
    assert out.data_pool.files is None


def test_presigned_manifest_partial_failure_is_soft(monkeypatch):
    # One object fails to download; the rest succeed. Fail-soft: NO raise, the
    # good objects are present, the failed one is skipped (no partial file).
    from pathlib import Path as _P

    from app.schemas.config_models import PresignedFile

    good = {"kpi/testing_set.csv": b"csvdata", "specs/spec.yaml": b"specdata"}

    def fake_urlopen(url, timeout=None):
        from urllib.parse import urlparse as _up

        rel = _up(url).path.split("signed/", 1)[-1]
        if rel == "kpi/images/missing.jpg":
            raise OSError("HTTP 404 not found")  # a failed download
        return _FakeHTTPResponse(good[rel])

    monkeypatch.setattr("app.execution.materialize.urllib.request.urlopen", fake_urlopen)

    rels = ["kpi/testing_set.csv", "kpi/images/missing.jpg", "specs/spec.yaml"]
    files = [PresignedFile(path=r, url=f"https://s3.internal/signed/{r}?sig=1") for r in rels]
    cfg = _config(
        data_pool=DataPoolConfig(path=f"s3://{BUCKET}/{SNAP_PREFIX}", files=files),
        model=ModelConfig(name="m", checkpoint="checkpoints/w.pt"),
        kpi=KpiConfig(path=None),
    )
    out = mat.materialize(cfg, None, "run-partial")  # no raise despite the failure
    ws = _P(out.data_pool.path)
    assert (ws / "kpi/testing_set.csv").read_bytes() == b"csvdata"
    assert (ws / "specs/spec.yaml").read_bytes() == b"specdata"
    assert not (ws / "kpi/images/missing.jpg").exists()  # skipped, no partial left
    assert not (ws / "kpi/images/missing.jpg.partial").exists()
