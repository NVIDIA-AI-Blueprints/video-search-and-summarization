import json

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

from backend.api.snapshot_schemas import SnapshotRequest
from backend.api.snapshot_service import (
    EXPIRED_AT_FIELD,
    INGESTED_AT_FIELD,
    SnapshotEmptySelectionError,
    SnapshotFanoutError,
    SnapshotSelectionError,
    compile_selection,
    create_snapshot,
    get_catalog_stats,
    get_snapshot,
    latest_snapshot_key,
    request_key,
    snapshot_key,
)
from backend.shared.catalog import SELECTION_FIELDS
from backend.config import settings

TEMPLATE_DIR = (
    Path(__file__).resolve().parents[2] / "deploy/docker/config/database/elasticsearch/templates"
)
# Container types a term filter cannot address, so nothing under them is a
# legal selection key.
UNSELECTABLE_TYPES = {"nested", "geo_point"}


def mapped_paths(properties: dict, prefix: str = "") -> set[str]:
    """Dotted paths of every selectable leaf under an ES ``properties`` block."""
    paths: set[str] = set()
    for name, mapping in properties.items():
        if mapping.get("type") in UNSELECTABLE_TYPES:
            continue
        if "properties" in mapping:
            paths |= mapped_paths(mapping["properties"], f"{prefix}{name}.")
        else:
            paths.add(f"{prefix}{name}")
    return paths


class TestCompileSelection:
    def test_created_after_maps_to_timestamp_range(self):
        q = compile_selection({"created_after": "2026-06-12T00:00:00Z"}, "aoi")
        assert q == {
            "bool": {
                "filter": [
                    {"range": {"metadata.content_metadata.timestamp": {"gte": "2026-06-12T00:00:00Z"}}}
                ],
                "must_not": [
                    {"term": {"metadata.content_metadata.tag": "kpi"}},
                    {"term": {"metadata.content_metadata.tag": "kpi-golden"}},
                ],
            }
        }

    def test_always_excludes_kpi_tags(self):
        q = compile_selection({"created_after": "t"}, "aoi")
        assert {"term": {"metadata.content_metadata.tag": "kpi"}} in q["bool"]["must_not"]
        assert {"term": {"metadata.content_metadata.tag": "kpi-golden"}} in q["bool"]["must_not"]

    def test_timestamp_range_is_always_first(self):
        q = compile_selection({"created_after": "t", "machineId": "M-1"}, "aoi")
        assert "range" in q["bool"]["filter"][0]

    def test_scalar_passthrough_becomes_term(self):
        q = compile_selection({"created_after": "t", "machineId": "M-1"}, "aoi")
        assert {"term": {"metadata.content_metadata.machineId": "M-1"}} in q["bool"]["filter"]

    def test_list_passthrough_becomes_terms(self):
        q = compile_selection({"created_after": "t", "jobType": ["insp", "repair"]}, "aoi")
        assert {"terms": {"metadata.content_metadata.jobType": ["insp", "repair"]}} in q["bool"]["filter"]


