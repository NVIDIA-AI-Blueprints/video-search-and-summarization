"""Retention pass for archived Smart City frames (backend.api.sc_gc).

The fakes below keep the catalog and the bucket in memory and answer the few
query shapes the pass sends, so each test states the stored state and checks
what is left afterwards rather than which calls were made.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from backend.api import sc_gc
from backend.api.sc_gc import run_gc_pass
from backend.config import Settings, settings

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
HOLD = timedelta(hours=48)
LATER = NOW + HOLD + timedelta(minutes=1)  # the first pass after the hold
OLD = NOW - timedelta(days=30)
RECENT = NOW - timedelta(days=1)
SNAPSHOT_REQUESTED = NOW - timedelta(days=7)


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def key(name: str) -> str:
    return f"{settings.its_object_prefix}/0123456789abcdef/{name}.jpg"


# ------------------------------------------------------------------ fakes


def _get(doc: dict, dotted: str):
    value = doc
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(doc_id: str, source: dict, query: dict) -> bool:
    (kind, body), = query.items()
    if kind == "match_all":
        return True
    if kind == "bool":
        return all(_matches(doc_id, source, q) for q in body.get("filter", [])) and not any(
            _matches(doc_id, source, q) for q in body.get("must_not", [])
        )
    if kind == "exists":
        return _get(source, body["field"]) is not None
    if kind == "ids":
        return doc_id in body["values"]
    (field, arg), = body.items()
    value = _get(source, field)
    if kind == "term":
        return value == arg
    if kind == "terms":
        return value in arg
    if kind == "prefix":
        return isinstance(value, str) and value.startswith(arg)
    if kind == "range":
        if value is None:
            return False
        ok = True
        if "lt" in arg:
            ok &= value < arg["lt"]
        if "gt" in arg:
            ok &= value > arg["gt"]
        return ok
    raise AssertionError(f"unsupported query {query}")


def _merge(target: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict):
            _merge(target.setdefault(k, {}), v)
        elif v is None:
            target.pop(k, None)
        else:
            target[k] = v


class FakeCatalog:
    """sc_* documents with per-document sequence numbers."""

    def __init__(self, events: list[str]):
        self.docs: dict[str, dict] = {}
        self.seq: dict[str, int] = {}
        self.events = events
        self.fail_delete: set[str] = set()
        self.pits: dict[str, list[str]] = {}
        self.mapped: list[tuple[str, str]] = []
        self.incomplete = False

    def add(self, doc_id, *, path, ingested, tag="its-train", size=100, expired=None):
        cm = {"objStorePath": path, "imageSize": size, "ingestedAt": iso(ingested)}
        if tag is not None:
            cm["tag"] = tag
        if expired is not None:
            cm["expiredAt"] = iso(expired)
        self.docs[doc_id] = {"metadata": {"content_metadata": cm}}
        self.seq[doc_id] = self.seq.get(doc_id, 0) + 1

    def cm(self, doc_id):
        return self.docs[doc_id]["metadata"]["content_metadata"]

    async def ensure_date_field(self, index, field):
        self.mapped.append((index, field))

    async def catalog_totals(self, query, index=None, *, allow_missing=False):
        hits = [d for i, d in self.docs.items() if _matches(i, d, query)]
        return len(hits), sum(_get(d, "metadata.content_metadata.imageSize") for d in hits)

    async def open_pit(self, index=None, keep_alive=None):
        pit = f"pit-{len(self.pits)}"
        self.pits[pit] = list(self.docs)
        return pit

    async def close_pit(self, pit_id):
        self.pits.pop(pit_id)

    async def search_pit_page(self, pit_id, query, *, size, search_after=None, source_includes=None):
        start = search_after[0] + 1 if search_after else 0
        page = []
        for pos, doc_id in enumerate(self.pits[pit_id]):
            if pos < start or doc_id not in self.docs:
                continue
            if not _matches(doc_id, self.docs[doc_id], query):
                continue
            page.append(
                {
                    "_index": "sc_cam",
                    "_id": doc_id,
                    "_seq_no": self.seq[doc_id],
                    "_primary_term": 1,
                    "_source": json.loads(json.dumps(self.docs[doc_id])),
                    "sort": [pos],
                }
            )
            if len(page) == size:
                break
        return page

    async def update_hits(self, hits, doc):
        kept = set()
        for hit in hits:
            doc_id = hit["_id"]
            if doc_id not in self.docs or self.seq[doc_id] != hit["_seq_no"]:
                kept.add(doc_id)
                continue
            _merge(self.docs[doc_id], json.loads(json.dumps(doc)))
            self.seq[doc_id] += 1
            self.events.append(f"es-update:{doc_id}")
        return kept

    async def delete_hits(self, hits):
        kept = set()
        for hit in hits:
            doc_id = hit["_id"]
            if doc_id not in self.docs:
                continue
            if doc_id in self.fail_delete or self.seq[doc_id] != hit["_seq_no"]:
                kept.add(doc_id)
                continue
            del self.docs[doc_id]
            self.events.append(f"es:{doc_id}")
        return kept

    async def referenced_paths(self, index, paths, exclude_ids=None):
        if self.incomplete:
            raise LookupError("search over sc_* was incomplete: {'failed': 1}")
        exclude = set(exclude_ids or ())
        live = {
            _get(d, "metadata.content_metadata.objStorePath")
            for i, d in self.docs.items()
            if i not in exclude
        }
        return set(paths) & live


class FakeBucket:
    def __init__(self, events: list[str], page_size: int = 2):
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.events = events
        self.page_size = page_size
        self.fail_delete: set[str] = set()
        self.lifecycle: list[dict] = []

    def put(self, k, data=b"x", modified=OLD):
        self.objects[k] = (data, modified)

    async def list_page(self, prefix, continuation_token=None):
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        start = int(continuation_token or 0)
        page = keys[start : start + self.page_size]
        nxt = start + self.page_size
        token = str(nxt) if nxt < len(keys) else None
        return [
            {"key": k, "last_modified": self.objects[k][1], "size": len(self.objects[k][0])}
            for k in page
        ], token

    async def get_object(self, k):
        return self.objects[k][0] if k in self.objects else None

    async def last_modified(self, k):
        return self.objects[k][1] if k in self.objects else None

    async def get_lifecycle_rules(self):
        return list(self.lifecycle)

    async def put_lifecycle_rules(self, rules):
        self.lifecycle = list(rules)

    async def delete_many(self, keys):
        failed = []
        for k in keys:
            if k in self.fail_delete:
                failed.append(k)
                continue
            self.objects.pop(k, None)
            self.events.append(f"s3:{k}")
        return failed


@pytest.fixture
def events():
    return []


@pytest.fixture
def es(events):
    return FakeCatalog(events)


@pytest.fixture
def s3(events):
    bucket = FakeBucket(events)
    # The latest ITS snapshot, requested a week ago.
    bucket.put(
        "snapshots/latest_snapshot_its.json",
        json.dumps(
            {"snapshot_id": "s1", "created_at": iso(SNAPSHOT_REQUESTED + timedelta(minutes=5))}
        ).encode(),
    )
    bucket.put(
        "snapshots/s1/_request.json",
        json.dumps({"requested_at": iso(SNAPSHOT_REQUESTED), "catalog": "its"}).encode(),
        modified=SNAPSHOT_REQUESTED,
    )
    return bucket


@pytest.fixture(autouse=True)
def gc_settings(monkeypatch):
    monkeypatch.setattr(settings, "sc_gc_retention_days", 14.0)
    monkeypatch.setattr(settings, "sc_gc_require_snapshot", True)
    monkeypatch.setattr(settings, "sc_gc_hold_s", int(HOLD.total_seconds()))
    monkeypatch.setattr(settings, "sc_gc_batch_size", 2)
    monkeypatch.setattr(settings, "sc_gc_max_deletes_per_pass", 1000)
    monkeypatch.setattr(settings, "sc_gc_dry_run", False)
    monkeypatch.setattr(settings, "sc_gc_orphan_sweep", False)
    monkeypatch.setattr(settings, "sc_gc_orphan_grace_s", 24 * 3600)
    monkeypatch.setattr(settings, "sc_gc_orphan_max_ratio", 0.2)
    monkeypatch.setattr(settings, "seaweedfs_master_url", "")
    monkeypatch.setattr(settings, "sc_gc_prune_snapshots", True)
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", False)
    sc_gc._kpi_manifest_paths.clear()


def frame(es, s3, name, *, ingested=OLD, tag="its-train", size=100, path=None, expired=None):
    path = path or key(name)
    es.add(name, path=path, ingested=ingested, tag=tag, size=size, expired=expired)
    s3.put(path, modified=ingested)
    return path


async def hide_then_delete(es, s3, **kwargs):
    first = await run_gc_pass(es, s3, now=NOW, **kwargs)
    second = await run_gc_pass(es, s3, now=LATER, **kwargs)
    return first, second


# ------------------------------------------------------------ hide / delete


@pytest.mark.asyncio
async def test_first_pass_only_hides_old_frames(es, s3):
    old = frame(es, s3, "old")
    recent = frame(es, s3, "recent", ingested=RECENT)

    report = await run_gc_pass(es, s3, now=NOW)

    assert es.cm("old")["expiredAt"] == iso(NOW)
    assert "expiredAt" not in es.cm("recent")
    assert old in s3.objects and recent in s3.objects
    assert report.hidden == 1 and report.documents_deleted == 0
    assert es.mapped == [("sc_*", "expiredAt")]


@pytest.mark.asyncio
async def test_hidden_frames_go_after_the_hold(es, s3):
    old = [frame(es, s3, f"old{i}", size=10 * (i + 1)) for i in range(3)]
    recent = frame(es, s3, "recent", ingested=RECENT)

    _, report = await hide_then_delete(es, s3)

    assert set(es.docs) == {"recent"}
    assert not any(k in s3.objects for k in old)
    assert recent in s3.objects
    assert report.documents_deleted == 3
    assert report.objects_deleted == 3
    assert report.bytes_deleted == 10 + 20 + 30


@pytest.mark.asyncio
async def test_hidden_frames_stay_during_the_hold(es, s3):
    path = frame(es, s3, "old")

    await run_gc_pass(es, s3, now=NOW)
    report = await run_gc_pass(es, s3, now=NOW + HOLD - timedelta(minutes=1))

    assert "old" in es.docs and path in s3.objects
    assert report.documents_deleted == 0


@pytest.mark.asyncio
async def test_a_pass_never_deletes_what_it_just_hid(es, s3, monkeypatch):
    monkeypatch.setattr(settings, "sc_gc_hold_s", 0)
    path = frame(es, s3, "old")

    report = await run_gc_pass(es, s3, now=NOW)

    assert "old" in es.docs and path in s3.objects
    assert report.hidden == 1 and report.documents_deleted == 0


@pytest.mark.asyncio
async def test_documents_are_deleted_before_their_objects(es, s3, events):
    frame(es, s3, "a")
    frame(es, s3, "b")

    await hide_then_delete(es, s3)

    assert events.index("es:a") < events.index(f"s3:{key('a')}")
    assert events.index("es:b") < events.index(f"s3:{key('b')}")


@pytest.mark.asyncio
async def test_kpi_tagged_frames_are_never_hidden(es, s3):
    kpi = frame(es, s3, "kpi", tag="kpi")
    golden = frame(es, s3, "golden", tag="kpi-golden")
    frame(es, s3, "mined")

    await hide_then_delete(es, s3)

    assert set(es.docs) == {"kpi", "golden"}
    assert "expiredAt" not in es.cm("kpi") and "expiredAt" not in es.cm("golden")
    assert kpi in s3.objects and golden in s3.objects


@pytest.mark.asyncio
async def test_untagged_old_frames_are_deleted(es, s3):
    frame(es, s3, "untagged", tag=None)

    await hide_then_delete(es, s3)

    assert "untagged" not in es.docs


@pytest.mark.asyncio
async def test_object_shared_with_a_live_document_is_kept(es, s3):
    shared = frame(es, s3, "old")
    es.add("replay", path=shared, ingested=RECENT)

    _, report = await hide_then_delete(es, s3)

    assert "old" not in es.docs
    assert shared in s3.objects
    assert report.objects_still_referenced == 1
    assert report.objects_deleted == 0


@pytest.mark.asyncio
async def test_object_listed_in_a_kpi_manifest_is_kept(es, s3):
    """The KPI copy lost its document to a same-id mining frame, so only the
    manifest still says fine-tuning reads it."""
    path = frame(es, s3, "collided")
    s3.put(
        "kpi/k1/manifest0.jsonl",
        (
            json.dumps({"file_path": path})
            + "\n[1, 2]\n"
            + json.dumps({"file_path": "deft-aoi/x.png"})
        ).encode(),
    )

    _, report = await hide_then_delete(es, s3)

    assert "collided" not in es.docs
    assert path in s3.objects
    assert report.objects_protected == 1


@pytest.mark.asyncio
async def test_object_written_again_after_archival_is_kept(es, s3):
    """The same frame archived anew: the object is fresh and its new document
    may still be on its way through Logstash."""
    path = frame(es, s3, "old")
    await run_gc_pass(es, s3, now=NOW)
    s3.put(path, modified=NOW + timedelta(hours=1))

    report = await run_gc_pass(es, s3, now=LATER)

    assert "old" not in es.docs
    assert path in s3.objects
    assert report.objects_rewritten == 1


@pytest.mark.asyncio
async def test_frame_archived_again_while_hidden_is_made_visible(es, s3):
    path = frame(es, s3, "old")
    await run_gc_pass(es, s3, now=NOW)
    # Logstash upserts the re-archived frame: new ingestedAt, expiredAt kept.
    es.cm("old")["ingestedAt"] = iso(NOW + timedelta(hours=2))
    es.seq["old"] += 1

    report = await run_gc_pass(es, s3, now=LATER)

    assert "old" in es.docs and "expiredAt" not in es.cm("old")
    assert path in s3.objects
    assert report.unhidden == 1 and report.documents_deleted == 0


@pytest.mark.asyncio
async def test_document_rewritten_between_read_and_delete_is_kept(es, s3):
    frame(es, s3, "raced")
    await run_gc_pass(es, s3, now=NOW)
    original = es.delete_hits

    async def delete_after_rewrite(hits):
        es.seq["raced"] += 1
        return await original(hits)

    es.delete_hits = delete_after_rewrite
    report = await run_gc_pass(es, s3, now=LATER)

    assert "raced" in es.docs
    assert key("raced") in s3.objects
    assert report.documents_kept == 1


@pytest.mark.asyncio
async def test_failed_document_delete_keeps_the_object(es, s3):
    path = frame(es, s3, "stuck")
    es.fail_delete.add("stuck")

    await hide_then_delete(es, s3)

    assert "stuck" in es.docs
    assert path in s3.objects


@pytest.mark.asyncio
async def test_failed_object_delete_is_reported_not_counted(es, s3):
    path = frame(es, s3, "a")
    s3.fail_delete.add(path)

    _, report = await hide_then_delete(es, s3)

    assert "a" not in es.docs
    assert path in s3.objects
    assert report.objects_deleted == 0
    assert report.object_delete_errors == 1
    assert report.failed


@pytest.mark.asyncio
async def test_incomplete_reference_answer_stops_before_any_delete(es, s3):
    """A closed or failed sc_ index could hold a reference; nothing may go."""
    path = frame(es, s3, "a")
    await run_gc_pass(es, s3, now=NOW)
    es.incomplete = True

    report = await run_gc_pass(es, s3, now=LATER)

    assert "a" in es.docs and path in s3.objects
    assert any("incomplete" in e for e in report.errors)


@pytest.mark.asyncio
async def test_pass_stops_at_the_budget(es, s3, monkeypatch):
    monkeypatch.setattr(settings, "sc_gc_max_deletes_per_pass", 3)
    for i in range(5):
        frame(es, s3, f"f{i}")

    first, second = await hide_then_delete(es, s3)

    assert first.hidden == 3
    assert second.documents_deleted == 3
    assert len(es.docs) == 2


@pytest.mark.asyncio
async def test_dry_run_changes_nothing(es, s3, monkeypatch):
    monkeypatch.setattr(settings, "seaweedfs_master_url", "http://master:9333")
    frame(es, s3, "hidden", size=7, expired=NOW - HOLD - timedelta(hours=1))
    frame(es, s3, "visible", size=5)
    before_docs = json.dumps(es.docs, sort_keys=True)
    before_objects = dict(s3.objects)

    report = await run_gc_pass(es, s3, now=NOW, dry_run=True)

    assert json.dumps(es.docs, sort_keys=True) == before_docs
    assert s3.objects == before_objects
    assert es.mapped == []
    assert report.documents_deleted == 1 and report.bytes_deleted == 7
    assert report.hidden == 1
    assert report.vacuum is None


@pytest.mark.asyncio
async def test_dry_run_counts_what_the_pass_deletes(es, s3):
    """Objects the pass keeps (still referenced, KPI-listed, written again)
    are not counted as deleted."""
    frame(es, s3, "plain", size=3)
    es.add("replay", path=frame(es, s3, "shared", size=5), ingested=RECENT)
    listed = frame(es, s3, "listed", size=7)
    s3.put("kpi/k1/manifest0.jsonl", json.dumps({"file_path": listed}).encode())
    rewritten = frame(es, s3, "rewritten", size=11)
    await run_gc_pass(es, s3, now=NOW)
    s3.put(rewritten, modified=NOW + timedelta(hours=1))

    dry = await run_gc_pass(es, s3, now=LATER, dry_run=True)
    real = await run_gc_pass(es, s3, now=LATER)

    counts = [
        "documents_deleted",
        "objects_deleted",
        "bytes_deleted",
        "objects_still_referenced",
        "objects_protected",
        "objects_rewritten",
    ]
    assert {c: getattr(dry, c) for c in counts} == {c: getattr(real, c) for c in counts}
    assert real.objects_deleted == 1 and real.bytes_deleted == 3


# ---------------------------------------------------------------- cutoff


@pytest.mark.asyncio
async def test_frames_newer_than_the_latest_snapshot_are_not_hidden(es, s3, monkeypatch):
    """Past the retention age but ingested after the last snapshot request."""
    monkeypatch.setattr(settings, "sc_gc_retention_days", 1.0)
    frame(es, s3, "before", ingested=SNAPSHOT_REQUESTED - timedelta(hours=2))
    frame(es, s3, "margin", ingested=SNAPSHOT_REQUESTED - timedelta(minutes=30))
    frame(es, s3, "after", ingested=SNAPSHOT_REQUESTED + timedelta(days=1))

    report = await run_gc_pass(es, s3, now=NOW)

    assert "expiredAt" in es.cm("before")
    assert "expiredAt" not in es.cm("margin") and "expiredAt" not in es.cm("after")
    assert report.cutoff == iso(SNAPSHOT_REQUESTED - timedelta(hours=1))


@pytest.mark.asyncio
async def test_cutoff_uses_the_earlier_of_marker_and_created_at(es, s3, monkeypatch):
    """A repeated POST with the same snapshot_id rewrites the marker later."""
    monkeypatch.setattr(settings, "sc_gc_retention_days", 1.0)
    s3.put(
        "snapshots/s1/_request.json",
        json.dumps({"requested_at": iso(NOW - timedelta(days=2)), "catalog": "its"}).encode(),
    )

    report = await run_gc_pass(es, s3, now=NOW)

    created = SNAPSHOT_REQUESTED + timedelta(minutes=5)
    assert report.cutoff == iso(created - timedelta(hours=1))


@pytest.mark.asyncio
async def test_without_the_snapshot_requirement_only_age_counts(es, s3, monkeypatch):
    monkeypatch.setattr(settings, "sc_gc_retention_days", 1.0)
    monkeypatch.setattr(settings, "sc_gc_require_snapshot", False)
    frame(es, s3, "after", ingested=SNAPSHOT_REQUESTED + timedelta(days=1))

    await run_gc_pass(es, s3, now=NOW)

    assert "expiredAt" in es.cm("after")


@pytest.mark.asyncio
async def test_a_failed_marker_read_stops_the_pass_before_hiding(es, s3):
    """The cutoff does not fall back to created_at alone, which can sit after
    the snapshot's PIT opened."""
    frame(es, s3, "old")
    read = s3.get_object

    async def get_object(k):
        if k.endswith("/_request.json"):
            raise ConnectionError("object store unreachable")
        return await read(k)

    s3.get_object = get_object
    with pytest.raises(ConnectionError):
        await run_gc_pass(es, s3, now=NOW)
    assert "expiredAt" not in es.cm("old")


