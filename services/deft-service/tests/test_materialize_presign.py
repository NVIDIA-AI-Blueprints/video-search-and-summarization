"""Credential-free READ materialize via the Storage Backend presign endpoint.

Stdlib only — mocks the presign endpoint + object GETs at the seams, so no boto3
and no network. Covers _presign_uris batching, single-object presign (checkpoint/
kpi), and s3:// prefix enumeration via snapshot manifests.

Run: uv run --with "pydantic>=2.6" pytest tests/test_materialize_presign.py
"""

from __future__ import annotations

import json

import pytest

from app.execution import materialize as mat


@pytest.fixture
def presign_cfg(monkeypatch):
    monkeypatch.setattr(mat, "MATERIALIZE_PRESIGN_URL", "http://sb/v1/get_presigned_urls")
    monkeypatch.setattr(mat, "MATERIALIZE_STORAGE_URL", "http://sb")
    monkeypatch.setattr(mat, "MATERIALIZE_PRESIGN_TOKEN", None)


class TestPresignMode:
    def test_presign_mode_true_only_without_storage_access(self, presign_cfg):
        assert mat._presign_mode(None) is True

    def test_presign_mode_off_when_no_url(self, monkeypatch):
        monkeypatch.setattr(mat, "MATERIALIZE_PRESIGN_URL", None)
        assert mat._presign_mode(None) is False


class TestPresignUris:
    def test_batches_at_50_and_maps_input_to_url(self, presign_cfg, monkeypatch):
        calls = []

        def fake_http_json(url, method, payload=None):
            calls.append(len(payload["urls"]))
            return {"items": [{"input": u, "presigned_url": f"http://s/{u[-3:]}"} for u in payload["urls"]], "failed": []}

        monkeypatch.setattr(mat, "_http_json", fake_http_json)
        uris = [f"s3://b/obj{i:03d}" for i in range(120)]
        out = mat._presign_uris(uris)
        assert len(out) == 120
        assert calls == [50, 50, 20]  # batched at _PRESIGN_BATCH
        assert out["s3://b/obj000"] == "http://s/000"

    def test_failed_items_logged_and_omitted(self, presign_cfg, monkeypatch):
        monkeypatch.setattr(mat, "_http_json", lambda *a, **k: {"items": [], "failed": [{"input": "s3://b/x", "error": "nope"}]})
        assert mat._presign_uris(["s3://b/x"]) == {}

    def test_presign_one_raises_when_unsigned(self, presign_cfg, monkeypatch):
        monkeypatch.setattr(mat, "_http_json", lambda *a, **k: {"items": [], "failed": []})
        with pytest.raises(mat.MaterializeError, match="could not presign"):
            mat._presign_one("s3://b/x")


class TestPrefixEnumeration:
    def test_enumerates_manifests_and_data_objects(self, presign_cfg, monkeypatch):
        prefix = "s3://aoi/snapshots/SNAP1/"
        manifest_uri = "s3://aoi/snapshots/SNAP1/manifest0.jsonl"
        # snapshot detail returns the manifest uri; presign returns a fake url;
        # reading the manifest yields two data objects (one s3:// value, one {key}).
        monkeypatch.setattr(mat, "_http_json", lambda url, method, payload=None: (
            {"manifests": [{"uri": manifest_uri}]} if method == "GET"
            else {"items": [{"input": u, "presigned_url": f"http://s/{u.rsplit('/',1)[-1]}"} for u in payload["urls"]], "failed": []}
        ))
        manifest_text = "\n".join([
            json.dumps({"uri": "s3://aoi/snapshots/SNAP1/img/a.png"}),
            json.dumps({"key": "snapshots/SNAP1/img/b.png"}),
        ])
        monkeypatch.setattr(mat.urllib.request, "urlopen", _fake_urlopen(manifest_text))

        uris = mat._snapshot_object_uris(prefix)
        assert manifest_uri in uris
        assert "s3://aoi/snapshots/SNAP1/img/a.png" in uris
        assert "s3://aoi/snapshots/SNAP1/img/b.png" in uris

    def test_snapshot_id_from_prefix(self, presign_cfg, monkeypatch):
        seen = {}
        monkeypatch.setattr(mat, "_http_json", lambda url, method, payload=None: (seen.update({"url": url}) or {"manifests": []}))
        mat._snapshot_object_uris("s3://aoi/snapshots/ABC123/")
        assert seen["url"].endswith("/v1/snapshot/ABC123")


def _fake_urlopen(text):
    import contextlib

    @contextlib.contextmanager
    def _cm(url, timeout=None):
        class R:
            def read(self_inner):
                return text.encode()
        yield R()
    return _cm