class TestSelectionFieldValidation:
    """The indices are dynamic:strict, so a key the catalog does not map compiles
    to a filter that matches nothing instead of failing — hence the allow-list."""

    def test_its_selection_leaves_out_documents_hidden_by_retention(self):
        q = compile_selection({"created_after": "1970-01-01T00:00:00Z"}, "its")
        assert {"exists": {"field": EXPIRED_AT_FIELD}} in q["bool"]["must_not"]
        # KPI tags stay excluded as before.
        assert {"term": {"metadata.content_metadata.tag": "kpi"}} in q["bool"]["must_not"]

    def test_aoi_selection_is_unchanged_by_retention(self):
        q = compile_selection({"created_after": "1970-01-01T00:00:00Z"}, "aoi")
        assert q["bool"]["must_not"] == [
            {"term": {"metadata.content_metadata.tag": "kpi"}},
            {"term": {"metadata.content_metadata.tag": "kpi-golden"}},
        ]

    def test_its_selection_compiles_to_range_and_terms(self):
        q = compile_selection(
            {
                "created_after": "2026-06-12T00:00:00Z",
                "tag": "its-train",
                "sensorId": "cam-1",
                "sensor.id": "cam-1",
                "objectTypes": ["car", "bus"],
            },
            "its",
        )
        assert q["bool"]["filter"][0] == {
            "range": {"metadata.content_metadata.timestamp": {"gte": "2026-06-12T00:00:00Z"}}
        }
        assert {"term": {"metadata.content_metadata.tag": "its-train"}} in q["bool"]["filter"]
        assert {"term": {"metadata.content_metadata.sensorId": "cam-1"}} in q["bool"]["filter"]
        # A dotted nested path is passed through verbatim, not rejected.
        assert {"term": {"metadata.content_metadata.sensor.id": "cam-1"}} in q["bool"]["filter"]
        assert {
            "terms": {"metadata.content_metadata.objectTypes": ["car", "bus"]}
        } in q["bool"]["filter"]

    @pytest.mark.parametrize("bad", ["cameras", "sites", "event_class", "sample_fps", "limit"])
    def test_unmapped_key_is_rejected(self, bad):
        with pytest.raises(SnapshotSelectionError, match=bad):
            compile_selection({"created_after": "t", bad: "x"}, "its")

    def test_allow_list_is_per_catalog(self):
        # machineId is an AOI field only; the ITS template never maps it.
        compile_selection({"created_after": "t", "machineId": "M-1"}, "aoi")
        with pytest.raises(SnapshotSelectionError, match="machineId"):
            compile_selection({"created_after": "t", "machineId": "M-1"}, "its")

    def test_the_retention_field_is_not_selectable(self):
        # Every ITS selection leaves out the documents that carry expiredAt, so
        # a filter on it could only match nothing.
        with pytest.raises(SnapshotSelectionError, match="expiredAt"):
            compile_selection({"created_after": "t", "expiredAt": "x"}, "its")

    def test_every_offending_key_is_named(self):
        with pytest.raises(SnapshotSelectionError) as exc:
            compile_selection({"created_after": "t", "cameras": "c", "limit": 5}, "its")
        assert "cameras" in str(exc.value)
        assert "limit" in str(exc.value)

    @pytest.mark.parametrize(
        "catalog,template",
        [("aoi", "aoi-inspection-template.json"), ("its", "sc-template.json")],
    )
    def test_allow_list_is_the_set_the_index_template_declares(self, catalog, template):
        # SELECTION_FIELDS is a hand-copy of the templates, and a copy nobody
        # re-reads drifts both ways: a new facet starts getting rejected, or a
        # dropped one keeps being blessed into a filter that matches nothing.
        mappings = json.loads((TEMPLATE_DIR / template).read_text())["template"]["mappings"]
        content_metadata = mappings["properties"]["metadata"]["properties"]["content_metadata"]
        # created_after is an API-level key, not a mapped field; expiredAt is
        # mapped but only the retention pass sets it.
        assert mapped_paths(content_metadata["properties"]) - {"expiredAt"} == set(
            SELECTION_FIELDS[catalog]
        ) - {"created_after"}


@pytest.fixture
def mock_es():
    es = MagicMock()
    es.open_pit = AsyncMock(return_value="PIT-123")
    # create_snapshot counts the selection before opening the PIT; cases about
    # the count override this.
    es.catalog_totals = AsyncMock(return_value=(42, 4200))
    return es


@pytest.fixture
def mock_kafka():
    kafka = MagicMock()
    kafka.send = AsyncMock()
    return kafka


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.put_bytes = AsyncMock()
    storage.get_bytes = AsyncMock()
    storage.exists = AsyncMock(return_value=False)
    storage.get_object = AsyncMock(return_value=None)
    return storage


@pytest.mark.asyncio
async def test_generates_snapshot_id_when_absent(mock_es, mock_kafka, mock_storage):
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
    sid = await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    assert sid and isinstance(sid, str)


@pytest.mark.asyncio
async def test_trusts_caller_snapshot_id(mock_es, mock_kafka, mock_storage):
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"}, snapshot_id="caller-123")
    sid = await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    assert sid == "caller-123"