@pytest.mark.asyncio
async def test_no_its_snapshot_skips_hiding_but_still_deletes(es, s3):
    del s3.objects["snapshots/latest_snapshot_its.json"]
    frame(es, s3, "fresh-old")
    hidden = frame(es, s3, "hidden", expired=NOW - HOLD - timedelta(hours=1))

    report = await run_gc_pass(es, s3, now=NOW)

    assert "expiredAt" not in es.cm("fresh-old")
    assert "hidden" not in es.docs and hidden not in s3.objects
    assert report.hide_skipped == "no ITS snapshot has been taken yet"


# ------------------------------------------------------------- orphans


@pytest.fixture
def sweep(monkeypatch):
    monkeypatch.setattr(settings, "sc_gc_orphan_sweep", True)


@pytest.mark.asyncio
async def test_orphan_sweep_removes_unreferenced_old_objects(es, s3, sweep):
    live = frame(es, s3, "live", ingested=RECENT)
    orphan = key("orphan")
    young = key("young")
    kept_by_kpi = key("kpi-copy")
    s3.put(orphan)
    s3.put(young, modified=NOW - timedelta(hours=1))
    s3.put(kept_by_kpi)
    s3.put("kpi/k1/manifest0.jsonl", json.dumps({"file_path": kept_by_kpi}).encode())

    report = await run_gc_pass(es, s3, now=NOW)

    assert orphan not in s3.objects
    assert live in s3.objects and young in s3.objects and kept_by_kpi in s3.objects
    assert report.orphans_deleted == 1


