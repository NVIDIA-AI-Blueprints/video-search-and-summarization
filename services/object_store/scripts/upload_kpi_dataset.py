#!/usr/bin/env python3
"""
One-time loader that populates the AOI object store with KPI dataset
and creates an immutable KPI set used as the offline evaluation gate for model
promotion (kpi_eval(baseline, candidate, kpi_set)).

Expected input layout:
    <root dir>/
        <kpi-dir>/
            testing_set.csv            # ground-truth labels per component, KPI
            images/
                <project>/AOI_B/...    # test component crop images (tag=kpi)
                <project>/AOI_T/...    # test component crop images (tag=kpi)
                golden/images/.../     # golden reference crops    (tag=kpi-golden)
        <train dir>/
            <base>/
                train_set.csv          # train set
                validation_set.csv.    # validation set

Three-phase execution:
  Phase 1 — Upload golden reference images (tag="kpi-golden"). Their storage
             keys are captured so test images can reference them via golden-ref.
  Phase 2 — Upload test images (tag="kpi") with golden-ref pointing to the
             Phase 1 storage key for that component. Both AOI_B and AOI_T are
             uploaded. Rows with is_valid=0 are skipped by default.
  Phase 3 — (optional) POST /v1/aoi/kpi to freeze the uploaded images into an
             immutable KPI set manifest. Prints the kpi_id on success.

The kpi_id returned by Phase 3 is the stable reference DFW uses:
  GET /v1/kpi/{kpi_id}  →  manifest URIs  →  21 134 records with
    file_path   (test image S3 key)
    goldenRef   (golden reference S3 key)
    cropInfo[0].label  (ground truth: PASS / Missing / Shift / …)

This script is meant to be run ONCE per dataset version. Re-running is safe:
the storage key is deterministic (sha256 of image bytes), so already-uploaded
images are overwritten in place and re-indexed in ES via Kafka/Logstash.

Install deps (from repo root):
    pip install -e scripts/    # or: uv pip install -e scripts/

Usage:
    python scripts/upload_kpi_dataset.py \\
        --kpi-dir ~/Downloads/workspace/kpi \\
        --api-url http://localhost:8008 \\
        --create-kpi-set \\
        [--batch-size 10] \\
        [--inter-batch-delay 0.1] \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from PIL import Image

logger = logging.getLogger(__name__)

_COMP_TYPE_TO_PACKAGE = {
    "C": "Capacitor",
    "R": "Resistor",
    "U": "Integrated Circuit",
    "L": "Inductor",
    "J": "Connector",
    "D": "Diode",
    "Q": "Transistor",
    "F": "Fuse",
    "Y": "Crystal",
}

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
}

MACHINE_LOCATION = "KPI Dataset"


def _parse_timestamp(folder_name: str) -> str:
    """Extract ISO-8601 UTC timestamp from inspection folder name.

    Format: FXLH_{project}_{boardname}_{type}_{side}_{YYYYMMDDHHMMSS}
    Falls back to current time if parsing fails.
    """
    try:
        ts_raw = folder_name.rsplit("_", 1)[-1]
        dt = datetime.strptime(ts_raw, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (ValueError, IndexError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_machine_id(folder_name: str) -> str:
    return folder_name.split("_")[0]


def _crop_item(
    object_name: str,
    label: str,
    comp_type: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    package_type = _COMP_TYPE_TO_PACKAGE.get(comp_type.upper(), comp_type or "Unknown")
    return {
        "id": object_name,
        "label": label,
        "packageType": package_type,
        "crop": {
            "cropType": "component",
            # Every image in a KPI dataset is already a single component crop, so
            # the crop box is the whole image. Consumers cut the component out of
            # the stored image using these coordinates and discard the record when
            # the box has no area.
            "coordinates": {"top": 0, "left": 0, "width": width, "height": height},
        },
        # Ground truth, not the machine verdict: consumers derive the class from
        # defectType and treat any value other than "PASS" as a defect of that name.
        "aoiDefectInfo": {"defectType": label},
        "referenceLabels": None,
    }


def _bounded_crop_size(
    width: int,
    height: int,
    golden_size: tuple[int, int] | None,
    object_name: str,
) -> tuple[int, int]:
    """Clamp a test crop box so it also fits inside its golden reference.

    Consumers cut the component out of BOTH the test image and its golden
    reference using the same coordinates. The box is anchored at (0, 0), so
    staying in-bounds just means not exceeding either image. A test image larger
    than its golden would otherwise describe a box that overruns the golden and
    yields a truncated crop — or a discarded record.

    Falls back to the test image's own size when the golden size is unknown
    (golden missing on disk, or a dry run).
    """
    if golden_size is None:
        return width, height

    golden_w, golden_h = golden_size
    bounded = (min(width, golden_w), min(height, golden_h))
    if bounded != (width, height):
        logger.warning(
            "%s: crop box %dx%d exceeds golden reference %dx%d — clamped to %dx%d",
            object_name, width, height, golden_w, golden_h, bounded[0], bounded[1],
        )
    return bounded


def _build_golden_metadata(object_name: str, tag :str, comp_type: str, width: int, height: int) -> dict[str, Any]:
    """Minimal metadata for a golden reference image."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return {
        "version": "1.0.0",
        "description": "KPI dataset — golden reference crop",
        "timestamp": now,
        "tag": tag,
        "machineInfo": {
            "machineId": "golden-ref",
            "location": MACHINE_LOCATION,
            "jobType": "reference",
        },
        "aoiDefects": {"count": 0, "details": []},
        "imageInfo": {
            "imageType": "golden_reference",
            "path": f"{object_name}_SolderLight.jpg",
            "golden-ref": "",
            "cropInfo": [_crop_item(object_name, "PASS", comp_type, width, height)],
        },
    }


