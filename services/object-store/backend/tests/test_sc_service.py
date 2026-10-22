"""Archival handler: image to the object store, flat metadata to Kafka."""

import base64
import hashlib

import pytest

from pydantic import ValidationError

from backend.api.sc_schemas import SCFrame
from backend.api.sc_service import archive_frame, build_metadata, object_key, upload_frame
from backend.config import settings

RAW = b"\xff\xd8fake-jpeg-bytes"
FRAME_ID = "20260105T000320Z_10_31_177_247_0_frame_000115"


def frame(**overrides):
    base = {
        "version": "4.0",
        "id": FRAME_ID,
        "timestamp": "2026-01-05T00:03:20.000Z",
        "sensorId": "SanTomasIntersection",
        "tag": "its-train",
        "objects": [],
        "imageData": {
            "content_type": "image/jpeg",
            "data": base64.b64encode(RAW).decode(),
            "metadata": {"width": 1920, "height": 1080},
        },
    }
    base.update(overrides)
    return base


class FakeStorage:
    def __init__(self, fail: bool = False):
        self.uploaded: list[tuple] = []
        self.deleted: list[str] = []
        self.tagging: list[str | None] = []
        self.fail = fail

    async def upload(self, key, data, content_type, metadata, tagging=None):
        if self.fail:
            raise RuntimeError("s3 down")
        self.uploaded.append((key, data, content_type, metadata))
        self.tagging.append(tagging)
        return f"http://s3/{key}"

    async def delete(self, key):
        self.deleted.append(key)


class FakeKafka:
    def __init__(self, fail: bool = False, delete_fails: bool = False):
        self.sent: list[tuple] = []
        self.fail = fail

    async def send(self, payload, topic=None, key=None, partition=None):
        if self.fail:
            raise RuntimeError("kafka down")
        self.sent.append((payload, topic, key))


# --------------------------------------------------------------- object key


def test_object_key_is_content_addressed():
    key = object_key(RAW, FRAME_ID, "image/jpeg")
    digest = hashlib.sha256(RAW).hexdigest()[:16]
    assert key == f"{settings.its_object_prefix}/{digest}/{FRAME_ID}.jpg"


def test_same_bytes_give_the_same_key():
    """Re-archiving a frame must overwrite, not accumulate a second copy."""
    assert object_key(RAW, FRAME_ID, "image/jpeg") == object_key(RAW, FRAME_ID, "image/jpeg")


def test_different_bytes_give_different_keys():
    assert object_key(RAW, FRAME_ID, "image/jpeg") != object_key(b"other", FRAME_ID, "image/jpeg")


def test_object_key_sanitises_the_frame_id():
    key = object_key(RAW, "cam/01 frame:7", "image/jpeg")
    name = key.rsplit("/", 1)[1]
    assert name == "cam_01_frame_7.jpg"


def test_object_key_falls_back_on_unknown_content_type():
    assert object_key(RAW, FRAME_ID, "application/octet-stream").endswith(".bin")


# ---------------------------------------------------------------- metadata


def test_metadata_drops_the_image_and_records_where_it_went():
    key = "deft-its/abc/frame.jpg"
    md = build_metadata(SCFrame.model_validate(frame()), key, RAW)

    assert "imageData" not in md
    assert md["objStorePath"] == key
    assert md["imageType"] == "image/jpeg"
    assert md["imageSize"] == len(RAW)
    assert md["imageWidth"] == 1920
    assert md["imageHeight"] == 1080


def test_metadata_stays_flat():
    """sc-ingest.conf builds the nesting; sending it from here would duplicate
    that knowledge in two places."""
    md = build_metadata(SCFrame.model_validate(frame()), "k", RAW)
    assert "metadata" not in md
    assert "content_metadata" not in md
    assert md["sensorId"] == "SanTomasIntersection"
    assert md["tag"] == "its-train"


def test_metadata_omits_dimensions_when_absent():
    f = frame()
    f["imageData"]["metadata"] = {}
    md = build_metadata(SCFrame.model_validate(f), "k", RAW)
    assert "imageWidth" not in md and "imageHeight" not in md


# ----------------------------------------------------------------- archive


async def test_archive_uploads_then_publishes():
    storage, kafka = FakeStorage(), FakeKafka()
    key = await archive_frame(storage, kafka, frame())

    assert len(storage.uploaded) == 1
    stored_key, data, content_type, _ = storage.uploaded[0]
    assert stored_key == key
    assert data == RAW
    assert content_type == "image/jpeg"

    payload, topic, msg_key = kafka.sent[0]
    assert topic == settings.sc_metadata_topic
    assert msg_key == "SanTomasIntersection"
    assert payload["objStorePath"] == key
    assert "imageData" not in payload


