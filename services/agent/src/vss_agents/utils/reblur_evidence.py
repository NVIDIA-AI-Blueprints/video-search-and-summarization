# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Re-blur an analysed video's stored evidence images without analysing it again.

The face-blur toggle changes how the evidence frames, crops and clip heads of a report look, not what the report
says. This module takes the evidence entries of an existing revision and makes new images for each:

1. The frame is read again from the video at the entry's own ``box_seconds``.
2. The crop is cut from it at the stored box (``detected_box`` with the same 5 % padding as the original cut, else the
   stored ``bounding_box`` as it is), so no detector decides where the object is. The box, second, track, zoom and
   every other field of the entry are kept exactly as they were.
3. With ``blur`` on, :class:`~vss_agents.utils.crop_evidence.EvidenceBlur` pixelates the frame and the crop: the
   heads a whole-frame head detection finds, the matched person tracks stored beside the entries, the located people
   and the regions the earlier blur recorded (a re-blur never blurs less than the run it starts from). It also stores
   ``<itemId>-heads.json`` for the clips. With ``blur`` off the frame and crop are stored as they were cut.
4. The images go under a new ``run-<n>`` folder, never over the source's keys.

Nothing here calls the caption pass, P1, grounding, the zoomed second look or tracking: the only model call is the
head detection, and only when blurring. An item that cannot be re-cut or blurred keeps no image (``image_gap`` says why)
rather than the source's image, which may be blurred differently.
"""

import asyncio
import copy
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np

from vss_agents.utils import crop_evidence
from vss_agents.utils import privacy_blur
from vss_agents.utils import track_evidence

logger = logging.getLogger(__name__)


def entry_seconds(entry: dict[str, Any]) -> float | None:
    """The second the entry's stored frame was read at (``box_seconds``; the item's own second for an older entry)."""
    for key in ("box_seconds", "timestamp_seconds"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return None


def _recorded_regions(value: Any) -> list[privacy_blur.Box]:
    """The regions of an entry's ``blur_regions`` that are valid boxes (none for an unblurred or malformed entry)."""
    if not isinstance(value, list):
        return []
    return privacy_blur.unique_regions(
        [tuple(region) for region in value if isinstance(region, (list, tuple)) and len(region) == 4]
    )


def cut_crop(
    entry: dict[str, Any], frame: np.ndarray, seconds: float, keep_frame: bool
) -> crop_evidence.CropResult | None:
    """The entry's crop of ``frame`` from its stored box, or the frame alone for an entry the detector never located."""
    detected = entry.get("detected_box")
    stored = entry.get("bounding_box")
    if entry.get("located") and isinstance(detected, (list, tuple)) and len(detected) == 4:
        # The original cut: the detector's own box with the same padding, so the crop comes out pixel for pixel.
        return crop_evidence._locate_in_frame(
            frame,
            seconds,
            [crop_evidence.Detection(box=[int(v) for v in detected], score=1.0)],
            str(entry.get("label") or ""),
            0.0,
            keep_frame=keep_frame,
        )
    if entry.get("located") and isinstance(stored, (list, tuple)) and len(stored) == 4:
        # An older entry has only the padded box that was stored; cut exactly that.
        frame_h, frame_w = frame.shape[:2]
        ymin, xmin, ymax, xmax = (float(v) for v in stored)
        y0, y1 = max(0, round(ymin * frame_h / 1000.0)), min(frame_h, round(ymax * frame_h / 1000.0))
        x0, x1 = max(0, round(xmin * frame_w / 1000.0)), min(frame_w, round(xmax * frame_w / 1000.0))
        if y0 >= y1 or x0 >= x1:
            return None
        image_bytes = crop_evidence.encode_jpeg(frame[y0:y1, x0:x1], quality=85)
        frame_bytes = crop_evidence.encode_jpeg(frame, quality=crop_evidence.FRAME_JPEG_QUALITY)
        if image_bytes is None or frame_bytes is None:
            return None
        return crop_evidence.CropResult(
            image_bytes=image_bytes,
            normalized_box=[int(v) for v in stored],
            score=1.0,
            frame_size=(frame_w, frame_h),
            frame_bytes=frame_bytes,
            box_seconds=seconds,
            frame=frame if keep_frame else None,
            crop_px=(y0, x0, y1, x1),
        )
    return crop_evidence._not_located_result(frame, seconds, keep_frame)


def _read_track(raw: bytes, key: str, source: str) -> track_evidence.Track | None:
    """A stored ``<itemId>-track.json`` as a track; None when it is not one."""
    try:
        rows = json.loads(raw)
        samples = [
            track_evidence.Sample(t=float(row["t"]), box=tuple(int(v) for v in row["box"]), score=float(row["score"]))  # type: ignore[arg-type]
            for row in rows
        ]
    except (ValueError, KeyError, TypeError):
        return None
    return track_evidence.Track(track_id=key, samples=samples, source=source) if samples else None


