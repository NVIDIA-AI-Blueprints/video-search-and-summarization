#!/usr/bin/env python3
"""
Upload NVPAW_DET dataset to Object Store, split into 2 datasets: 1 for training and 1 for (online) eval.

Dataset layout:
    <dataset-dir>/
      manifest.csv          # sample_index,class,dataset,id,...,reference_image,tested_image,annotation,...
      images/
        <NNNNNN>_ref.png    # defect-free reference (template) images
        <NNNNNN>_test.png   # tested images (may contain defects)
      annotations/
        <NNNNNN>.json       # bounding-box annotations

Split strategy: configurable train/eval ratio (default 80/20), stratified by class so each split has
equal proportions of normal and abnormal samples. Use --train-ratio to override.

Two-phase upload (per split):
  Phase 1 — Upload reference images (tag="aoi1-golden-{split}").  Storage keys are derived locally
             from sha256(image_bytes) so Phase 2 doesn't depend on the server
             response for key look-ups.
  Phase 2 — Upload tested images (tag="aoi1-{split}") with golden-ref set to the Phase 1
             storage key, and aoiDefects populated from the annotation file.

Install deps (from repo root):
    pip install -e scripts/

Usage:
    python scripts/upload_training_online_eval_dataset.py \\
        --dataset-dir ~/Downloads/"NVPAW_DET_subset_600" \\
        --api-url http://localhost:8008 \\
        [--split train|eval|all]        # default: all
        [--train-ratio 0.8]             # default: 0.8 (80/20 split)
        [--batch-size 10]               # max 10 (backend limit)
        [--inter-batch-delay 0.5]       # default 0.5
        [--num-pass-crops 5]            # synthetic PASS crops per tested image (default: 0 = off)
        [--pass-crop-min-size 32x32]    # minimum WxH of each PASS crop (default: 32x32)
        [--pass-crop-max-size 128x128]  # maximum WxH of each PASS crop (default: 128x128)
        [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from deft_common import ConfirmedLabel
from pass_crop import generate_pass_crops

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
}

_DATASET_NAME = "NVPAW_DET_subset_600"
_MAX_BATCH_BYTES = 9 * 1024 * 1024


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _stratified_split(
    rows: list[dict[str, str]], train_ratio: float
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return (train_rows, eval_rows) with equal class ratios in each split."""
    by_class: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_class.setdefault(row["class"], []).append(row)

    train, eval_ = [], []
    for cls_rows in by_class.values():
        n_train = round(len(cls_rows) * train_ratio)
        train.extend(cls_rows[:n_train])
        eval_.extend(cls_rows[n_train:])
    return train, eval_


def _load_annotation(annotation_path: Path) -> list[dict[str, Any]]:
    """Return the objects list from an annotation JSON. Raises on any failure."""
    try:
        with open(annotation_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__} in {annotation_path}")
        if "objects" not in data:
            raise ValueError(f"Missing 'objects' key in {annotation_path}")
        return data["objects"]
    except Exception as exc:
        logger.error("Cannot read annotation %s: %s", annotation_path, exc)
        raise


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------


def _load_image(image_path: Path) -> tuple[bytes, str, str, int, int] | None:
    """Return (raw_bytes, base64_data, content_type, pil_width, pil_height) or None."""
    content_type = _CONTENT_TYPES.get(image_path.suffix.lower())
    if not content_type:
        logger.warning("Unsupported image type: %s", image_path)
        return None
    try:
        raw = image_path.read_bytes()
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()
        with Image.open(io.BytesIO(raw)) as img:
            pil_width, pil_height = img.size
    except (OSError, Image.DecompressionBombError) as exc:
        logger.error("Failed to read %s: %s", image_path, exc)
        return None
    if pil_width <= 0 or pil_height <= 0:
        logger.error("Zero-area image %s (%dx%d) — skipping", image_path, pil_width, pil_height)
        return None
    return raw, base64.b64encode(raw).decode("ascii"), content_type, pil_width, pil_height


def _local_storage_key(image_bytes: bytes, filename: str) -> str:
    """Mirrors backend/api/service.py key formula: deft-aoi/{sha256[:16]}/{filename}."""
    return f"deft-aoi/{hashlib.sha256(image_bytes).hexdigest()[:16]}/{filename}"


