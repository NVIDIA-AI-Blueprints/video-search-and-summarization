#!/usr/bin/env python3
"""
Upload the `bianca` AOI dataset (train + kpi splits) to the Object Store via POST /api/samples.

Dataset layout:
    <dataset-dir>/
      train.json          # {"pairs": [ {defect_image_path, referecne_image_path, defect_boxes, pass_boxes}, ... ]}
      kpi.json            # same shape
      images/
        <id>_defect.jpg
        <id>_ref.jpg

Boxes are COCO_XYWH ([x, y, w, h]) in pixels. `defect_boxes` are confirmed defects,
`pass_boxes` are machine calls that a human overruled to PASS.

Two-phase upload (per split):
  Phase 1 — Upload the unique reference images (tag="aoi1-golden-train" / "kpi-golden").
            Storage keys are derived locally from sha256(image_bytes) so Phase 2 does
            not depend on the server response for key look-ups.
  Phase 2 — Upload the defect images (tag="aoi1-train" / "kpi") with golden-ref set to
            the Phase 1 storage key and cropInfo built from defect_boxes + pass_boxes.

Negative samples reuse the same file as both reference and tested image. Since the
storage key is content-derived, both roles map to one object; Phase 1 skips those so
the tested-image metadata (which carries the crops) is the one that survives.

Install deps (from repo root):
    pip install -e scripts/

Usage:
    python scripts/upload_bianca_dataset.py \\
        --dataset-dir ~/nvidia/foo/aoi_bianca \\
        --api-url http://localhost:8008 \\
        [--split train|kpi|all]     # default: all
        [--batch-size 10]           # max 10 (backend limit)
        [--inter-batch-delay 0.5]
        [--limit N]                 # first N pairs per split, for smoke tests
        [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import requests
from PIL import Image

from deft_common import ConfirmedLabel, Tag

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
}

_DATASET_NAME = "bianca"
# The backend rejects a request whose images sum to more than settings.max_request_bytes
# once *decoded*, so batches are measured in decoded bytes, not base64 length.
_MAX_REQUEST_BYTES = 10 * 1024 * 1024
_MAX_BATCH_BYTES = 9 * 1024 * 1024
_MAX_BATCH_COUNT = 10
_RETRY_PASSES = 2
_RETRY_BACKOFF_S = 5.0
_MAX_RETRY_QUEUE = 200
_MACHINE_LOCATION = "bianca dataset"

_SPLIT_TAGS: dict[str, tuple[str, str]] = {
    # split -> (tested image tag, reference image tag)
    "train": (Tag.AOI1_TRAIN, Tag.AOI1_GOLDEN_TRAIN),
    "day1": (Tag.AOI1_TRAIN, Tag.AOI1_GOLDEN_TRAIN),
    "day2": (Tag.AOI1_TRAIN, Tag.AOI1_GOLDEN_TRAIN),
    "day3": (Tag.AOI1_TRAIN, Tag.AOI1_GOLDEN_TRAIN),
    "kpi": (Tag.KPI, Tag.KPI_GOLDEN),
}

# The JSON ships this key misspelled; keep the wire name in one place.
_REF_KEY = "referecne_image_path"

# Per-item decoded size, carried on the item for batching and stripped before POSTing.
_SIZE_KEY = "_decoded_bytes"


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------


def _load_image(image_path: Path) -> tuple[bytes, str, str, int, int] | None:
    """Return (raw_bytes, base64_data, content_type, width, height) or None on failure."""
    content_type = _CONTENT_TYPES.get(image_path.suffix.lower())
    if not content_type:
        logger.warning("Unsupported image type: %s", image_path)
        return None
    try:
        raw = image_path.read_bytes()
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
    except (OSError, Image.DecompressionBombError) as exc:
        logger.error("Failed to read %s: %s", image_path, exc)
        return None
    if width <= 0 or height <= 0:
        logger.error("Zero-area image %s (%dx%d) — skipping", image_path, width, height)
        return None
    return raw, base64.b64encode(raw).decode("ascii"), content_type, width, height


def _too_large(image_path: Path, raw: bytes) -> bool:
    """True if this image alone exceeds the backend's per-request decoded-byte limit.

    Such an image can never be uploaded through /api/samples — even alone in a
    batch — so it is skipped loudly rather than retried into a guaranteed 422.
    """
    if len(raw) <= _MAX_REQUEST_BYTES:
        return False
    logger.error(
        "%s is %.1f MB decoded, over the backend's %.0f MB per-request limit — skipping. "
        "Raise MAX_REQUEST_BYTES on backend-api to include it.",
        image_path.name, len(raw) / 1024 / 1024, _MAX_REQUEST_BYTES / 1024 / 1024,
    )
    return True


def _local_storage_key(image_bytes: bytes, filename: str) -> str:
    """Mirrors backend/api/service.py key formula: deft-aoi/{sha256[:16]}/{filename}."""
    return f"deft-aoi/{hashlib.sha256(image_bytes).hexdigest()[:16]}/{filename}"


# ---------------------------------------------------------------------------
# Metadata builders
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _pair_timestamp(pair: dict[str, Any]) -> str:
    for box in pair["defect_boxes"] + pair["pass_boxes"]:
        ts = box.get("timestamp")
        if ts:
            return ts
    return _now_iso()


def _machine_info(pair: dict[str, Any]) -> dict[str, Any]:
    info: dict[str, Any] = {"machineId": "", "location": _MACHINE_LOCATION, "jobType": "AOI"}
    for box in pair["pass_boxes"]:
        if box.get("unit_id"):
            info["batchId"] = box["unit_id"]
        if box.get("serial_number"):
            info["serialNumber"] = box["serial_number"]
        if "batchId" in info and "serialNumber" in info:
            break
    return info


def _coords(bbox: list[float]) -> dict[str, float]:
    x, y, w, h = bbox
    return {"left": x, "top": y, "width": w, "height": h}


def _crop_items(pair: dict[str, Any], sample_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for i, box in enumerate(pair["defect_boxes"]):
        if not box.get("bbox_valid", True):
            continue
        defect_type = box.get("defect_type") or "defect"
        items.append({
            "id": f"{sample_id}_defect_{i}",
            "label": defect_type,
            "confirmed_label": ConfirmedLabel.DEFECT,
            "crop": {"cropType": "bbox", "coordinates": _coords(box["bbox"])},
            "aoiDefectInfo": {"defectType": defect_type},
            "referenceLabels": None,
        })
    for j, box in enumerate(pair["pass_boxes"]):
        if not box.get("bbox_valid", True):
            continue
        item: dict[str, Any] = {
            "id": f"{sample_id}_pass_{j}",
            "label": ConfirmedLabel.PASS.value,
            "confirmed_label": ConfirmedLabel.PASS,
            "crop": {"cropType": "bbox", "coordinates": _coords(box["bbox"])},
            "aoiDefectInfo": {"defectType": ConfirmedLabel.PASS.value},
            "referenceLabels": None,
        }
        # The machine's original (overruled) call is the only place this survives.
        if box.get("monitor_label"):
            item["customInfo"] = {"key": "monitor_label", "value": box["monitor_label"]}
        items.append(item)
    return items


def _build_ref_metadata(sample_id: str, filename: str, split: str) -> dict[str, Any]:
    ref_tag = _SPLIT_TAGS[split][1]
    return {
        "version": "1.0.0",
        "description": f"{_DATASET_NAME} | split={split} | sample={sample_id} | reference image",
        "timestamp": _now_iso(),
        "tag": ref_tag,
        "machineInfo": {"machineId": "", "location": _MACHINE_LOCATION, "jobType": "reference"},
        "aoiDefects": {"count": 0, "details": []},
        "imageInfo": {"imageType": "pcb_reference", "path": filename, "golden-ref": ""},
    }


def _build_test_metadata(
    pair: dict[str, Any],
    sample_id: str,
    filename: str,
    golden_ref_key: str,
    split: str,
) -> dict[str, Any]:
    test_tag = _SPLIT_TAGS[split][0]
    defect_boxes = [b for b in pair["defect_boxes"] if b.get("bbox_valid", True)]
    return {
        "version": "1.0.0",
        "description": f"{_DATASET_NAME} | split={split} | sample={sample_id}",
        "timestamp": _pair_timestamp(pair),
        "tag": test_tag,
        "machineInfo": _machine_info(pair),
        "aoiDefects": {
            "count": len(defect_boxes),
            "details": [
                {"defectType": b.get("defect_type") or "defect", "count": 1} for b in defect_boxes
            ],
        },
        "imageInfo": {
            "imageType": "pcb_tested",
            "path": filename,
            "golden-ref": golden_ref_key,
            "cropInfo": _crop_items(pair, sample_id),
        },
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post_batch(
    session: requests.Session,
    api_url: str,
    batch: list[dict[str, Any]],
    dry_run: bool,
) -> list[dict[str, Any]]:
    if dry_run:
        return [{"filename": item["filename"], "storage_key": f"dry-run/{item['filename']}"} for item in batch]
    payload = [{k: v for k, v in item.items() if not k.startswith("_")} for item in batch]
    try:
        resp = session.post(f"{api_url}/api/samples", json={"images": payload}, timeout=300)
        if not resp.ok:
            logger.error("Upload failed HTTP %d: %s", resp.status_code, resp.text[:300])
            return []
        result = resp.json()
        for f in result.get("failed", []):
            logger.warning("Server rejected %s: %s", f.get("filename"), f.get("error"))
        return result.get("uploaded", [])
    except requests.RequestException as exc:
        logger.error("Request error: %s", exc)
        return []


def _iter_batches(
    items: Iterable[dict[str, Any]], max_count: int
) -> Iterator[list[dict[str, Any]]]:
    """Group items into batches bounded by count and total decoded payload size.

    A generator so the caller never holds more than one batch of image data in
    memory — the full dataset is several GB.
    """
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    for item in items:
        item_bytes = item.get(_SIZE_KEY, 0)
        if batch and (len(batch) >= max_count or batch_bytes + item_bytes > _MAX_BATCH_BYTES):
            yield batch
            batch, batch_bytes = [], 0
        batch.append(item)
        batch_bytes += item_bytes
    if batch:
        yield batch


def _upload_stream(
    session: requests.Session,
    api_url: str,
    items: Iterable[dict[str, Any]],
    batch_size: int,
    delay_s: float,
    dry_run: bool,
    label: str,
) -> set[str]:
    """Upload items batch by batch; return the set of filenames the server confirmed."""
    confirmed: set[str] = set()
    attempted = 0
    last_logged = 0
    # Failures are usually transient (an S3 or broker blip drops a whole batch),
    # so they are retried in a later pass rather than lost. Bounded so a total
    # outage cannot pull the whole dataset into memory.
    pending_retry: list[dict[str, Any]] = []
    dropped = 0

    for batch in _iter_batches(items, batch_size):
        if delay_s > 0 and attempted:
            time.sleep(delay_s)
        attempted += len(batch)
        uploaded = {u["filename"] for u in _post_batch(session, api_url, batch, dry_run)}
        confirmed.update(uploaded)
        for item in batch:
            if item["filename"] in uploaded:
                continue
            if len(pending_retry) < _MAX_RETRY_QUEUE:
                pending_retry.append(item)
            else:
                dropped += 1
                logger.error("%s: failed to upload %s (retry queue full)", label, item["filename"])
        if attempted - last_logged >= 100:
            last_logged = attempted
            logger.info("%s: %d/%d uploaded so far", label, len(confirmed), attempted)

    for attempt in range(1, _RETRY_PASSES + 1):
        retry_now = [i for i in pending_retry if i["filename"] not in confirmed]
        if not retry_now:
            break
        backoff = _RETRY_BACKOFF_S * attempt
        logger.warning("%s: retrying %d failed image(s), pass %d/%d after %.0fs",
                       label, len(retry_now), attempt, _RETRY_PASSES, backoff)
        time.sleep(backoff)
        for batch in _iter_batches(retry_now, batch_size):
            confirmed.update(u["filename"] for u in _post_batch(session, api_url, batch, dry_run))

    for item in pending_retry:
        if item["filename"] not in confirmed:
            logger.error("%s: gave up on %s after %d retry pass(es)",
                         label, item["filename"], _RETRY_PASSES)
    logger.info("%s: %d uploaded, %d failed (total %d)",
                label, len(confirmed), attempted - len(confirmed), attempted)
    return confirmed


# ---------------------------------------------------------------------------
# Per-split upload
# ---------------------------------------------------------------------------


def _sample_id(image_path: str) -> str:
    return Path(image_path).stem


def _upload_split(
    session: requests.Session,
    pairs: list[dict[str, Any]],
    dataset_dir: Path,
    api_url: str,
    split: str,
    batch_size: int,
    delay_s: float,
    dry_run: bool,
) -> None:
    positives = sum(1 for p in pairs if p["defect_boxes"])
    logger.info("=== Split: %s — %d pairs (%d with defects, %d defect-free) ===",
                split, len(pairs), positives, len(pairs) - positives)

    # ------------------------------------------------------------------
    # Phase 1: reference images (unique by path)
    # ------------------------------------------------------------------
    defect_paths = {p["defect_image_path"] for p in pairs}
    ref_paths = sorted({p[_REF_KEY] for p in pairs})
    # A path used as both reference and tested image resolves to a single object
    # (content-derived key), so let Phase 2 own it — its metadata carries the crops.
    self_ref = [r for r in ref_paths if r in defect_paths]
    upload_refs = [r for r in ref_paths if r not in defect_paths]
    logger.info("[%s] Phase 1: %d unique reference images (%d also used as tested images, uploaded in Phase 2)",
                split, len(ref_paths), len(self_ref))

    ref_keys: dict[str, str] = {}       # ref path -> storage key
    ref_filenames: dict[str, str] = {}  # ref path -> filename
    missing_ref = 0
    oversized_ref = 0

    def ref_items() -> Iterator[dict[str, Any]]:
        nonlocal missing_ref, oversized_ref
        for rel_path in upload_refs:
            img_path = dataset_dir / rel_path
            if not img_path.exists():
                logger.warning("Reference image not found: %s", img_path)
                missing_ref += 1
                continue
            if dry_run:
                ref_keys[rel_path] = f"dry-run/{img_path.name}"
                ref_filenames[rel_path] = img_path.name
                yield {"filename": img_path.name}
                continue
            loaded = _load_image(img_path)
            if loaded is None:
                missing_ref += 1
                continue
            raw, data, content_type, _, _ = loaded
            if _too_large(img_path, raw):
                oversized_ref += 1
                continue
            ref_keys[rel_path] = _local_storage_key(raw, img_path.name)
            ref_filenames[rel_path] = img_path.name
            yield {
                "filename": img_path.name,
                "content_type": content_type,
                "data": data,
                "metadata": _build_ref_metadata(_sample_id(rel_path), img_path.name, split),
                _SIZE_KEY: len(raw),
            }

    confirmed = _upload_stream(session, api_url, ref_items(), batch_size, delay_s, dry_run,
                               label=f"[{split}] Phase 1 reference images")
    if missing_ref:
        logger.warning("[%s] Phase 1: %d reference images unreadable or not on disk", split, missing_ref)
    if oversized_ref:
        logger.warning("[%s] Phase 1: %d reference images skipped as too large for one request",
                       split, oversized_ref)

    # Drop keys the server never confirmed so Phase 2 doesn't embed a broken golden-ref.
    if not dry_run:
        for rel_path, filename in list(ref_filenames.items()):
            if filename not in confirmed:
                logger.error("[%s] Phase 1: %s not confirmed uploaded — dependent samples will be skipped",
                             split, filename)
                ref_keys.pop(rel_path, None)

    # Self-referencing samples: key is derived from the same bytes Phase 2 uploads.
    for rel_path in self_ref:
        img_path = dataset_dir / rel_path
        if dry_run:
            ref_keys[rel_path] = f"dry-run/{img_path.name}"
        elif img_path.exists():
            raw = img_path.read_bytes()
            # Phase 2 will skip an oversized image, so registering its key here
            # would leave the sample pointing at an object that never lands.
            if not _too_large(img_path, raw):
                ref_keys[rel_path] = _local_storage_key(raw, img_path.name)

    # ------------------------------------------------------------------
    # Phase 2: tested (defect) images
    # ------------------------------------------------------------------
    logger.info("[%s] Phase 2: uploading %d tested images", split, len(pairs))
    missing_test = 0
    skipped_no_ref = 0
    oversized_test = 0

    def test_items() -> Iterator[dict[str, Any]]:
        nonlocal missing_test, skipped_no_ref, oversized_test
        for pair in pairs:
            rel_path = pair["defect_image_path"]
            img_path = dataset_dir / rel_path
            sample_id = _sample_id(rel_path)
            if not img_path.exists():
                logger.warning("Tested image not found: %s", img_path)
                missing_test += 1
                continue
            golden_ref_key = ref_keys.get(pair[_REF_KEY])
            if not golden_ref_key:
                logger.error("[%s] Phase 2: sample %s has no usable reference — skipping", split, sample_id)
                skipped_no_ref += 1
                continue
            if dry_run:
                yield {"filename": img_path.name}
                continue
            loaded = _load_image(img_path)
            if loaded is None:
                missing_test += 1
                continue
            raw, data, content_type, _, _ = loaded
            if _too_large(img_path, raw):
                oversized_test += 1
                continue
            yield {
                "filename": img_path.name,
                "content_type": content_type,
                "data": data,
                "metadata": _build_test_metadata(pair, sample_id, img_path.name, golden_ref_key, split),
                _SIZE_KEY: len(raw),
            }

    _upload_stream(session, api_url, test_items(), batch_size, delay_s, dry_run,
                   label=f"[{split}] Phase 2 tested images")
    if missing_test:
        logger.warning("[%s] Phase 2: %d tested images unreadable or not on disk", split, missing_test)
    if skipped_no_ref:
        logger.warning("[%s] Phase 2: %d samples skipped for missing reference upload", split, skipped_no_ref)
    if oversized_test:
        logger.warning("[%s] Phase 2: %d tested images skipped as too large for one request",
                       split, oversized_test)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _load_pairs(path: Path) -> list[dict[str, Any]]:
    with open(path) as f:
        data = json.load(f)
    pairs = data["pairs"]
    for pair in pairs:
        missing = {"defect_image_path", _REF_KEY, "defect_boxes", "pass_boxes"} - pair.keys()
        if missing:
            raise ValueError(f"{path}: pair missing keys {sorted(missing)}")
    return pairs


def upload_bianca_dataset(
    dataset_dir: Path,
    api_url: str,
    split_selection: str = "all",
    batch_size: int = _MAX_BATCH_COUNT,
    inter_batch_delay_s: float = 0.5,
    dry_run: bool = False,
    limit: int | None = None,
) -> None:
    splits = ["train", "kpi"] if split_selection == "all" else [split_selection]

    loaded: list[tuple[str, list[dict[str, Any]]]] = []
    for split in splits:
        json_path = dataset_dir / f"{split}.json"
        if not json_path.exists():
            logger.error("%s not found in %s", json_path.name, dataset_dir)
            sys.exit(1)
        pairs = _load_pairs(json_path)
        if limit is not None and limit < len(pairs):
            logger.warning("[%s] --limit %d: uploading only the first %d of %d pairs",
                           split, limit, limit, len(pairs))
            pairs = pairs[:limit]
        loaded.append((split, pairs))

    with requests.Session() as session:
        session.headers["Content-Type"] = "application/json"
        for split, pairs in loaded:
            _upload_split(session, pairs, dataset_dir, api_url, split,
                          batch_size, inter_batch_delay_s, dry_run)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset-dir", required=True, type=Path,
                   help="Path to the aoi_bianca folder (contains train.json, kpi.json and images/)")
    p.add_argument("--api-url", default="http://localhost:8008",
                   help="Backend API base URL (default: http://localhost:8008)")
    p.add_argument("--split", default="all", choices=["train", "kpi", "day1", "day2", "day3", "all"],
                   help="Which dataset to upload (default: all)")
    p.add_argument("--batch-size", type=int, default=_MAX_BATCH_COUNT, metavar="N",
                   help=f"Images per upload batch, max {_MAX_BATCH_COUNT} (backend limit)")
    p.add_argument("--inter-batch-delay", type=float, default=0.5, metavar="SECONDS",
                   help="Delay between batches in seconds (default: 0.5)")
    p.add_argument("--max-request-mb", type=float, default=10.0, metavar="MB",
                   help="Must match the server's MAX_REQUEST_BYTES. Images larger than this "
                        "can never fit in a request and are skipped (default: 10)")
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="Upload only the first N pairs of each split (for smoke-testing)")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate paths and log counts without uploading")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()
    if not 1 <= args.batch_size <= _MAX_BATCH_COUNT:
        p.error(f"--batch-size must be between 1 and {_MAX_BATCH_COUNT} (backend limit)")
    return args


if __name__ == "__main__":
    args = _parse_args()
    _MAX_REQUEST_BYTES = int(args.max_request_mb * 1024 * 1024)
    # Keep batches comfortably under the server cap; a lone image may use all of it.
    _MAX_BATCH_BYTES = min(_MAX_BATCH_BYTES, int(_MAX_REQUEST_BYTES * 0.9))
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=getattr(logging, args.log_level),
    )
    upload_bianca_dataset(
        dataset_dir=args.dataset_dir,
        api_url=args.api_url.rstrip("/"),
        split_selection=args.split,
        batch_size=args.batch_size,
        inter_batch_delay_s=args.inter_batch_delay,
        dry_run=args.dry_run,
        limit=args.limit,
    )