@pytest.mark.asyncio
async def test_orphan_sweep_is_off_by_default(es, s3, monkeypatch):
    default = Settings.model_fields["sc_gc_orphan_sweep"].default
    monkeypatch.setattr(settings, "sc_gc_orphan_sweep", default)
    frame(es, s3, "live", ingested=RECENT)
    s3.put(key("orphan"))

    await run_gc_pass(es, s3, now=NOW)

    assert default is False
    assert key("orphan") in s3.objects


@pytest.mark.asyncio
async def test_orphan_sweep_refuses_a_catalog_that_points_elsewhere(es, s3, sweep):
    """With no document under the frame prefix, every object would look like an
    orphan; the sweep must not run."""
    es.add("aoi", path="deft-aoi/abc/board.png", ingested=RECENT)
    s3.put(key("orphan"))

    report = await run_gc_pass(es, s3, now=NOW)

    assert key("orphan") in s3.objects
    assert any("no document" in e for e in report.errors)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["deft-aoi", "snapshots", "kpi", "upload", "models"])
async def test_orphan_sweep_refuses_a_prefix_shared_with_other_data(
    es, s3, sweep, monkeypatch, prefix
):
    monkeypatch.setattr(settings, "its_object_prefix", prefix)
    es.add("its", path=f"{prefix}/abc/f.jpg", ingested=RECENT)
    s3.put(f"{prefix}/zzz/other.bin")

    report = await run_gc_pass(es, s3, now=NOW)

    assert f"{prefix}/zzz/other.bin" in s3.objects
    assert any("overlaps" in e for e in report.errors)