# ---------------------------------------------------------------------------
# Metadata builders
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _build_ref_metadata(
    sample_id: str,
    filename: str,
    split: str,
) -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "description": f"{_DATASET_NAME} | split={split} | sample={sample_id} | reference image",
        "timestamp": _now_iso(),
        "tag": f"aoi1-golden-{split}",
        "machineInfo": {
            "machineId": "",
            "location": "",
            "jobType": "",
        },
        "aoiDefects": {"count": 0, "details": []},
        "imageInfo": {
            "imageType": "pcb_reference",
            "path": filename,
        },
    }


def _build_crop_info(
    objects: list[dict[str, Any]],
    sample_id: str,
    is_normal: bool = False,
    img_width: int = 0,
    img_height: int = 0,
    n_pass_crops: int = 0,
    min_crop_w: int = 32,
    min_crop_h: int = 32,
    max_crop_w: int = 128,
    max_crop_h: int = 128,
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    crop_items = []
    all_bboxes: list[tuple[int, int, int, int]] = []
    for i, obj in enumerate(objects):
        label = obj.get("label", "defect")
        x_min, y_min, x_max, y_max = obj.get("bbox_pixel", [0, 0, 0, 0])
        raw_label = obj.get("label")
        confirmed_label = (
            ConfirmedLabel.PASS if raw_label == "PASS" else ConfirmedLabel.DEFECT
        ) if raw_label is not None else None
        crop_items.append({
            "id": f"{sample_id}_{i}",
            "label": label,
            "confirmed_label": confirmed_label,
            "crop": {
                "cropType": "bbox",
                "coordinates": {
                    "left": x_min,
                    "top": y_min,
                    "width": x_max - x_min,
                    "height": y_max - y_min,
                },
            },
            "aoiDefectInfo": {"defectType": label},
            "referenceLabels": None,
        })
        all_bboxes.append((x_min, y_min, x_max, y_max))

    if n_pass_crops > 0 and img_width > 0 and img_height > 0:
        # For normal boards any region is PASS, so no exclusions.
        # For abnormal boards avoid all annotated bboxes (defect and otherwise).
        excluded = [] if is_normal else all_bboxes
        _rng = rng if rng is not None else random.Random(sample_id)
        pass_boxes = generate_pass_crops(
            img_width, img_height, excluded, n_pass_crops, _rng,
            min_crop_w=min_crop_w, min_crop_h=min_crop_h,
            max_crop_w=max_crop_w, max_crop_h=max_crop_h,
        )
        for j, (left, top, right, bottom) in enumerate(pass_boxes):
            crop_items.append({
                "id": f"{sample_id}_pass_{j}",
                "label": "PASS",
                "confirmed_label": ConfirmedLabel.PASS,
                "crop": {
                    "cropType": "bbox",
                    "coordinates": {
                        "left": left,
                        "top": top,
                        "width": right - left,
                        "height": bottom - top,
                    },
                },
                "aoiDefectInfo": {"defectType": "PASS"},
                "referenceLabels": None,
            })

    return crop_items


def _build_test_metadata(
    sample_id: str,
    sample_class: str,
    filename: str,
    objects: list[dict[str, Any]],
    golden_ref_key: str,
    split: str,
    img_width: int = 0,
    img_height: int = 0,
    n_pass_crops: int = 0,
    min_crop_w: int = 32,
    min_crop_h: int = 32,
    max_crop_w: int = 128,
    max_crop_h: int = 128,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    defect_details = [
        {"defectType": obj.get("label", "defect"), "count": 1}
        for obj in objects
    ]
    return {
        "version": "1.0.0",
        "description": f"{_DATASET_NAME} | split={split} | sample={sample_id} | class={sample_class}",
        "timestamp": _now_iso(),
        "tag": f"aoi1-{split}",
        "machineInfo": {
            "machineId": "",
            "location": "",
            "jobType": "",
        },
        "aoiDefects": {
            "count": len(objects),
            "details": defect_details,
        },
        "imageInfo": {
            "imageType": "pcb_tested",
            "path": filename,
            "golden-ref": golden_ref_key,
            "cropInfo": _build_crop_info(
                objects, sample_id,
                is_normal=(sample_class == "normal"),
                img_width=img_width,
                img_height=img_height,
                n_pass_crops=n_pass_crops,
                min_crop_w=min_crop_w,
                min_crop_h=min_crop_h,
                max_crop_w=max_crop_w,
                max_crop_h=max_crop_h,
                rng=rng,
            ),
        },
    }


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _post_batch(
    session: requests.Session,
    api_url: str,
    batch: list[dict[str, Any]],
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    if dry_run:
        logger.debug("[DRY RUN] Would upload %d image(s)", len(batch))
        return [{"filename": item["filename"], "storage_key": f"dry-run/{item['filename']}"} for item in batch]
    try:
        resp = session.post(f"{api_url}/api/samples", json={"images": batch}, timeout=120)
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


def _size_aware_batches(
    items: list[dict[str, Any]], max_count: int
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    for item in items:
        item_bytes = len(item.get("data", ""))
        if batch and (len(batch) >= max_count or batch_bytes + item_bytes > _MAX_BATCH_BYTES):
            batches.append(batch)
            batch, batch_bytes = [], 0
        batch.append(item)
        batch_bytes += item_bytes
    if batch:
        batches.append(batch)
    return batches


def _upload_in_batches(
    session: requests.Session,
    api_url: str,
    items: list[dict[str, Any]],
    batch_size: int,
    delay_s: float,
    dry_run: bool,
    label: str,
) -> list[dict[str, Any]]:
    uploaded_all: list[dict[str, Any]] = []
    total_failed = 0
    batches = _size_aware_batches(items, batch_size)
    for i, batch in enumerate(batches):
        uploaded = _post_batch(session, api_url, batch, dry_run)
        uploaded_all.extend(uploaded)
        failed_count = len(batch) - len(uploaded)
        total_failed += failed_count
        if failed_count:
            uploaded_filenames = {u["filename"] for u in uploaded}
            for item in batch:
                if item["filename"] not in uploaded_filenames:
                    logger.error("%s: failed to upload %s", label, item["filename"])
        if delay_s > 0 and i < len(batches) - 1:
            time.sleep(delay_s)
    logger.info("%s: %d uploaded, %d failed (total %d)", label, len(uploaded_all), total_failed, len(items))
    return uploaded_all


# ---------------------------------------------------------------------------
# Per-split upload
# ---------------------------------------------------------------------------


def _upload_split(
    session: requests.Session,
    rows: list[dict[str, str]],
    dataset_dir: Path,
    api_url: str,
    split: str,
    batch_size: int,
    delay_s: float,
    dry_run: bool,
    n_pass_crops: int = 0,
    min_crop_w: int = 32,
    min_crop_h: int = 32,
    max_crop_w: int = 128,
    max_crop_h: int = 128,
) -> None:
    normal_count = sum(1 for r in rows if r["class"] == "normal")
    abnormal_count = len(rows) - normal_count
    logger.info(
        "=== Split: %s — %d total (%d normal, %d abnormal) ===",
        split.upper(), len(rows), normal_count, abnormal_count,
    )

    # ------------------------------------------------------------------
    # Phase 1: reference images
    # ------------------------------------------------------------------
    logger.info("[%s] Phase 1: uploading %d reference images", split, len(rows))
    ref_keys: dict[str, str] = {}  # sample_id -> storage_key
    ref_filename_to_sample: dict[str, str] = {}  # filename -> sample_id (for upload confirmation)
    ref_items: list[dict[str, Any]] = []
    missing_ref = 0

    for row in rows:
        img_path = dataset_dir / row["reference_image"]
        sample_id = row["id"]
        if not img_path.exists():
            logger.warning("Reference image not found: %s", img_path)
            missing_ref += 1
            continue
        if dry_run:
            ref_keys[sample_id] = f"dry-run/{img_path.name}"
            ref_items.append({"filename": img_path.name})
            continue
        loaded = _load_image(img_path)
        if loaded is None:
            missing_ref += 1
            continue
        raw, data, content_type, pil_w, pil_h = loaded
        width, height = int(row["tested_width"]), int(row["tested_height"])
        if (pil_w, pil_h) != (width, height):
            logger.warning("Ref image %s PIL size %dx%d differs from manifest %dx%d — using manifest",
                           img_path.name, pil_w, pil_h, width, height)
        ref_keys[sample_id] = _local_storage_key(raw, img_path.name)
        ref_filename_to_sample[img_path.name] = sample_id
        ref_items.append({
            "filename": img_path.name,
            "content_type": content_type,
            "data": data,
            "metadata": _build_ref_metadata(sample_id, img_path.name, split),
        })

    phase1_uploaded = _upload_in_batches(session, api_url, ref_items, batch_size, delay_s, dry_run,
                                         label=f"[{split}] Phase 1 reference images")
    if missing_ref:
        logger.warning("[%s] Phase 1: %d reference images not found on disk", split, missing_ref)

    # Drop ref_keys for samples whose Phase 1 upload was not confirmed by the server,
    # so Phase 2 doesn't embed a storage key pointing to a non-existent object.
    if not dry_run:
        confirmed_ref_filenames = {u["filename"] for u in phase1_uploaded}
        for filename, sid in ref_filename_to_sample.items():
            if filename not in confirmed_ref_filenames:
                logger.error(
                    "[%s] Phase 1: ref image %s (sample %s) not confirmed uploaded — "
                    "Phase 2 will skip this sample to avoid a broken golden-ref",
                    split, filename, sid,
                )
                ref_keys.pop(sid, None)

    # ------------------------------------------------------------------
    # Phase 2: tested images
    # ------------------------------------------------------------------
    logger.info("[%s] Phase 2: uploading %d tested images", split, len(rows))
    test_items: list[dict[str, Any]] = []
    missing_test = 0

    for row in rows:
        img_path = dataset_dir / row["tested_image"]
        sample_id = row["id"]
        sample_class = row["class"]
        if not img_path.exists():
            logger.warning("Tested image not found: %s", img_path)
            missing_test += 1
            continue

        annotation_path = dataset_dir / row["annotation"]
        try:
            objects = _load_annotation(annotation_path)
        except Exception:
            logger.error("[%s] Phase 2: skipping sample %s — annotation load failed for %s", split, sample_id, annotation_path)
            missing_test += 1
            continue
        golden_ref_key = ref_keys.get(sample_id, "")
        if not golden_ref_key:
            logger.error("[%s] Phase 2: sample %s has no confirmed Phase 1 ref upload — golden-ref will be empty", split, sample_id)

        if dry_run:
            test_items.append({"filename": img_path.name})
            continue
        loaded = _load_image(img_path)
        if loaded is None:
            missing_test += 1
            continue
        raw, data, content_type, pil_w, pil_h = loaded
        width, height = int(row["tested_width"]), int(row["tested_height"])
        if (pil_w, pil_h) != (width, height):
            logger.warning("Test image %s PIL size %dx%d differs from manifest %dx%d — using manifest",
                           img_path.name, pil_w, pil_h, width, height)
        test_items.append({
            "filename": img_path.name,
            "content_type": content_type,
            "data": data,
            "metadata": _build_test_metadata(
                sample_id, sample_class, img_path.name, objects,
                golden_ref_key, split,
                img_width=width,
                img_height=height,
                n_pass_crops=n_pass_crops,
                min_crop_w=min_crop_w,
                min_crop_h=min_crop_h,
                max_crop_w=max_crop_w,
                max_crop_h=max_crop_h,
                rng=random.Random(sample_id),
            ),
        })

    _upload_in_batches(session, api_url, test_items, batch_size, delay_s, dry_run,
                       label=f"[{split}] Phase 2 tested images")
    if missing_test:
        logger.warning("[%s] Phase 2: %d tested images not found on disk", split, missing_test)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def upload_nvpaw_dataset(
    dataset_dir: Path,
    api_url: str,
    split_selection: str = "all",
    train_ratio: float = 0.8,
    batch_size: int = 10,
    inter_batch_delay_s: float = 0.5,
    dry_run: bool = False,
    n_pass_crops: int = 0,
    min_crop_w: int = 32,
    min_crop_h: int = 32,
    max_crop_w: int = 128,
    max_crop_h: int = 128,
) -> None:
    csv_path = dataset_dir / "manifest.csv"
    if not csv_path.exists():
        logger.error("manifest.csv not found in %s", dataset_dir)
        sys.exit(1)

    with open(csv_path, newline="") as f:
        # Sort by sample_index so the split is deterministic regardless of CSV row order.
        rows = sorted(csv.DictReader(f), key=lambda r: int(r["sample_index"]))

    train_rows, eval_rows = _stratified_split(rows, train_ratio)
    logger.info(
        "Dataset: %d total → train %d, eval %d",
        len(rows), len(train_rows), len(eval_rows),
    )

    splits_to_run: list[tuple[str, list[dict[str, str]]]] = []
    if split_selection in ("train", "all"):
        splits_to_run.append(("train", train_rows))
    if split_selection in ("eval", "all"):
        splits_to_run.append(("eval", eval_rows))

    with requests.Session() as session:
        session.headers["Content-Type"] = "application/json"
        for split_name, split_rows in splits_to_run:
            _upload_split(
                session, split_rows, dataset_dir, api_url,
                split_name, batch_size, inter_batch_delay_s, dry_run,
                n_pass_crops=n_pass_crops,
                min_crop_w=min_crop_w,
                min_crop_h=min_crop_h,
                max_crop_w=max_crop_w,
                max_crop_h=max_crop_h,
            )


def _parse_size(value: str, flag: str, parser: argparse.ArgumentParser) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in value.lower().split("x"))
        if w <= 0 or h <= 0:
            raise ValueError
        return w, h
    except (ValueError, TypeError):
        parser.error(f"{flag} must be WxH with positive integers, e.g. 32x32")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset-dir", required=True, type=Path,
                   help="Path to the NVPAW_DET dataset folder (contains manifest.csv and images/)")
    p.add_argument("--api-url", default="http://localhost:8008",
                   help="Backend API base URL (default: http://localhost:8008)")
    p.add_argument("--split", default="all", choices=["train", "eval", "all"],
                   help="Which split to upload: train, eval, or all (default: all)")
    p.add_argument("--batch-size", type=int, default=10, metavar="N",
                   help="Images per upload batch, max 10 (backend limit). Default: 10")
    p.add_argument("--inter-batch-delay", type=float, default=0.5, metavar="SECONDS",
                   help="Delay between batches in seconds (default: 0.5)")
    p.add_argument("--train-ratio", type=float, default=0.8, metavar="RATIO",
                   help="Fraction of data for training split, e.g. 0.8 (default: 0.8)")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate paths and log counts without uploading")
    p.add_argument("--num-pass-crops", type=int, default=0, metavar="N",
                   help="Number of synthetic PASS crops to add per tested image (default: 0 = disabled). "
                        "On normal boards crops are placed anywhere within bounds; on abnormal boards "
                        "they avoid all annotated bounding boxes.")
    p.add_argument("--pass-crop-min-size", default="32x32", metavar="WxH",
                   help="Minimum size of synthetic PASS crops as WxH pixels (default: 32x32)")
    p.add_argument("--pass-crop-max-size", default="128x128", metavar="WxH",
                   help="Maximum size of synthetic PASS crops as WxH pixels (default: 128x128)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = p.parse_args()
    if not 1 <= args.batch_size <= 10:
        p.error("--batch-size must be between 1 and 10 (backend limit)")
    args.min_crop_w, args.min_crop_h = _parse_size(args.pass_crop_min_size, "--pass-crop-min-size", p)
    args.max_crop_w, args.max_crop_h = _parse_size(args.pass_crop_max_size, "--pass-crop-max-size", p)
    if args.min_crop_w > args.max_crop_w or args.min_crop_h > args.max_crop_h:
        p.error("--pass-crop-min-size must be <= --pass-crop-max-size")
    return args


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=getattr(logging, args.log_level),
    )
    upload_nvpaw_dataset(
        dataset_dir=args.dataset_dir,
        api_url=args.api_url.rstrip("/"),
        split_selection=args.split,
        train_ratio=args.train_ratio,
        batch_size=args.batch_size,
        inter_batch_delay_s=args.inter_batch_delay,
        dry_run=args.dry_run,
        n_pass_crops=args.num_pass_crops,
        min_crop_w=args.min_crop_w,
        min_crop_h=args.min_crop_h,
        max_crop_w=args.max_crop_w,
        max_crop_h=args.max_crop_h,
    )