@pytest.mark.asyncio
async def test_pit_opened_exactly_once(mock_es, mock_kafka, mock_storage):
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
    await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    mock_es.open_pit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fans_out_one_message_per_partition(mock_es, mock_kafka, mock_storage):
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
    await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    assert mock_kafka.send.await_count == settings.snapshot_partitions
    # Sends are concurrent (asyncio.gather), so completion order is not guaranteed.
    partitions = sorted(c.kwargs["partition"] for c in mock_kafka.send.await_args_list)
    keys = sorted(c.kwargs["key"] for c in mock_kafka.send.await_args_list)
    assert partitions == list(range(settings.snapshot_partitions))
    assert keys == sorted(str(i) for i in range(settings.snapshot_partitions))


@pytest.mark.asyncio
async def test_failed_send_closes_pit_and_raises(mock_es, mock_kafka, mock_storage):
    mock_es.close_pit = AsyncMock()

    def send_side_effect(work, topic=None, key=None, partition=None):
        if partition == 1:
            raise RuntimeError("broker down")

    mock_kafka.send = AsyncMock(side_effect=send_side_effect)
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})

    with pytest.raises(SnapshotFanoutError):
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")

    mock_es.close_pit.assert_awaited_once_with("PIT-123")


@pytest.mark.asyncio
async def test_pit_close_failure_does_not_mask_fanout_error(mock_es, mock_kafka, mock_storage):
    mock_es.close_pit = AsyncMock(side_effect=Exception("es unreachable"))
    mock_kafka.send = AsyncMock(side_effect=RuntimeError("broker down"))
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})

    # The original fan-out failure surfaces even though PIT close also failed.
    with pytest.raises(SnapshotFanoutError):
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")


@pytest.mark.asyncio
async def test_success_does_not_close_pit(mock_es, mock_kafka, mock_storage):
    mock_es.close_pit = AsyncMock()
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
    await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    mock_es.close_pit.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_slices_share_one_pit_and_carry_slice_descriptor(mock_es, mock_kafka, mock_storage):
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
    await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    works = [c.args[0] for c in mock_kafka.send.await_args_list]
    assert {w["pit_id"] for w in works} == {"PIT-123"}
    # Every slice descriptor 0..N-1 is present, each with max=N (order-independent).
    assert sorted(w["slice"]["id"] for w in works) == list(range(settings.snapshot_partitions))
    assert all(w["slice"]["max"] == settings.snapshot_partitions for w in works)
    # topic override targets the snapshot-slices topic
    assert all(c.kwargs["topic"] == settings.snapshot_slices_topic for c in mock_kafka.send.await_args_list)


@pytest.mark.asyncio
async def test_writes_request_marker_on_success(mock_es, mock_kafka, mock_storage):
    req = SnapshotRequest(
        selection={"created_after": "2026-06-12T00:00:00Z"}, snapshot_id="snap-1"
    )
    await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    mock_storage.put_bytes.assert_awaited_once()
    key = mock_storage.put_bytes.call_args.args[0]
    payload = json.loads(mock_storage.put_bytes.call_args.args[1])
    assert key == request_key("snap-1")
    assert payload["snapshot_id"] == "snap-1"
    assert payload["slice_count"] == settings.snapshot_partitions
    assert payload["requested_at"].endswith("Z")


@pytest.mark.asyncio
async def test_no_request_marker_on_fanout_failure(mock_es, mock_kafka, mock_storage):
    mock_es.close_pit = AsyncMock()
    mock_kafka.send = AsyncMock(side_effect=RuntimeError("broker down"))
    req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
    with pytest.raises(SnapshotFanoutError):
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
    mock_storage.put_bytes.assert_not_awaited()