@pytest.mark.asyncio
async def test_orphan_sweep_stops_on_an_incomplete_answer(es, s3, sweep):
    frame(es, s3, "live", ingested=RECENT)
    s3.put(key("orphan"))
    es.incomplete = True

    report = await run_gc_pass(es, s3, now=NOW)

    assert key("orphan") in s3.objects
    assert any("orphan sweep stopped" in e for e in report.errors)


@pytest.mark.asyncio
async def test_orphan_sweep_stops_when_too_much_is_unreferenced(es, s3, sweep):
    """A catalog that does not describe the bucket shows up as a page that is
    mostly unreferenced; that must stop the sweep, not feed it."""
    s3.page_size = 50
    for i in range(10):
        frame(es, s3, f"live{i}", ingested=RECENT - timedelta(days=1))
    for i in range(15):
        s3.put(key(f"orphan{i}"))

    report = await run_gc_pass(es, s3, now=NOW)

    assert all(key(f"orphan{i}") in s3.objects for i in range(15))
    assert any("above SC_GC_ORPHAN_MAX_RATIO" in e for e in report.errors)


def test_the_frame_prefix_is_normalised():
    base = {
        "aws_endpoint": "x",
        "aws_access_key_id": "x",
        "aws_secret_access_key": "x",
        "aws_bucket": "x",
    }
    assert Settings(**base, its_object_prefix="deft-its/").its_object_prefix == "deft-its"
    assert Settings(**base, its_object_prefix="/deft-its").its_object_prefix == "deft-its"
    with pytest.raises(ValueError):
        Settings(**base, its_object_prefix="/")