@pytest.mark.parametrize(
    ("tag", "tagging"),
    [
        ("its-train", "deft-retention=its-frame"),
        (None, "deft-retention=its-frame"),
        ("kpi", None),
        ("kpi-golden", None),
    ],
)
async def test_frames_but_kpi_ones_are_tagged_for_the_lifecycle_rule(tag, tagging):
    storage, kafka = FakeStorage(), FakeKafka()
    await archive_frame(storage, kafka, frame(tag=tag) if tag else _untagged())

    assert storage.tagging == [tagging]


async def test_uploaded_samples_are_tagged_like_archived_frames():
    storage, kafka = FakeStorage(), FakeKafka()
    for tag in ("its-train", "kpi"):
        await upload_frame(
            storage,
            kafka,
            metadata=SCFrame.model_validate(frame(tag=tag)),
            data=RAW,
            content_type="image/jpeg",
        )

    assert storage.tagging == ["deft-retention=its-frame", None]


async def test_frame_tagging_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "sc_frame_tagging", False)
    storage, kafka = FakeStorage(), FakeKafka()
    await archive_frame(storage, kafka, frame())

    assert storage.tagging == [None]


def _untagged():
    raw = frame()
    del raw["tag"]
    return raw


async def test_kafka_failure_removes_the_object():
    """An object with nothing in the catalog pointing at it would never be
    noticed, so the upload is rolled back."""
    storage, kafka = FakeStorage(), FakeKafka(fail=True)
    with pytest.raises(RuntimeError):
        await archive_frame(storage, kafka, frame())

    assert len(storage.uploaded) == 1
    assert storage.deleted == [storage.uploaded[0][0]]


async def test_storage_failure_publishes_nothing():
    storage, kafka = FakeStorage(fail=True), FakeKafka()
    with pytest.raises(RuntimeError):
        await archive_frame(storage, kafka, frame())
    assert kafka.sent == []


async def test_frame_without_image_is_skipped_not_failed():
    """A metadata-only frame is valid on this topic."""
    storage, kafka = FakeStorage(), FakeKafka()
    f = frame()
    del f["imageData"]
    assert await archive_frame(storage, kafka, f) is None
    assert storage.uploaded == [] and kafka.sent == []


async def test_invalid_base64_raises():
    storage, kafka = FakeStorage(), FakeKafka()
    f = frame()
    f["imageData"]["data"] = "not base64!!!"
    with pytest.raises(ValueError):
        await archive_frame(storage, kafka, f)
    assert storage.uploaded == []


async def test_empty_image_raises():
    storage, kafka = FakeStorage(), FakeKafka()
    f = frame()
    f["imageData"]["data"] = ""
    assert await archive_frame(storage, kafka, f) is None


# ------------------------------------------------------------- validation


async def test_undeclared_field_is_rejected_before_anything_is_written():
    """The trade for extra=forbid: a frame with an unknown key is refused, so
    nothing reaches the object store."""
    storage, kafka = FakeStorage(), FakeKafka()
    f = frame()
    f["fov"] = {"x": 1}
    with pytest.raises(ValidationError):
        await archive_frame(storage, kafka, f)
    assert storage.uploaded == [] and kafka.sent == []


async def test_missing_sensor_id_is_rejected():
    """sensorId picks the index, so a frame without one has nowhere to go."""
    storage, kafka = FakeStorage(), FakeKafka()
    f = frame()
    del f["sensorId"]
    with pytest.raises(ValidationError):
        await archive_frame(storage, kafka, f)
    assert storage.uploaded == []


async def test_minimal_vss_variant_timestamp_is_accepted():
    """Selected Image Schema sends timestamp; the minimal variant sends
    @timestamp. The ingest pipeline takes both, so this must too."""
    storage, kafka = FakeStorage(), FakeKafka()
    f = frame()
    f["@timestamp"] = f.pop("timestamp")
    await archive_frame(storage, kafka, f)
    assert kafka.sent[0][0]["timestamp"] == "2026-01-05T00:03:20.000Z"


def test_declared_nvschema_blocks_do_not_break_validation():
    """sensor, place, analyticsModule and videoPath are declared ahead of use so
    a producer that starts sending them is not rejected."""
    f = frame()
    f["videoPath"] = "http://vst/1"
    f["sensor"] = {"id": "cam1", "type": "Camera",
                   "location": {"lat": 1.0, "lon": 2.0, "alt": 3.0}}
    f["place"] = {"id": "p1", "name": "Garage", "info": {"lane": "l1", "zone": "z"}}
    f["analyticsModule"] = {"id": "am1", "version": "2"}
    model = SCFrame.model_validate(f)
    assert model.sensor.location.alt == 3.0
    assert model.place.info == {"lane": "l1", "zone": "z"}


def test_metadata_keeps_declared_blocks_flat():
    f = frame()
    f["videoPath"] = "http://vst/1"
    md = build_metadata(SCFrame.model_validate(f), "k", RAW)
    assert md["videoPath"] == "http://vst/1"
    assert "metadata" not in md and "content_metadata" not in md