class TestEmptySelection:
    """A selection whose values match nothing used to produce a READY snapshot
    with 0 records, which the consumer then trained on."""

    @pytest.mark.asyncio
    async def test_zero_count_raises(self, mock_es, mock_kafka, mock_storage):
        mock_es.catalog_totals = AsyncMock(return_value=(0, 0))
        req = SnapshotRequest(
            selection={"created_after": "2026-06-12T00:00:00Z", "tag": "its-small-200b"}
        )
        with pytest.raises(SnapshotEmptySelectionError, match="its-small-200b"):
            await create_snapshot(mock_es, mock_kafka, mock_storage, req, "its")

    @pytest.mark.asyncio
    async def test_zero_count_never_opens_a_pit(self, mock_es, mock_kafka, mock_storage):
        mock_es.catalog_totals = AsyncMock(return_value=(0, 0))
        req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
        with pytest.raises(SnapshotEmptySelectionError):
            await create_snapshot(mock_es, mock_kafka, mock_storage, req, "its")
        # No PIT to leak, no work items, no _request.json marker.
        mock_es.open_pit.assert_not_awaited()
        mock_kafka.send.assert_not_awaited()
        mock_storage.put_bytes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_count_runs_against_the_catalog_index(self, mock_es, mock_kafka, mock_storage):
        # A Smart City deployment that has not indexed a frame has no sc_* index
        # at all; that must read as zero, not as a 404 from Elasticsearch.
        req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "its")
        assert mock_es.catalog_totals.await_args.kwargs == {
            "index": settings.sc_es_index,
            "allow_missing": True,
        }

    @pytest.mark.asyncio
    async def test_its_selection_opens_the_pit_on_the_its_index(
        self, mock_es, mock_kafka, mock_storage
    ):
        req = SnapshotRequest(
            selection={
                "created_after": "2026-06-12T00:00:00Z",
                "tag": "its-train",
                "sensorId": "cam-1",
                "objectTypes": ["car", "bus"],
            }
        )
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "its")
        mock_es.open_pit.assert_awaited_once_with(index=settings.sc_es_index)


@pytest.mark.asyncio
async def test_get_snapshot_returns_finalized_document(mock_storage):
    doc = {"snapshot_id": "s1", "status": "ready", "total_records": 42}
    mock_storage.exists = AsyncMock(return_value=True)  # snapshot.json present
    mock_storage.get_bytes = AsyncMock(return_value=json.dumps(doc).encode())
    result = await get_snapshot(mock_storage, "s1")
    assert result == doc
    mock_storage.get_bytes.assert_awaited_once_with(snapshot_key("s1"))


@pytest.mark.asyncio
async def test_get_snapshot_creating_from_request_marker(mock_storage):
    async def _exists(key):
        return key == request_key("s1")  # only _request.json exists

    mock_storage.exists = AsyncMock(side_effect=_exists)
    mock_storage.get_bytes = AsyncMock(
        return_value=json.dumps(
            {"snapshot_id": "s1", "requested_at": "2026-07-16T00:00:00Z", "slice_count": 3}
        ).encode()
    )
    result = await get_snapshot(mock_storage, "s1")
    assert result["status"] == "creating"
    assert result["requested_at"] == "2026-07-16T00:00:00Z"
    assert result["manifests"] == []


@pytest.mark.asyncio
async def test_get_snapshot_returns_none_when_unknown(mock_storage):
    mock_storage.exists = AsyncMock(return_value=False)  # neither object exists
    result = await get_snapshot(mock_storage, "nope")
    assert result is None


@pytest.mark.asyncio
async def test_get_catalog_stats_computes_since_last(mock_es, mock_storage):
    latest = {
        "snapshot_id": "prev1",
        "created_at": "2026-07-01T00:00:00Z",
        "query": {"bool": {"filter": []}},
        "total_records": 100,
        "total_bytes": 1000,
    }
    mock_storage.exists = AsyncMock(return_value=True)  # latest_snapshot.json present
    mock_storage.get_bytes = AsyncMock(return_value=json.dumps(latest).encode())
    mock_es.catalog_totals = AsyncMock(return_value=(150, 1500))  # current live totals

    stats = await get_catalog_stats(mock_es, mock_storage, "aoi")

    assert stats["status"] == "live"
    assert stats["total_records"] == 150
    assert stats["total_bytes"] == 1500
    assert stats["records_since_last_snapshot"] == 50
    assert stats["bytes_since_last_snapshot"] == 500
    assert stats["last_snapshot_id"] == "prev1"
    assert stats["last_snapshot_created_at"] == "2026-07-01T00:00:00Z"
    # the last snapshot's query is what gets re-run against ES, against the
    # index of the catalog asked for
    mock_es.catalog_totals.assert_awaited_once_with(
        {"bool": {"filter": []}}, index="aoi_*", allow_missing=False
    )