# ------------------------------------------------------------- vacuum


def _master(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(settings, "seaweedfs_master_url", "http://master:9333/")
    monkeypatch.setattr(
        sc_gc.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


def _volume(vid, *, read_only, size=1000, garbage=0):
    return {"Id": vid, "ReadOnly": read_only, "Size": size, "DeletedByteCount": garbage}


def _status(*volumes):
    return {"Volumes": {"DataCenters": {"dc1": {"rack1": {"srv:8080": list(volumes)}}}}}


@pytest.mark.asyncio
async def test_vacuum_runs_after_a_pass(es, s3, monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/vol/status":
            return httpx.Response(200, json=_status(_volume(1, read_only=False, garbage=900)))
        return httpx.Response(200, json={})

    _master(monkeypatch, handler)
    monkeypatch.setattr(settings, "sc_gc_vacuum_garbage_threshold", 0.1)

    report = await run_gc_pass(es, s3, now=NOW)

    assert calls[0] == ("/vol/vacuum", {"garbageThreshold": "0.1"})
    assert report.vacuum == "ok (HTTP 200)"


@pytest.mark.asyncio
async def test_read_only_volumes_with_garbage_are_reported(es, s3, monkeypatch):
    def handler(request):
        if request.url.path == "/vol/status":
            return httpx.Response(
                200,
                json=_status(
                    _volume(3, read_only=True, garbage=500),
                    _volume(4, read_only=True, garbage=0),
                    _volume(5, read_only=False, garbage=900),
                ),
            )
        return httpx.Response(200, json={})

    _master(monkeypatch, handler)

    report = await run_gc_pass(es, s3, now=NOW)

    assert report.vacuum.startswith("failed: read-only volumes [3]")
    assert report.failed


@pytest.mark.asyncio
async def test_vacuum_failure_is_reported(es, s3, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")

    _master(monkeypatch, handler)

    report = await run_gc_pass(es, s3, now=NOW)

    assert report.vacuum.startswith("failed")
    assert report.failed


@pytest.mark.asyncio
async def test_vacuum_without_a_master_url_is_reported(es, s3):
    report = await run_gc_pass(es, s3, now=NOW)

    assert report.vacuum.startswith("skipped")
    assert not report.failed


# --------------------------------------------------------------- loop


@pytest.mark.asyncio
async def test_loop_survives_a_failed_pass(monkeypatch):
    runs = []

    async def flaky_pass(es, storage):
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("es down")
        if len(runs) == 3:
            raise asyncio.CancelledError
        return sc_gc.GcReport(dry_run=False)

    monkeypatch.setattr(settings, "sc_gc_interval_s", 0)
    monkeypatch.setattr(sc_gc, "run_gc_pass", flaky_pass)

    with pytest.raises(asyncio.CancelledError):
        await sc_gc.gc_loop(None, None)
    assert len(runs) == 3


@pytest.mark.asyncio
async def test_first_pass_comes_soon_after_startup(monkeypatch):
    delays = []
    real_sleep = asyncio.sleep

    async def record(seconds):
        delays.append(seconds)
        await real_sleep(0)
        raise asyncio.CancelledError

    monkeypatch.setattr(settings, "sc_gc_interval_s", 3600)
    monkeypatch.setattr(sc_gc.asyncio, "sleep", record)

    with pytest.raises(asyncio.CancelledError):
        await sc_gc.gc_loop(None, None)
    assert delays == [300]


# ------------------------------------------------------- frame keys only


@pytest.mark.asyncio
async def test_sweep_only_touches_keys_shaped_like_archived_frames(es, s3, sweep):
    """An archive or checkpoint someone stored under the frame prefix is not a
    frame, even though no document points to it."""
    frame(es, s3, "live", ingested=RECENT)
    tarball = f"{settings.its_object_prefix}/kpi-set-v1.tar.gz"
    checkpoint = f"{settings.its_object_prefix}/models/best.pth"
    not_hex = f"{settings.its_object_prefix}/notahexdigest!!/f.jpg"
    for k in (tarball, checkpoint, not_hex):
        s3.put(k)
    s3.put(key("orphan"))

    report = await run_gc_pass(es, s3, now=NOW)

    assert tarball in s3.objects and checkpoint in s3.objects and not_hex in s3.objects
    assert key("orphan") not in s3.objects
    assert report.orphans_deleted == 1


@pytest.mark.asyncio
async def test_a_document_pointing_outside_frame_keys_loses_only_itself(es, s3):
    elsewhere = "upload/run-1/best_model.pth"
    frame(es, s3, "odd", path=elsewhere)

    _, report = await hide_then_delete(es, s3)

    assert "odd" not in es.docs
    assert elsewhere in s3.objects
    assert report.objects_deleted == 0


# ------------------------------------------------------ snapshot pruning


def snapshot_dir(s3, snapshot_id, *, catalog="its", modified=OLD, with_final=True, marker=True):
    base = f"snapshots/{snapshot_id}"
    for i in range(3):
        s3.put(f"{base}/manifest{i}.jsonl", b"{}\n", modified=modified)
        s3.put(f"{base}/_status/slice{i}.json", b"{}", modified=modified)
    body = {} if catalog is None else {"catalog": catalog}
    if with_final:
        s3.put(f"{base}/snapshot.json", json.dumps(body).encode(), modified=modified)
    if marker:
        s3.put(f"{base}/_request.json", json.dumps(body).encode(), modified=modified)
    return base


def dir_exists(s3, base):
    return any(k.startswith(base + "/") for k in s3.objects)


@pytest.mark.asyncio
async def test_old_its_snapshot_directories_are_deleted(es, s3):
    old = snapshot_dir(s3, "old-its")

    report = await run_gc_pass(es, s3, now=NOW)

    assert not dir_exists(s3, old)
    assert report.snapshots_deleted == 1 and report.snapshot_objects_deleted == 8


@pytest.mark.asyncio
async def test_snapshot_pruning_keeps_what_is_still_needed(es, s3):
    latest = "snapshots/s1"  # the latest ITS snapshot (fixture), a week old
    aoi = snapshot_dir(s3, "old-aoi", catalog="aoi")
    silent = snapshot_dir(s3, "old-unknown", catalog=None)
    recent = snapshot_dir(s3, "recent-its", modified=NOW - timedelta(hours=1))
    mixed = snapshot_dir(s3, "mixed-its")
    s3.put(f"{mixed}/manifest0.jsonl", b"{}\n", modified=NOW - timedelta(hours=1))
    marker_only = snapshot_dir(s3, "creating-its", with_final=False, modified=NOW - timedelta(hours=1))

    report = await run_gc_pass(es, s3, now=NOW)

    for base in (latest, aoi, silent, recent, mixed, marker_only):
        assert dir_exists(s3, base), base
    assert "snapshots/latest_snapshot_its.json" in s3.objects
    assert report.snapshots_deleted == 0


@pytest.mark.asyncio
async def test_snapshot_pruning_in_a_dry_run_deletes_nothing(es, s3):
    old = snapshot_dir(s3, "old-its")

    report = await run_gc_pass(es, s3, now=NOW, dry_run=True)

    assert dir_exists(s3, old)
    assert report.snapshots_deleted == 1


@pytest.mark.asyncio
async def test_snapshot_pruning_can_be_switched_off(es, s3, monkeypatch):
    monkeypatch.setattr(settings, "sc_gc_prune_snapshots", False)
    old = snapshot_dir(s3, "old-its")

    await run_gc_pass(es, s3, now=NOW)

    assert dir_exists(s3, old)


# --------------------------------------------------------- lifecycle rule


@pytest.mark.asyncio
async def test_a_pass_syncs_the_lifecycle_rule(es, s3, monkeypatch):
    """A rule refused at startup (object store not up yet) is written later."""
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", True)
    monkeypatch.setattr(settings, "sc_gc_enabled", True)
    _master(monkeypatch, lambda request: httpx.Response(200, json={"Version": "30GB 4.48 x"}))

    report = await run_gc_pass(es, s3, now=NOW)

    assert report.lifecycle.startswith("set")
    assert [r["ID"] for r in s3.lifecycle] == ["deft-its-frame-expiry"]


@pytest.mark.asyncio
async def test_a_refused_lifecycle_rule_is_reported(es, s3, monkeypatch):
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", True)
    monkeypatch.setattr(settings, "sc_gc_enabled", True)
    _master(monkeypatch, lambda request: httpx.Response(200, json={"Version": "30GB 4.17 x"}))

    report = await run_gc_pass(es, s3, now=NOW)

    assert report.lifecycle.startswith("refused") and report.failed
    assert s3.lifecycle == []


@pytest.mark.asyncio
async def test_a_dry_run_leaves_the_lifecycle_rule_alone(es, s3, monkeypatch):
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", True)
    monkeypatch.setattr(settings, "sc_gc_enabled", True)

    report = await run_gc_pass(es, s3, now=NOW, dry_run=True)

    assert report.lifecycle is None and s3.lifecycle == []
