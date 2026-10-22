"""Re-encoding of archived Smart City frames (backend.api.sc_compress).

These run the real libjpeg-turbo tools on a synthetic JPEG, since whether the
lossless mode really keeps every pixel is the point; they skip where the tools
are not installed.
"""

import base64
import hashlib
import shutil
import subprocess

import pytest

from backend.api import sc_compress
from backend.api.sc_compress import compress_frame
from backend.api.sc_schemas import SCFrame
from backend.api.sc_service import archive_frame, upload_frame
from backend.config import settings
from tests.test_sc_service import FakeKafka, FakeStorage, frame

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("jpegtran", "djpeg", "cjpeg")),
    reason="libjpeg-turbo tools (jpegtran, djpeg, cjpeg) are not installed",
)


def _ppm(width=320, height=240) -> bytes:
    """A gradient with a deterministic texture, so JPEG has work to do."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            noise = (x * 7919 + y * 104729) % 23
            pixels += bytes(((x + noise) % 256, (y * 2) % 256, (x + y + noise) % 256))
    return f"P6\n{width} {height}\n255\n".encode() + bytes(pixels)


def _run(argv, data) -> bytes:
    return subprocess.run(argv, input=data, capture_output=True, check=True).stdout


@pytest.fixture(scope="module")
def jpeg() -> bytes:
    # Baseline with standard Huffman tables, as a camera or ffmpeg writes it.
    return _run(["cjpeg", "-quality", "92"], _ppm())


@pytest.fixture(autouse=True)
def mode(monkeypatch):
    monkeypatch.setattr(settings, "sc_frame_compression", "lossless")
    monkeypatch.setattr(settings, "sc_frame_jpeg_quality", 85)


def _pixels(data: bytes) -> bytes:
    return _run(["djpeg"], data)


@pytest.mark.asyncio
async def test_off_stores_the_bytes_received(jpeg, monkeypatch):
    monkeypatch.setattr(settings, "sc_frame_compression", "off")
    assert await compress_frame(jpeg, "image/jpeg", "its-train") is jpeg


@pytest.mark.asyncio
async def test_lossless_is_smaller_and_pixel_identical(jpeg):
    out = await compress_frame(jpeg, "image/jpeg", "its-train")

    assert len(out) < len(jpeg)
    assert _pixels(out) == _pixels(jpeg)


@pytest.mark.asyncio
async def test_lossy_is_smaller_still(jpeg, monkeypatch):
    lossless = await compress_frame(jpeg, "image/jpeg", "its-train")
    monkeypatch.setattr(settings, "sc_frame_compression", "lossy")

    out = await compress_frame(jpeg, "image/jpeg", "its-train")

    assert len(out) < len(lossless)
    assert out.startswith(b"\xff\xd8")
    assert len(_pixels(out)) == len(_pixels(jpeg))  # same dimensions


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["kpi", "kpi-golden"])
async def test_kpi_frames_are_never_reencoded(jpeg, tag, monkeypatch):
    for mode in ("lossless", "lossy"):
        monkeypatch.setattr(settings, "sc_frame_compression", mode)
        assert await compress_frame(jpeg, "image/jpeg", tag) is jpeg


@pytest.mark.asyncio
async def test_other_image_types_are_left_alone():
    png = b"\x89PNG\r\n\x1a\nnot-really"
    assert await compress_frame(png, "image/png", "its-train") is png


@pytest.mark.asyncio
async def test_output_that_is_not_smaller_is_discarded(jpeg):
    once = await compress_frame(jpeg, "image/jpeg", "its-train")
    assert await compress_frame(once, "image/jpeg", "its-train") is once


@pytest.mark.asyncio
async def test_undecodable_bytes_are_stored_as_received():
    junk = b"\xff\xd8not a jpeg at all"
    assert await compress_frame(junk, "image/jpeg", "its-train") is junk


@pytest.mark.asyncio
async def test_truncated_jpeg_is_stored_as_received(jpeg):
    """libjpeg-turbo only warns on a truncated file; that must not pass."""
    truncated = jpeg[: len(jpeg) // 2]
    assert await compress_frame(truncated, "image/jpeg", "its-train") is truncated


@pytest.mark.asyncio
async def test_missing_tools_store_as_received(jpeg, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setattr(sc_compress, "_missing_tool_logged", False)
    assert await compress_frame(jpeg, "image/jpeg", "its-train") is jpeg
    assert sc_compress._missing_tool_logged


# --------------------------------------------------- through archive_frame


@pytest.mark.asyncio
async def test_archived_key_and_size_describe_the_stored_bytes(jpeg):
    storage, kafka = FakeStorage(), FakeKafka()
    raw = frame(imageData={
        "content_type": "image/jpeg",
        "data": base64.b64encode(jpeg).decode(),
        "metadata": {"width": 320, "height": 240},
    })

    key = await archive_frame(storage, kafka, raw)

    (stored_key, stored, _, header), = storage.uploaded
    assert stored_key == key
    assert len(stored) < len(jpeg)
    assert hashlib.sha256(stored).hexdigest()[:16] in key
    assert header["imageSize"] == len(stored)
    assert kafka.sent[0][0]["imageSize"] == len(stored)


@pytest.mark.asyncio
async def test_archived_kpi_frame_is_stored_verbatim(jpeg):
    storage, kafka = FakeStorage(), FakeKafka()
    raw = frame(tag="kpi", imageData={
        "content_type": "image/jpeg",
        "data": base64.b64encode(jpeg).decode(),
        "metadata": {"width": 320, "height": 240},
    })

    await archive_frame(storage, kafka, raw)

    assert storage.uploaded[0][1] == jpeg


# ----------------------------------------------------- POST /api/samples


@pytest.mark.asyncio
async def test_uploaded_frame_is_compressed_and_its_stored_size_returned(jpeg):
    storage, kafka = FakeStorage(), FakeKafka()
    meta = SCFrame.model_validate(frame(imageData=None))

    key, _, size = await upload_frame(
        storage, kafka, metadata=meta, data=jpeg, content_type="image/jpeg"
    )

    stored = storage.uploaded[0][1]
    assert size == len(stored) < len(jpeg)
    assert hashlib.sha256(stored).hexdigest()[:16] in key
    assert kafka.sent[0][0]["imageSize"] == size


@pytest.mark.asyncio
async def test_uploaded_kpi_frame_is_stored_verbatim(jpeg):
    storage, kafka = FakeStorage(), FakeKafka()
    meta = SCFrame.model_validate(frame(tag="kpi", imageData=None))

    _, _, size = await upload_frame(
        storage, kafka, metadata=meta, data=jpeg, content_type="image/jpeg"
    )

    assert storage.uploaded[0][1] == jpeg
    assert size == len(jpeg)


# ------------------------------------------------------------ failures


@pytest.mark.asyncio
async def test_a_tool_that_cannot_be_spawned_stores_as_received(jpeg, tmp_path, monkeypatch):
    blocked = tmp_path / "jpegtran"
    blocked.write_text("#!/bin/sh\n")
    blocked.chmod(0o644)  # present but not executable
    monkeypatch.setenv("PATH", str(tmp_path))

    assert await compress_frame(jpeg, "image/jpeg", "its-train") is jpeg


@pytest.mark.asyncio
async def test_a_hung_tool_is_killed_and_the_frame_stored_as_received(jpeg, monkeypatch):
    monkeypatch.setattr(sc_compress, "_TIMEOUT_S", 0.2)
    spawned = []
    real_exec = sc_compress.asyncio.create_subprocess_exec

    async def sleeper(*argv, **kwargs):
        proc = await real_exec("sleep", "30", **kwargs)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(sc_compress.asyncio, "create_subprocess_exec", sleeper)

    assert await compress_frame(jpeg, "image/jpeg", "its-train") is jpeg
    assert spawned and all(p.returncode is not None for p in spawned)


@pytest.mark.asyncio
async def test_lossy_rejects_a_truncated_frame_even_though_the_pipe_succeeds(jpeg, monkeypatch):
    """djpeg only warns on a truncated file and cjpeg happily encodes what it
    got; every stage's exit status must count."""
    monkeypatch.setattr(settings, "sc_frame_compression", "lossy")
    truncated = jpeg[: len(jpeg) // 2]

    assert await compress_frame(truncated, "image/jpeg", "its-train") is truncated