@pytest.mark.asyncio
async def test_get_catalog_stats_live_totals_when_no_snapshot(mock_es, mock_storage):
    """Before the first snapshot there is no baseline, so the whole catalog counts
    as new rather than the endpoint reporting nothing."""
    mock_storage.exists = AsyncMock(return_value=False)  # no latest_snapshot.json
    mock_es.catalog_totals = AsyncMock(return_value=(150, 1500))

    stats = await get_catalog_stats(mock_es, mock_storage, "aoi")

    assert stats["status"] == "live"
    assert stats["total_records"] == 150
    assert stats["total_bytes"] == 1500
    # No baseline -> every record is new and the pointers are null.
    assert stats["records_since_last_snapshot"] == 150
    assert stats["bytes_since_last_snapshot"] == 1500
    assert stats["last_snapshot_id"] is None
    assert stats["last_snapshot_created_at"] is None
    # The population counted is the one a snapshot would take: KPI-set tags out.
    mock_es.catalog_totals.assert_awaited_once_with(
        {
            "bool": {
                "must_not": [
                    {"term": {"metadata.content_metadata.tag": "kpi"}},
                    {"term": {"metadata.content_metadata.tag": "kpi-golden"}},
                ]
            }
        },
        index="aoi_*",
        allow_missing=False,
    )


class TestCatalogRouting:
    """The catalog segment decides which index is read and which baseline the
    deltas are measured against. Getting either wrong produces a plausible
    number rather than an error, so both are asserted directly."""

    @pytest.mark.asyncio
    async def test_aoi_reads_the_aoi_index(self, mock_es, mock_kafka, mock_storage):
        req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "aoi")
        mock_es.open_pit.assert_awaited_once_with(index=settings.es_index)

    @pytest.mark.asyncio
    async def test_its_reads_the_smart_city_index(self, mock_es, mock_kafka, mock_storage):
        req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "its")
        mock_es.open_pit.assert_awaited_once_with(index=settings.sc_es_index)

    @pytest.mark.asyncio
    async def test_every_work_item_carries_the_catalog(self, mock_es, mock_kafka, mock_storage):
        # The worker picks its latest-snapshot pointer from this field; a slice
        # that omits it would promote an ITS snapshot over the AOI baseline.
        req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "its")
        sent = [call.args[0] for call in mock_kafka.send.await_args_list]
        assert sent, "no work items were published"
        assert all(w["catalog"] == "its" for w in sent)

    @pytest.mark.asyncio
    async def test_request_marker_records_the_catalog(self, mock_es, mock_kafka, mock_storage):
        req = SnapshotRequest(selection={"created_after": "2026-06-12T00:00:00Z"})
        await create_snapshot(mock_es, mock_kafka, mock_storage, req, "its")
        marker = json.loads(mock_storage.put_bytes.await_args.args[1])
        assert marker["catalog"] == "its"

    def test_latest_pointer_is_per_catalog(self):
        # AOI keeps the original key so an existing baseline survives the
        # upgrade; anything else is qualified and cannot collide with it.
        assert latest_snapshot_key("aoi") == f"{settings.snapshot_prefix}/latest_snapshot.json"
        assert latest_snapshot_key("its") != latest_snapshot_key("aoi")

    @pytest.mark.asyncio
    async def test_its_stats_use_the_its_baseline_and_index(self, mock_es, mock_storage):
        latest = {"snapshot_id": "p", "created_at": "t", "query": {"match_all": {}},
                  "total_records": 10, "total_bytes": 100}
        mock_storage.exists = AsyncMock(return_value=True)
        mock_storage.get_bytes = AsyncMock(return_value=json.dumps(latest).encode())
        mock_es.catalog_totals = AsyncMock(side_effect=[(12, 120), (2, 20)])

        stats = await get_catalog_stats(mock_es, mock_storage, "its")

        assert stats["total_records"] == 12
        assert stats["records_since_last_snapshot"] == 2
        mock_storage.exists.assert_awaited_with(latest_snapshot_key("its"))
        # A Smart City deployment that has not indexed a frame yet has no sc_*
        # index at all; that must read as zero, not as a 404 from Elasticsearch.
        assert mock_es.catalog_totals.await_args_list[0] == call(
            {"match_all": {}}, index=settings.sc_es_index, allow_missing=True
        )


