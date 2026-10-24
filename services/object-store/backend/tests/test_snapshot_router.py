import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.application import app
from backend.api.snapshot_router import get_es
from backend.config import settings


@pytest.fixture
def override_es():
    es = MagicMock()
    es.open_pit = AsyncMock(return_value="PIT-xyz")
    # /stats always queries ES now (there is no short-circuit before the first
    # snapshot), and creating a snapshot counts its selection first, so give
    # every test a usable non-empty default; cases that assert on the totals
    # override it.
    es.catalog_totals = AsyncMock(return_value=(42, 4200))
    app.dependency_overrides[get_es] = lambda: es
    yield es
    app.dependency_overrides.pop(get_es, None)


class TestCreateSnapshot:
    def test_returns_202_and_snapshot_id(self, client, override_es):
        resp = client.post("/v1/aoi/snapshot", json={"selection": {"created_after": "2026-06-12T00:00:00Z"}})
        assert resp.status_code == 202
        assert resp.json()["snapshot_id"]

    def test_trusts_caller_snapshot_id(self, client, override_es):
        resp = client.post(
            "/v1/aoi/snapshot",
            json={"selection": {"created_after": "2026-06-12T00:00:00Z"}, "snapshot_id": "caller-1"},
        )
        assert resp.status_code == 202
        assert resp.json()["snapshot_id"] == "caller-1"

    def test_missing_selection_returns_422(self, client, override_es):
        resp = client.post("/v1/aoi/snapshot", json={})
        assert resp.status_code == 422

    def test_missing_created_after_returns_422(self, client, override_es):
        resp = client.post("/v1/aoi/snapshot", json={"selection": {}})
        assert resp.status_code == 422

    def test_unsupported_selection_field_returns_422(self, client, override_es):
        resp = client.post(
            "/v1/its/snapshot",
            json={"selection": {"created_after": "2026-06-12T00:00:00Z", "cameras": ["c1"]}},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["unknown_fields"] == ["cameras"]

    def test_rejection_does_not_ship_the_whole_allow_list(self, client, override_es):
        # The catalog maps 40+ selection fields; returning all of them on every
        # rejection is noise the caller cannot act on. Only the offending keys
        # come back.
        resp = client.post(
            "/v1/its/snapshot",
            json={"selection": {"created_after": "2026-06-12T00:00:00Z", "cameras": ["c1"]}},
        )
        detail = resp.json()["detail"]
        assert detail["unknown_fields"] == ["cameras"]
        assert detail["catalog"] == "its"
        body = json.dumps(detail)
        for mapped in ("sensorId", "objectTypes", "analyticsModule.version"):
            assert mapped not in body

    def test_valid_its_selection_returns_202(self, client, override_es):
        resp = client.post(
            "/v1/its/snapshot",
            json={
                "selection": {
                    "created_after": "2026-06-12T00:00:00Z",
                    "tag": "its-train",
                    "sensorId": "cam-1",
                }
            },
        )
        assert resp.status_code == 202

    def test_aoi_field_is_rejected_on_the_its_route(self, client, override_es):
        body = {"selection": {"created_after": "2026-06-12T00:00:00Z", "machineId": "M-1"}}
        assert client.post("/v1/aoi/snapshot", json=body).status_code == 202
        resp = client.post("/v1/its/snapshot", json=body)
        assert resp.status_code == 422
        assert resp.json()["detail"]["unknown_fields"] == ["machineId"]

    def test_unsupported_field_on_the_legacy_alias_returns_422(self, client, override_es):
        resp = client.post(
            "/v1/snapshot",
            json={"selection": {"created_after": "2026-06-12T00:00:00Z", "sample_fps": 2}},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["unknown_fields"] == ["sample_fps"]

    def test_empty_selection_returns_422(self, client, override_es):
        # A selection whose values match nothing must not yield a ready,
        # zero-record snapshot.
        override_es.catalog_totals = AsyncMock(return_value=(0, 0))
        resp = client.post(
            "/v1/its/snapshot",
            json={"selection": {"created_after": "2026-06-12T00:00:00Z", "tag": "its-small-200b"}},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["selection"]["tag"] == "its-small-200b"
        override_es.open_pit.assert_not_awaited()

    def test_empty_selection_on_the_legacy_alias_returns_422(self, client, override_es):
        override_es.catalog_totals = AsyncMock(return_value=(0, 0))
        resp = client.post(
            "/v1/snapshot", json={"selection": {"created_after": "2026-06-12T00:00:00Z"}}
        )
        assert resp.status_code == 422

    def test_fanout_failure_returns_503(self, client, override_es):
        override_es.close_pit = AsyncMock()
        app.state.kafka.send = AsyncMock(side_effect=RuntimeError("broker down"))
        resp = client.post("/v1/aoi/snapshot", json={"selection": {"created_after": "2026-06-12T00:00:00Z"}})
        assert resp.status_code == 503


class TestGetSnapshot:
    def test_returns_finalized_snapshot(self, client, mock_storage):
        doc = {
            "snapshot_id": "s1",
            "status": "ready",
            "total_records": 42,
            "manifests": [
                {"format": "jsonl", "uri": "s3://b/k", "size_bytes": 10, "sha256": "x", "record_count": 5}
            ],
        }
        mock_storage.exists = AsyncMock(return_value=True)  # snapshot.json present
        mock_storage.get_bytes = AsyncMock(return_value=json.dumps(doc).encode())
        resp = client.get("/v1/aoi/snapshot/s1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["total_records"] == 42
        assert body["manifests"][0]["uri"] == "s3://b/k"

    def test_returns_creating(self, client, mock_storage):
        async def _exists(key):
            return key.endswith("_request.json")

        mock_storage.exists = AsyncMock(side_effect=_exists)
        mock_storage.get_bytes = AsyncMock(
            return_value=json.dumps(
                {"snapshot_id": "s2", "requested_at": "2026-07-16T00:00:00Z", "slice_count": 3}
            ).encode()
        )
        resp = client.get("/v1/aoi/snapshot/s2")
        assert resp.status_code == 200
        assert resp.json()["status"] == "creating"

    def test_returns_404_when_unknown(self, client, mock_storage):
        mock_storage.exists = AsyncMock(return_value=False)  # neither object exists
        resp = client.get("/v1/aoi/snapshot/nope")
        assert resp.status_code == 404


class TestCatalogStats:
    def test_returns_live_stats(self, client, override_es, mock_storage):
        latest = {
            "snapshot_id": "prev1",
            "created_at": "2026-07-01T00:00:00Z",
            "query": {"bool": {"filter": []}},
            "total_records": 100,
            "total_bytes": 1000,
        }
        mock_storage.exists = AsyncMock(return_value=True)
        mock_storage.get_bytes = AsyncMock(return_value=json.dumps(latest).encode())
        override_es.catalog_totals = AsyncMock(return_value=(150, 1500))

        resp = client.get("/v1/aoi/snapshot/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "live"
        assert body["total_records"] == 150
        assert body["records_since_last_snapshot"] == 50
        assert body["last_snapshot_id"] == "prev1"
        # confirms /stats routed here (not captured by /{snapshot_id})
        override_es.catalog_totals.assert_awaited_once()

    def test_returns_live_totals_when_no_snapshot(self, client, override_es, mock_storage):
        mock_storage.exists = AsyncMock(return_value=False)
        override_es.catalog_totals = AsyncMock(return_value=(150, 1500))

        resp = client.get("/v1/aoi/snapshot/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_records"] == 150
        # No baseline yet: everything is new and the pointers are null.
        assert body["records_since_last_snapshot"] == 150
        assert body["last_snapshot_id"] is None


class TestCatalogSegment:
    def test_its_is_accepted(self, client, override_es):
        resp = client.post(
            "/v1/its/snapshot", json={"selection": {"created_after": "2026-06-12T00:00:00Z"}}
        )
        assert resp.status_code == 202

    def test_unknown_catalog_is_rejected(self, client, override_es):
        # Typed as a literal so an unrecognised segment is refused up front
        # rather than resolving to whichever index the fallback picks.
        resp = client.post(
            "/v1/nope/snapshot", json={"selection": {"created_after": "2026-06-12T00:00:00Z"}}
        )
        assert resp.status_code == 422

    def test_unprefixed_path_means_aoi(self, client, override_es):
        # Kept for callers written before the segment existed; it has to reach
        # the AOI index, not merely return 202.
        resp = client.post(
            "/v1/snapshot", json={"selection": {"created_after": "2026-06-12T00:00:00Z"}}
        )
        assert resp.status_code == 202
        override_es.open_pit.assert_awaited_once_with(index=settings.es_index)

    def test_catalog_cannot_be_smuggled_in_as_a_query_param(self, client, override_es):
        # The alias is a route, not a parameter default. Had it been a default,
        # FastAPI would have made `catalog` a query parameter and this would
        # have selected the Smart City index from the unprefixed path.
        resp = client.post(
            "/v1/snapshot?catalog=its",
            json={"selection": {"created_after": "2026-06-12T00:00:00Z"}},
        )
        assert resp.status_code == 202
        override_es.open_pit.assert_awaited_once_with(index=settings.es_index)

    def test_unprefixed_stats_and_status_resolve(self, client, override_es, mock_storage):
        assert client.get("/v1/snapshot/stats").status_code in (200, 404)
        assert client.get("/v1/snapshot/some-id").status_code in (200, 404)