def _build_test_metadata(
    row: dict[str, str],
    tag,
    golden_storage_key: str,
    width: int,
    height: int,
    golden_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Build the AOI metadata dict for one test-image CSV row.

    golden_size is the (width, height) of this row's golden reference; the crop
    box is clamped to it so the box is valid against both images.
    """
    input_path = row["input_path"]
    parts = input_path.rstrip("/").split("/")
    aoi_side = parts[1] if len(parts) > 1 else "AOI"
    inspection_folder = parts[2] if len(parts) > 2 else ""

    timestamp = _parse_timestamp(inspection_folder)
    machine_id = _parse_machine_id(inspection_folder) or "FXLH"

    label = row["label"]
    object_name = row["object_name"]
    comp_type = row.get("comp_type_2", "")

    is_pass = label == "PASS"
    crop_w, crop_h = _bounded_crop_size(width, height, golden_size, object_name)
    return {
        "version": "1.0.0",
        "description": "KPI dataset — AOI component crop",
        "timestamp": timestamp,
        "tag": tag,
        "machineInfo": {
            "machineId": machine_id,
            "location": MACHINE_LOCATION,
            "jobType": aoi_side,
            "batchId": row.get("boardname", ""),
        },
        "aoiDefects": {
            "count": 0 if is_pass else 1,
            "details": [] if is_pass else [{"defectType": label, "count": 1}],
        },
        "imageInfo": {
            "imageType": "component_crop",
            "path": f"{object_name}_SolderLight.jpg",
            "golden-ref": golden_storage_key,
            "cropInfo": [_crop_item(object_name, label, comp_type, crop_w, crop_h)],
        },
    }


def _local_storage_key(image_bytes: bytes, filename: str) -> str:
    """Compute the storage key the backend assigns: deft-aoi/{sha256[:16]}/{filename}.

    Mirrors backend/api/service.py so we know the golden image's key before
    uploading, eliminating any response-tracking ambiguity.
    """
    return f"deft-aoi/{hashlib.sha256(image_bytes).hexdigest()[:16]}/{filename}"


def _load_image(image_path: Path) -> tuple[bytes, str, str, int, int] | None:
    """Return (raw_bytes, base64_data, content_type, width, height) or None on failure.

    The dimensions become the crop box in _crop_item, so an image whose header
    cannot be read is skipped rather than uploaded with a fabricated size.
    """
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
        logger.error("Refusing %s: image reports a zero-area size %dx%d", image_path, width, height)
        return None
    return raw, base64.b64encode(raw).decode("ascii"), content_type, width, height


def _post_batch(
    session: requests.Session,
    api_url: str,
    batch: list[dict[str, Any]],
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Upload a batch. Returns list of uploaded items (with storage_key) on success, [] on failure."""
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
            logger.warning("Server rejected: %s — %s", f.get("filename"), f.get("error"))
        return result.get("uploaded", [])
    except requests.RequestException as exc:
        logger.error("Request error: %s", exc)
        return []


_MAX_BATCH_BYTES = 9 * 1024 * 1024  # stay under the backend's 10 MB request limit


def _size_aware_batches(
    items: list[dict[str, Any]], max_count: int
) -> list[list[dict[str, Any]]]:
    """Split items into batches respecting both count and total base64 payload size."""
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    for item in items:
        # base64 data is ~4/3 of raw bytes; len(data string) approximates payload size
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
    """Upload items in size-aware batches; return all uploaded response objects."""
    uploaded_all: list[dict[str, Any]] = []
    total_failed = 0
    batches = _size_aware_batches(items, batch_size)
    for i, batch in enumerate(batches):
        uploaded = _post_batch(session, api_url, batch, dry_run)
        uploaded_all.extend(uploaded)
        total_failed += len(batch) - len(uploaded)
        if delay_s > 0 and i < len(batches) - 1:
            time.sleep(delay_s)
    logger.info(
        "%s: %d uploaded, %d failed (total %d)",
        label, len(uploaded_all), total_failed, len(items),
    )
    return uploaded_all


def _create_kpi_set(
    session: requests.Session,
    api_url: str,
    reason: str | None = None,
    dry_run: bool = False,
) -> str | None:
    if dry_run:
        logger.info("[DRY RUN] Would POST /v1/aoi/kpi with tag=kpi")
        return "dry-run-kpi-id"
    payload: dict[str, Any] = {"selection": {"tag": "kpi"}}
    if reason:
        payload["reason"] = reason
    try:
        resp = session.post(f"{api_url}/v1/aoi/kpi", json=payload, timeout=30)
        if not resp.ok:
            logger.error("KPI set creation failed HTTP %d: %s", resp.status_code, resp.text[:300])
            return None
        kpi_id = resp.json().get("kpi_id")
        logger.info("KPI set created: kpi_id=%s", kpi_id)
        return kpi_id
    except requests.RequestException as exc:
        logger.error("KPI set creation request error: %s", exc)
        return None


def _poll_es_count(
    session: requests.Session,
    api_url: str,
    tag: str,
    expected: int,
    timeout_s: float = 120.0,
    poll_interval_s: float = 5.0,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = session.get(f"{api_url}/v1/kpi/count", params={"tag": tag}, timeout=30)
            if resp.ok:
                count = resp.json().get("count", 0)
                logger.info("ES indexed %d/%d records (tag=%s)…", count, expected, tag)
                if count >= expected:
                    return True
        except requests.RequestException as exc:
            logger.warning("ES count poll error: %s", exc)
        time.sleep(poll_interval_s)
    logger.error(
        "ES count for tag=%s did not reach %d within %.0fs", tag, expected, timeout_s
    )
    return False


def _poll_kpi_ready(
    session: requests.Session,
    api_url: str,
    kpi_id: str,
    timeout_s: float = 300.0,
    poll_interval_s: float = 5.0,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            resp = session.get(f"{api_url}/v1/kpi/{kpi_id}", timeout=30)
            if resp.ok:
                data = resp.json()
                status = data.get("status")
                if status == "ready":
                    return data
                if status == "failed":
                    logger.error("KPI %s reported failed: %s", kpi_id, data.get("errors"))
                    return None
        except requests.RequestException as exc:
            logger.warning("KPI poll error: %s", exc)
        time.sleep(poll_interval_s)
    logger.error("KPI %s did not reach ready within %.0fs", kpi_id, timeout_s)
    return None


_MAX_BATCH_COUNT = 10  # backend /api/samples cap per request


def upload_kpi_dataset(
    kpi_dir: Path,
    dataset: Path,
    api_url: str,
    batch_size: int = 10,
    skip_invalid: bool = True,
    create_kpi_set: bool = False,
    kpi_reason: str | None = None,
    inter_batch_delay_s: float = 0.5,
    kpi_ready_timeout_s: float = 3600.0,
    dry_run: bool = False,
    yes: bool = False,
) -> None:
    if batch_size > _MAX_BATCH_COUNT:
        logger.warning("--batch-size %d exceeds backend limit of %d; clamping", batch_size, _MAX_BATCH_COUNT)
        batch_size = _MAX_BATCH_COUNT
    golden_tag = ""
    tag = ""
    if str(dataset).find("testing_set") != -1:
        golden_tag = "kpi-golden"
        tag = "kpi"
    else:
        golden_tag = "aoi1-golden-train"
        tag = "aoi1-train"
    csv_path = kpi_dir / dataset
    images_base = kpi_dir / "kpi/images"

    if not csv_path.exists():
        logger.error("CSV not found: %s", csv_path)
        sys.exit(1)
    if not images_base.exists():
        logger.error("Images directory not found: %s", images_base)
        sys.exit(1)

    with open(csv_path, newline="") as f:
        all_rows = list(csv.DictReader(f))

    rows = [r for r in all_rows if not (skip_invalid and r.get("is_valid") == "0")]
    logger.info("CSV: %d total rows, %d valid, %d skipped (is_valid=0)", len(all_rows), len(rows), len(all_rows) - len(rows))

    # -------------------------------------------------------------------------
    # Pre-flight: check for existing tag=kpi records in ES
    # -------------------------------------------------------------------------
    pre_count = 0
    if create_kpi_set and not dry_run:
        try:
            with requests.Session() as _s:
                resp = _s.get(f"{api_url}/v1/kpi/count", params={"tag": "kpi"}, timeout=30)
            if resp.ok:
                pre_count = resp.json().get("count", 0)
        except requests.RequestException as exc:
            logger.warning("Could not query existing ES count (will treat as 0): %s", exc)
        if pre_count > 0:
            logger.warning(
                "ES already contains %d tag=kpi record(s) from a previous upload. "
                "These will be included in the new KPI set alongside the records uploaded now.",
                pre_count,
            )
            if not yes:
                answer = input("Continue and include existing records in the KPI set? [y/N] ").strip().lower()
                if answer not in ("y", "yes", "Y", "YES"):
                    logger.info("Aborted by user.")
                    sys.exit(0)

    # -------------------------------------------------------------------------
    # Phase 1: Upload golden reference images
    # -------------------------------------------------------------------------
    # Collect unique (golden_path, object_name) pairs from valid rows.
    seen_golden: dict[tuple[str, str], str] = {}  # (golden_path, object_name) -> comp_type
    for row in rows:
        key = (row["golden_path"], row["object_name"])
        if key not in seen_golden:
            seen_golden[key] = row.get("comp_type_2", "")

    logger.info("Phase 1: uploading %d unique golden reference images", len(seen_golden))

    # Compute storage keys locally (mirrors backend key formula) so there is no
    # dependency on the server response for tracking — avoids filename collisions
    # when images from different golden_path folders share the same filename.
    golden_keys: dict[tuple[str, str], str] = {}  # (golden_path, object_name) -> storage_key
    # Golden dimensions are kept so Phase 2 can clamp each test crop box to its
    # reference; a box that overruns the golden is not comparable.
    golden_sizes: dict[tuple[str, str], tuple[int, int]] = {}
    golden_items: list[dict[str, Any]] = []
    missing_golden = 0

    for (golden_path, object_name), comp_type in seen_golden.items():
        img_path = images_base / golden_path / f"{object_name}_SolderLight.jpg"
        if not img_path.exists():
            logger.warning("Golden image not found: %s", img_path)
            missing_golden += 1
            continue

        if dry_run:
            golden_keys[(golden_path, object_name)] = f"dry-run/{img_path.name}"
            golden_items.append({"filename": img_path.name})
        else:
            loaded = _load_image(img_path)
            if loaded is None:
                missing_golden += 1
                continue
            raw, data, content_type, width, height = loaded
            storage_key = _local_storage_key(raw, img_path.name)
            golden_keys[(golden_path, object_name)] = storage_key
            golden_sizes[(golden_path, object_name)] = (width, height)
            golden_items.append({
                "filename": img_path.name,
                "content_type": content_type,
                "data": data,
                "metadata": _build_golden_metadata(object_name, golden_tag, comp_type, width, height),
            })

    if missing_golden:
        logger.warning("Phase 1: %d golden images not found on disk", missing_golden)

    with requests.Session() as session:
        session.headers["Content-Type"] = "application/json"
        uploaded_golden = _upload_in_batches(
            session, api_url, golden_items, batch_size, inter_batch_delay_s, dry_run,
            label="Phase 1 golden images",
        )

    logger.info("Phase 1 done: %d golden images uploaded, %d missing", len(uploaded_golden), missing_golden)

    # -------------------------------------------------------------------------
    # Phase 2: Upload test images (AOI_B + AOI_T per-component crops)
    # -------------------------------------------------------------------------
    logger.info("Phase 2: uploading %d test images", len(rows))

    test_items: list[dict[str, Any]] = []
    missing_test = 0

    for row in rows:
        img_path = images_base / row["input_path"] / f"{row['object_name']}_SolderLight.jpg"
        if not img_path.exists():
            logger.warning("Test image not found: %s", img_path)
            missing_test += 1
            continue

        golden_storage_key = golden_keys.get((row["golden_path"], row["object_name"]), "")
        if not golden_storage_key:
            logger.debug("No golden key for (%s, %s) — golden-ref will be empty", row["golden_path"], row["object_name"])

        if dry_run:
            test_items.append({"filename": img_path.name})
        else:
            loaded = _load_image(img_path)
            if loaded is None:
                missing_test += 1
                continue
            _, data, content_type, width, height = loaded
            golden_size = golden_sizes.get((row["golden_path"], row["object_name"]))
            test_items.append({
                "filename": img_path.name,
                "content_type": content_type,
                "data": data,
                "metadata": _build_test_metadata(
                    row, tag, golden_storage_key, width, height, golden_size
                ),
            })

    with requests.Session() as session:
        session.headers["Content-Type"] = "application/json"
        uploaded_test = _upload_in_batches(
            session, api_url, test_items, batch_size, inter_batch_delay_s, dry_run,
            label="Phase 2 test images",
        )

        logger.info(
            "Phase 2 done: %d uploaded, %d missing on disk",
            len(uploaded_test), missing_test,
        )

        # -------------------------------------------------------------------------
        # Phase 3: Create KPI set
        # -------------------------------------------------------------------------
        if create_kpi_set:
            test_failed = len(test_items) - len(uploaded_test)
            if (test_failed > 0 or not uploaded_test) and not dry_run:
                logger.error(
                    "%d of %d test images failed to upload; "
                    "refusing to create KPI set with an incomplete record set. "
                    "Fix upload errors above and retry.",
                    test_failed if test_failed > 0 else len(test_items),
                    len(test_items),
                )
                sys.exit(1)

            if not dry_run:
                expected_total = pre_count + len(uploaded_test)
                if not _poll_es_count(session, api_url, "kpi", expected_total, timeout_s=kpi_ready_timeout_s):
                    logger.error("Aborting: ES has not indexed all uploaded records; retry once indexing catches up")
                    sys.exit(1)

            kpi_id = _create_kpi_set(session, api_url, reason=kpi_reason, dry_run=dry_run)
            if not kpi_id and not dry_run:
                logger.error("KPI set creation returned no kpi_id — check server logs for details")
                sys.exit(1)
            if kpi_id:
                print(kpi_id)
                if not dry_run:
                    data = _poll_kpi_ready(session, api_url, kpi_id, timeout_s=kpi_ready_timeout_s)
                    if data is None:
                        logger.error("KPI %s: could not confirm ready status; record count unverified", kpi_id)
                        sys.exit(1)
                    total_records = data.get("total_records")
                    expected_total = pre_count + len(uploaded_test)
                    if total_records is None or total_records < expected_total:
                        logger.error(
                            "KPI %s record count too low: expected at least %d (%d existing + %d new), got %s — "
                            "ES had not indexed all records at freeze time; re-run --create-kpi-set to freeze again",
                            kpi_id, expected_total, pre_count, len(uploaded_test), total_records,
                        )
                        sys.exit(1)
                    logger.info(
                        "KPI %s verified: %d total records (%d pre-existing + %d this run)",
                        kpi_id, total_records, pre_count, len(uploaded_test),
                    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kpi-dir", required=True, type=Path, help="Path to the root kpi folder")
    p.add_argument("--dataset", required=True, type=Path, help="Path to the dataset file (csv file: testing_set.csv, training_set.csv, validation_set.csv)")
    p.add_argument("--api-url", default="http://localhost:8008", help="Backend API base URL (default: http://localhost:8008)")
    p.add_argument("--batch-size", type=int, default=10, help="Images per upload batch (default: 10)")
    p.add_argument("--skip-invalid", action="store_true", default=True, help="Skip rows where is_valid=0 (default: True)")
    p.add_argument("--no-skip-invalid", dest="skip_invalid", action="store_false")
    p.add_argument("--inter-batch-delay", type=float, default=0.5, metavar="SECONDS", help="Delay between batches in seconds (default: 0.5)")
    p.add_argument("--create-kpi-set", action="store_true", help="Call POST /v1/aoi/kpi after uploading to create the KPI set")
    p.add_argument("--kpi-reason", default=None, help="Optional reason string for the KPI set creation")
    p.add_argument("--kpi-ready-timeout", type=float, default=3600.0, metavar="SECONDS", help="How long to poll for ES indexing and KPI ready status before failing (default: 3600s)")
    p.add_argument("--dry-run", action="store_true", help="Validate paths and log counts without uploading")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt when pre-existing tag=kpi records are found")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=getattr(logging, args.log_level),
    )
    upload_kpi_dataset(
        kpi_dir=args.kpi_dir,
        dataset=args.dataset,
        api_url=args.api_url.rstrip("/"),
        batch_size=args.batch_size,
        skip_invalid=args.skip_invalid,
        create_kpi_set=args.create_kpi_set,
        kpi_reason=args.kpi_reason,
        inter_batch_delay_s=args.inter_batch_delay,
        kpi_ready_timeout_s=args.kpi_ready_timeout,
        yes=args.yes,
        dry_run=args.dry_run,
    )