class TestItsSinceLastSnapshot:
    """ITS deltas count what was ingested after the last snapshot was requested.

    Retention deletes old frames, so the live total can fall below the baseline;
    "live minus baseline" would then report fewer new records than arrived.
    """

    LATEST = {
        "snapshot_id": "snap-1",
        "created_at": "2026-09-01T00:10:00Z",
        "query": {"bool": {"filter": [{"term": {"metadata.content_metadata.sensorId": "cam"}}]}},
        "total_records": 1000,
        "total_bytes": 10_000,
    }
    MARKER = {"snapshot_id": "snap-1", "requested_at": "2026-09-01T00:00:00Z"}

    @pytest.fixture
    def storage(self, mock_storage):
        mock_storage.exists = AsyncMock(return_value=True)
        mock_storage.get_bytes = AsyncMock(return_value=json.dumps(self.LATEST).encode())
        mock_storage.get_object = AsyncMock(return_value=json.dumps(self.MARKER).encode())
        return mock_storage

    @pytest.mark.asyncio
    async def test_deltas_count_records_ingested_after_the_request(self, mock_es, storage):
        # 400 old frames were deleted and 50 new ones arrived: live is 650.
        mock_es.catalog_totals = AsyncMock(side_effect=[(650, 6500), (50, 500)])

        stats = await get_catalog_stats(mock_es, storage, "its")

        assert stats["total_records"] == 650
        assert stats["records_since_last_snapshot"] == 50
        assert stats["bytes_since_last_snapshot"] == 500
        storage.get_object.assert_awaited_once_with(request_key("snap-1"))
        since_query = mock_es.catalog_totals.await_args_list[1].args[0]
        assert since_query == {
            "bool": {
                "filter": [
                    self.LATEST["query"],
                    {"range": {INGESTED_AT_FIELD: {"gte": "2026-09-01T00:00:00Z"}}},
                ]
            }
        }
        assert mock_es.catalog_totals.await_args_list[1].kwargs == {
            "index": settings.sc_es_index,
            "allow_missing": True,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "read",
        [
            {"return_value": None},
            {"return_value": b"not json"},
            {"return_value": b"{}"},
            {"side_effect": ConnectionError("object store unreachable")},
        ],
        ids=["missing", "not-json", "no-requested-at", "read-error"],
    )
    async def test_without_a_marker_counts_since_the_snapshot_was_finalised(
        self, mock_es, storage, read
    ):
        """Never the baseline difference, which goes negative once frames are
        deleted and would stall the trigger."""
        storage.get_object = AsyncMock(**read)
        mock_es.catalog_totals = AsyncMock(side_effect=[(650, 6500), (40, 400)])

        stats = await get_catalog_stats(mock_es, storage, "its")

        assert stats["records_since_last_snapshot"] == 40
        since_query = mock_es.catalog_totals.await_args_list[1].args[0]
        assert since_query["bool"]["filter"][1] == {
            "range": {INGESTED_AT_FIELD: {"gte": self.LATEST["created_at"]}}
        }

    @pytest.mark.asyncio
    async def test_aoi_keeps_the_baseline_difference(self, mock_es, storage):
        mock_es.catalog_totals = AsyncMock(return_value=(1200, 12_000))

        stats = await get_catalog_stats(mock_es, storage, "aoi")

        assert stats["records_since_last_snapshot"] == 200
        storage.get_object.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_baseline_counts_everything_without_reading_a_marker(self, mock_es, storage):
        storage.exists = AsyncMock(return_value=False)
        mock_es.catalog_totals = AsyncMock(return_value=(70, 700))

        stats = await get_catalog_stats(mock_es, storage, "its")

        assert stats["records_since_last_snapshot"] == 70
        storage.get_object.assert_not_awaited()