async def stored_person_tracks(
    entries: list[dict[str, Any]], store: crop_evidence.EvidenceStore
) -> list[track_evidence.Track]:
    """The person tracks the source run stored beside its entries (object tracks are not people)."""
    keys = {
        str(entry["track"]): str(entry.get("track_source") or track_evidence.PERSON_TRACK_SOURCE)
        for entry in entries
        if isinstance(entry.get("track"), str)
    }

    async def read(key: str, source: str) -> track_evidence.Track | None:
        try:
            raw = await store.get_image(key)
        except Exception as exc:  # the whole-frame head detection still covers the frame
            logger.warning("Stored track %s could not be read for the re-blur: %s", key, type(exc).__name__)
            return None
        return _read_track(raw, key, source) if raw is not None else None

    tracks = await asyncio.gather(
        *(read(key, source) for key, source in keys.items() if source == track_evidence.PERSON_TRACK_SOURCE)
    )
    return [track for track in tracks if track is not None]


async def reblur_evidence(
    *,
    video_ref: str | Path,
    video_id: str,
    source_evidence: list[dict[str, Any]],
    blur: bool,
    store: crop_evidence.EvidenceStore,
    detector: Any | None = None,
    privacy: privacy_blur.PrivacySummary | None = None,
    video_key: str | None = None,
    video_seconds: float | None = None,
    batch_id: str | None = None,
) -> list[dict[str, Any]]:
    """New evidence entries for ``source_evidence``: the same items and boxes, images made again and stored under a new run.

    ``blur`` pixelates the heads in every frame and crop (``detector`` finds them, drawing on ``privacy``'s call cap;
    both are needed then); without it the images are stored as cut from the video. Never raises for a single item.
    """
    if blur and (detector is None or privacy is None):
        raise ValueError("a re-blur with the blur on needs a head detector and a privacy summary")
    folder = video_key or video_id
    batch = batch_id or await store.next_batch_id(folder)
    entries = copy.deepcopy(source_evidence)
    semaphore = asyncio.Semaphore(crop_evidence.EVIDENCE_CROP_CONCURRENCY)
    crops: dict[str, crop_evidence.CropResult] = {}

    async def cut(entry: dict[str, Any]) -> None:
        seconds = entry_seconds(entry)
        if not entry.get("frame") or seconds is None:
            return  # the source stored no frame for this item; there is nothing to make again
        async with semaphore:
            try:

                def read_and_cut() -> crop_evidence.CropResult | None:
                    frame = crop_evidence.extract_frame(video_ref, seconds)
                    if frame is None or frame.size == 0:
                        return None
                    return cut_crop(entry, frame, seconds, keep_frame=blur)

                crop = await asyncio.to_thread(read_and_cut)
            except Exception as exc:
                logger.warning("Re-cut failed for %s %s: %s", video_id, entry["item_id"], type(exc).__name__)
                crop = None
        if crop is None or crop.frame_bytes is None:
            entry["image_gap"] = f"re-cut failed: the frame at {seconds}s could not be read from the video"
            return
        crops[entry["item_id"]] = crop

    await asyncio.gather(*(cut(entry) for entry in entries))

    head_samples: crop_evidence.HeadSamples = {}
    if blur:
        assert privacy is not None
        tracks = await stored_person_tracks(entries, store)
        carried = {entry["item_id"]: _recorded_regions(entry.get("blur_regions")) for entry in entries}
        head_samples = await crop_evidence.EvidenceBlur(
            privacy=privacy,
            detector=crop_evidence.unbudgeted(detector),
            semaphore=semaphore,
            video_id=video_id,
            video_ref=video_ref,
            video_seconds=video_seconds,
            sample_clips=True,
        ).run(entries, crops, tracks, carried)

    def drop_images(entry: dict[str, Any]) -> None:
        """The entry keeps no image: never the source's, which may be blurred differently (or not at all)."""
        entry["frame"] = None
        entry["image"] = None
        entry.pop("heads", None)
        entry.pop("blur_regions", None)

    async def upload(entry: dict[str, Any]) -> None:
        item_id = entry["item_id"]
        crop = crops.get(item_id)
        if crop is None or crop.frame_bytes is None:
            if entry.get("frame"):  # the source stored a frame that could not be made again
                drop_images(entry)
            return
        frame_key = crop_evidence.evidence_frame_key(folder, batch, item_id)
        image_key = crop_evidence.evidence_key(folder, batch, item_id)
        heads_key = crop_evidence.evidence_heads_key(folder, batch, item_id)
        entry.pop("heads", None)
        if not blur:
            entry.pop("blur_regions", None)
        try:
            await store.put_image(frame_key, crop.frame_bytes)
            if crop.image_bytes is not None:
                await store.put_image(image_key, crop.image_bytes)
            samples = head_samples.get(item_id)
            if samples is not None:
                await store.put_image(heads_key, privacy_blur.heads_json(samples), "application/json")
                entry["heads"] = heads_key
        except Exception as exc:
            logger.warning("Re-blurred image upload failed for %s: %s", image_key, type(exc).__name__)
            drop_images(entry)
            entry["image_gap"] = f"image upload failed: {type(exc).__name__}: {exc}"
            return
        entry["frame"] = frame_key
        entry["image"] = image_key if crop.image_bytes is not None else None

    await asyncio.gather(*(upload(entry) for entry in entries))
    return entries
