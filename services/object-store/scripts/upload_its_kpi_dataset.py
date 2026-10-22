#!/usr/bin/env python3
r"""Validate and upload an ITS KPI dataset.

The script sends each image and its ITS/VSS metadata to ``POST /api/samples``.
With ``--create-kpi-set``, it then waits for the indexed ``tag=kpi`` records and
calls ``POST /v1/its/kpi``. S3, Kafka, and Elasticsearch remain internal to the
backend; this script does not need credentials for them. ``--api-url`` must
point to the ITS backend deployment, whose ``ES_INDEX`` selects ``sc_*``.

Expected input layout::

    <kpi-dir>/
      images/**/<stem>.jpg
      labels/**/<stem>.txt

Each image and label must share a unique stem using one of the ITS frame naming
formats validated by this script. The current San Tomas format carries a full
UTC capture time, while the Walsh format carries only a capture date;
Walsh records therefore use midnight UTC to represent that date-only precision.
For example::

    San Tomas pattern:
      <sensor>_<UTC timestamp>_<IPv4 octets>_<token>_frame_<number>.jpg
    San Tomas example:
      SanTomasIntersection_20260101T170200Z_10_31_177_247_0_frame_000489.jpg
      | sensor (sensorId): SanTomasIntersection
      | UTC timestamp (timestamp): 20260101T170200Z
      | IPv4 address (retained in frameId): 10.31.177.247
      | unclassified token (retained in frameId): 0
      | frame number (retained in frameId): 000489

    Walsh pattern:
      <sensor>_<capture date>_<filename token>_frame_<frame number>.jpg
    Walsh example:
      walsh_santomas_20170602_037_frame_026914.jpg
      | sensor (sensorId): walsh_santomas
      | capture date (timestamp at midnight UTC): 20170602
      | unclassified token (retained in frameId): 037
      | frame number (retained in frameId): 026914

The dataset does not define the meaning of the numeric filename token. It uses only existing ITS fields:
the sensor becomes ``sensorId``, the capture time becomes ``timestamp``, and the
complete suffix after the sensor name becomes ``frameId``. The camera address
and numeric token therefore remain losslessly traceable inside ``frameId``.

Labels use the 15-field KITTI format; a
sixteenth confidence field is also accepted and validated. Label rows are
converted to ITS ``objects`` metadata rather than uploaded as separate KITTI or
COCO artifacts.

Run from the repository root. First install the script dependencies::

    uv sync --project scripts

Replace the example mapping below with the approved mapping for the dataset and
repeat ``--class-map`` for every source class in the KITTI labels.

Validate without uploading::

    uv run --project scripts python scripts/upload_its_kpi_dataset.py \
      --kpi-dir /path/to/kpi \
      --api-url http://localhost:8008 \
      --class-map SOURCE_CLASS=TARGET_CLASS \
      --dry-run

The dry run checks image decoding, image/label pairing, filename identity,
class mappings, confidence values, duplicate image bytes, and bounding boxes.

Upload through ``POST /api/samples`` by removing ``--dry-run``. To upload and
then create the KPI dataset, add::

      --create-kpi-set \
      --reason "Initial ITS KPI baseline"

With ``--create-kpi-set``, the script calls ``POST /v1/its/kpi`` with
``{"selection": {"tag": "kpi"}}``. The ITS backend applies this filter to its
deployment-configured ``ES_INDEX`` (``sc_*``) and freezes all matching ITS
records at that moment, including records from earlier uploads. AOI runs as a
separate deployment and is not involved in this request. The returned
``kpi_id`` identifies the frozen set; no dataset ID or version is sent.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import io
import logging
import math
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image
import requests

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = {".jpg", ".jpeg"}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ITS_FRAME_NAME = re.compile(
    r"^(?P<sensor>.+?)"
    r"_(?P<ts>\d{8}T\d{6}Z)"
    r"_(?P<o1>\d{1,3})_(?P<o2>\d{1,3})_(?P<o3>\d{1,3})_(?P<o4>\d{1,3})"
    r"_(?P<filename_token>\d+)"
    r"_frame_(?P<frame>\d+)$"
)
_ITS_WALSH_FRAME_NAME = re.compile(
    r"^(?P<sensor>walsh_santomas)"
    r"_(?P<date>\d{8})"
    r"_(?P<filename_token>\d+)"
    r"_frame_(?P<frame>\d+)$"
)
_MAX_BATCH_COUNT = 10
_MAX_BATCH_BYTES = 9 * 1024 * 1024


class DatasetValidationError(ValueError):
    """The local ITS KPI dataset violates the upload contract."""


def _parse_frame_name(stem: str) -> dict[str, str] | None:
    """Split a current San Tomas or legacy Walsh filename into frame fields.

    Walsh filenames contain a date but no capture time. Midnight UTC records
    that date without inventing a time-of-day. The numeric token before
    ``frame`` has no documented meaning, so the complete suffix stays in
    ``frameId`` rather than assigning that token to an invented metadata field.
    """
    match = _ITS_FRAME_NAME.match(stem)
    if match is not None:
        try:
            captured_at = datetime.strptime(
                match.group("ts"), "%Y%m%dT%H%M%SZ"
            ).replace(tzinfo=timezone.utc)
            ipaddress.IPv4Address(
                ".".join(match.group("o1", "o2", "o3", "o4"))
            )
        except ValueError:
            return None
        sensor_id = match.group("sensor")
        return {
            "sensorId": sensor_id,
            "timestamp": captured_at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "frameId": stem[len(sensor_id) + 1 :],
        }

    match = _ITS_WALSH_FRAME_NAME.match(stem)
    if match is not None:
        try:
            capture_date = datetime.strptime(match.group("date"), "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
        sensor_id = match.group("sensor")
        return {
            "sensorId": sensor_id,
            "timestamp": capture_date.strftime("%Y-%m-%dT00:00:00.000Z"),
            "frameId": stem[len(sensor_id) + 1 :],
        }

    return None


def _validate_component(value: str, label: str) -> str:
    """Reject unsafe or empty class-map names before adding them to metadata."""
    if not _SAFE_COMPONENT.fullmatch(value):
        raise DatasetValidationError(
            f"{label} must match {_SAFE_COMPONENT.pattern!r}: {value!r}"
        )
    return value


def parse_class_map(values: Iterable[str]) -> dict[str, str]:
    """Build the explicit source-to-ITS class mapping required for validation."""
    result: dict[str, str] = {}
    for value in values:
        source, sep, target = value.partition("=")
        source, target = source.strip(), target.strip()
        if not sep or not source or not target:
            raise DatasetValidationError(
                f"class mapping must be SOURCE=TARGET, got {value!r}"
            )
        _validate_component(source, "source class")
        _validate_component(target, "target class")
        if source in result and result[source] != target:
            raise DatasetValidationError(
                f"source class {source!r} is mapped more than once"
            )
        result[source] = target
    if not result:
        raise DatasetValidationError("at least one --class-map is required")
    return result


def _collect_by_stem(
    root: Path, suffixes: set[str], description: str
) -> dict[str, Path]:
    """Index input files by stem so image/label pairs can be matched uniquely."""
    if not root.is_dir():
        raise DatasetValidationError(f"{description} directory not found: {root}")
    found: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if path.stem in found:
            raise DatasetValidationError(
                f"duplicate {description} stem {path.stem!r}: "
                f"{found[path.stem]} and {path}"
            )
        found[path.stem] = path
    if not found:
        raise DatasetValidationError(f"no {description} files found under {root}")
    return found


def _load_image(path: Path) -> tuple[bytes, int, int]:
    """Decode an image and return its bytes and dimensions for upload checks."""
    try:
        data = path.read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            width, height = image.size
    except (OSError, Image.DecompressionBombError) as exc:
        raise DatasetValidationError(f"cannot decode image {path}: {exc}") from exc
    if width <= 0 or height <= 0:
        raise DatasetValidationError(f"image has invalid dimensions: {path}")
    return data, width, height


def _parse_objects(
    path: Path,
    *,
    stem: str,
    width: int,
    height: int,
    class_map: dict[str, str],
) -> tuple[list[dict[str, Any]], Counter[str], Counter[str]]:
    """Convert one KITTI label file into validated ITS ``objects`` metadata."""
    objects: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    mapped_counts: Counter[str] = Counter()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DatasetValidationError(f"cannot read annotation {path}: {exc}") from exc

    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) not in (15, 16):
            raise DatasetValidationError(
                f"{path}:{line_number}: expected 15 KITTI fields or 16 fields "
                f"with a trailing score, got {len(fields)}"
            )
        source_class = fields[0]
        if source_class not in class_map:
            raise DatasetValidationError(
                f"{path}:{line_number}: class {source_class!r} is absent from "
                "the approved --class-map"
            )
        try:
            numeric = [float(value) for value in fields[1:]]
        except ValueError as exc:
            raise DatasetValidationError(
                f"{path}:{line_number}: non-numeric KITTI value"
            ) from exc
        if not all(math.isfinite(value) for value in numeric):
            raise DatasetValidationError(
                f"{path}:{line_number}: annotation contains NaN or infinity"
            )
        if len(fields) == 16 and not 0.0 <= numeric[-1] <= 1.0:
            raise DatasetValidationError(
                f"{path}:{line_number}: confidence must be between 0 and 1"
            )

        left, top, right, bottom = numeric[3:7]
        if left < 0 or top < 0 or right > width or bottom > height:
            raise DatasetValidationError(
                f"{path}:{line_number}: bbox {(left, top, right, bottom)} is "
                f"outside decoded image bounds {width}x{height}"
            )
        if right <= left or bottom <= top:
            raise DatasetValidationError(
                f"{path}:{line_number}: bbox has zero or negative area"
            )

        mapped_class = class_map[source_class]
        source_counts[source_class] += 1
        mapped_counts[mapped_class] += 1
        objects.append(
            {
                "objectId": f"{stem}:{len(objects)}",
                "type": mapped_class,
                "bbox": {
                    "leftX": left,
                    "topY": top,
                    "rightX": right,
                    "bottomY": bottom,
                },
            }
        )
    return objects, source_counts, mapped_counts


def build_upload_items(
    kpi_dir: Path, class_map: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the dataset and build every ``POST /api/samples`` image item."""
    images = _collect_by_stem(kpi_dir / "images", _IMAGE_SUFFIXES, "JPEG image")
    labels = _collect_by_stem(kpi_dir / "labels", {".txt"}, "KITTI label")
    missing_labels = sorted(set(images) - set(labels))
    orphan_labels = sorted(set(labels) - set(images))
    if missing_labels or orphan_labels:
        details = []
        if missing_labels:
            details.append(f"images without labels: {missing_labels[:10]}")
        if orphan_labels:
            details.append(f"labels without images: {orphan_labels[:10]}")
        raise DatasetValidationError("; ".join(details))

    items: list[dict[str, Any]] = []
    image_digests: dict[str, Path] = {}
    source_counts: Counter[str] = Counter()
    mapped_counts: Counter[str] = Counter()
    object_count = 0
    for stem in sorted(images):
        identity = _parse_frame_name(stem)
        if identity is None:
            raise DatasetValidationError(
                f"image stem does not match the ITS frame naming contract: {stem!r}"
            )
        image_data, width, height = _load_image(images[stem])
        digest = hashlib.sha256(image_data).hexdigest()
        if digest in image_digests:
            raise DatasetValidationError(
                f"duplicate image bytes: {images[stem]} and {image_digests[digest]}"
            )
        image_digests[digest] = images[stem]
        objects, frame_source_counts, frame_mapped_counts = _parse_objects(
            labels[stem],
            stem=stem,
            width=width,
            height=height,
            class_map=class_map,
        )
        source_counts.update(frame_source_counts)
        mapped_counts.update(frame_mapped_counts)
        object_count += len(objects)
        items.append(
            {
                "filename": images[stem].name,
                "content_type": "image/jpeg",
                "data": base64.b64encode(image_data).decode("ascii"),
                "metadata": {
                    "version": "4.0",
                    "id": identity["frameId"],
                    "timestamp": identity["timestamp"],
                    "sensorId": identity["sensorId"],
                    "tag": "kpi",
                    "objects": objects,
                },
            }
        )

    statistics = {
        "image_count": len(items),
        "annotation_count": object_count,
        "source_class_counts": dict(sorted(source_counts.items())),
        "mapped_class_counts": dict(sorted(mapped_counts.items())),
    }
    return items, statistics


def _size_aware_batches(
    items: list[dict[str, Any]], max_count: int
) -> list[list[dict[str, Any]]]:
    """Batch items within the backend's image-count and request-size limits."""
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    batch_bytes = 0
    for item in items:
        item_bytes = len(item["data"])
        if batch and (
            len(batch) >= max_count or batch_bytes + item_bytes > _MAX_BATCH_BYTES
        ):
            batches.append(batch)
            batch, batch_bytes = [], 0
        batch.append(item)
        batch_bytes += item_bytes
    if batch:
        batches.append(batch)
    return batches


def upload_items(
    session: requests.Session,
    *,
    api_url: str,
    items: list[dict[str, Any]],
    batch_size: int,
    delay_s: float,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Send all samples through ``POST /api/samples`` and reject partial batches."""
    if dry_run:
        logger.info("[dry run] would POST %d ITS images to /api/samples", len(items))
        return [
            {"filename": item["filename"], "storage_key": "dry-run"}
            for item in items
        ]

    uploaded: list[dict[str, Any]] = []
    batches = _size_aware_batches(items, batch_size)
    for index, batch in enumerate(batches):
        try:
            response = session.post(
                f"{api_url}/api/samples",
                json={"images": batch},
                timeout=120,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"POST /api/samples failed: {exc}") from exc
        if not response.ok:
            raise RuntimeError(
                f"POST /api/samples failed HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        body = response.json()
        failures = body.get("failed") or []
        batch_uploaded = body.get("uploaded") or []
        if failures or len(batch_uploaded) != len(batch):
            raise RuntimeError(
                f"POST /api/samples uploaded {len(batch_uploaded)}/{len(batch)} "
                f"images; failures={failures[:10]}"
            )
        uploaded.extend(batch_uploaded)
        logger.info(
            "uploaded batch %d/%d (%d images)", index + 1, len(batches), len(batch)
        )
        if delay_s > 0 and index < len(batches) - 1:
            time.sleep(delay_s)
    return uploaded


def get_kpi_count(session: requests.Session, *, api_url: str) -> int:
    """Read the searchable ``tag=kpi`` count through the backend API."""
    response = session.get(
        f"{api_url}/v1/kpi/count",
        params={"tag": "kpi"},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(
            f"GET /v1/kpi/count failed HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    count = response.json().get("count")
    if not isinstance(count, int):
        raise RuntimeError("GET /v1/kpi/count returned no integer count")
    return count


def wait_for_catalog(
    session: requests.Session,
    *,
    api_url: str,
    minimum_count: int,
    timeout_s: float,
    poll_interval_s: float = 2.0,
) -> int:
    """Wait for Logstash indexing before freezing the tag-based KPI selection."""
    deadline = time.monotonic() + timeout_s
    last_count = 0
    last_error: RuntimeError | None = None
    while time.monotonic() < deadline:
        try:
            last_count = get_kpi_count(session, api_url=api_url)
            last_error = None
        except RuntimeError as exc:
            # The deployment index may not exist until Logstash processes the
            # first uploaded sample. Treat that as eventual consistency while
            # polling instead of failing the workflow immediately.
            last_error = exc
            time.sleep(poll_interval_s)
            continue
        if last_count >= minimum_count:
            logger.info("ITS catalog contains %d tag=kpi records", last_count)
            return last_count
        time.sleep(poll_interval_s)
    error_detail = f"; last error: {last_error}" if last_error else ""
    raise TimeoutError(
        f"ITS catalog did not reach {minimum_count} records within {timeout_s}s "
        f"(last count: {last_count}{error_detail})"
    )


def create_kpi_set(
    session: requests.Session,
    *,
    api_url: str,
    reason: str | None,
    dry_run: bool,
) -> str:
    """Create a KPI from ``tag=kpi`` through ``POST /v1/its/kpi`` and return its ID."""
    payload: dict[str, Any] = {
        "selection": {"tag": "kpi"},
    }
    if reason:
        payload["reason"] = reason
    if dry_run:
        logger.info("[dry run] would POST /v1/its/kpi with %s", payload)
        return "dry-run-kpi-id"
    response = session.post(f"{api_url}/v1/its/kpi", json=payload, timeout=30)
    if not response.ok:
        raise RuntimeError(
            f"POST /v1/its/kpi failed HTTP {response.status_code}: {response.text[:500]}"
        )
    kpi_id = response.json().get("kpi_id")
    if not kpi_id:
        raise RuntimeError("POST /v1/its/kpi returned no kpi_id")
    return kpi_id


def wait_for_kpi(
    session: requests.Session,
    *,
    api_url: str,
    kpi_id: str,
    minimum_records: int,
    timeout_s: float,
    poll_interval_s: float = 2.0,
) -> dict[str, Any]:
    """Wait for KPI workers to finish and verify the frozen record count."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = session.get(f"{api_url}/v1/kpi/{kpi_id}", timeout=30)
        if response.ok:
            body = response.json()
            if body.get("status") == "failed":
                raise RuntimeError(f"KPI job failed: {body.get('errors')}")
            if body.get("status") == "ready":
                total_records = body.get("total_records")
                if not isinstance(total_records, int) or total_records < minimum_records:
                    raise RuntimeError(
                        f"KPI job exported {total_records} records; expected at least "
                        f"{minimum_records}"
                    )
                return body
        time.sleep(poll_interval_s)
    raise TimeoutError(f"KPI job {kpi_id} did not become ready within {timeout_s}s")


def _build_parser() -> argparse.ArgumentParser:
    """Define the single-command validation, upload, and KPI creation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kpi-dir", type=Path, required=True)
    parser.add_argument("--api-url", default="http://localhost:8008")
    parser.add_argument(
        "--class-map",
        action="append",
        default=[],
        metavar="SOURCE=TARGET",
        help="approved ITS class mapping; repeat for every source class",
    )
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--inter-batch-delay", type=float, default=0.5)
    parser.add_argument("--create-kpi-set", action="store_true")
    parser.add_argument("--reason", default=None)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def main() -> None:
    """Run validation, public-API upload, optional KPI creation, and verification."""
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if args.batch_size > _MAX_BATCH_COUNT:
        logger.warning(
            "--batch-size %d exceeds the backend limit; clamping to %d",
            args.batch_size,
            _MAX_BATCH_COUNT,
        )
        args.batch_size = _MAX_BATCH_COUNT

    try:
        # Phase 1 — Validate the local image/KITTI pairs and translate each
        # annotation into the ITS metadata sent through POST /api/samples.
        items, statistics = build_upload_items(
            args.kpi_dir.expanduser(), parse_class_map(args.class_map)
        )
        print("Dataset statistics:")
        print(statistics)
        api_url = args.api_url.rstrip("/")
        with requests.Session() as session:
            session.headers["Content-Type"] = "application/json"

            # Phase 2 — Capture the current tag=kpi count so Phase 4 can wait
            # for the newly uploaded records to become searchable.
            previous_count = 0
            if args.create_kpi_set and not args.dry_run:
                try:
                    previous_count = get_kpi_count(session, api_url=api_url)
                except RuntimeError as exc:
                    logger.warning(
                        "could not read the pre-upload tag=kpi count; "
                        "using zero as the indexing baseline: %s",
                        exc,
                    )

            # Phase 3 — Upload every image and its ITS metadata through the
            # public backend API. S3 and Kafka remain internal to the backend.
            uploaded = upload_items(
                session,
                api_url=api_url,
                items=items,
                batch_size=args.batch_size,
                delay_s=args.inter_batch_delay,
                dry_run=args.dry_run,
            )
            if not args.create_kpi_set:
                logger.info("uploaded %d ITS KPI images", len(uploaded))
                return

            if args.dry_run:
                print(
                    create_kpi_set(
                        session,
                        api_url=api_url,
                        reason=args.reason,
                        dry_run=True,
                    )
                )
                return

            # Phase 4 — Wait until the uploaded tag=kpi records are searchable,
            # then freeze the current ITS selection through POST /v1/its/kpi.
            indexed_count = wait_for_catalog(
                session,
                api_url=api_url,
                minimum_count=previous_count + len(uploaded),
                timeout_s=args.timeout,
            )
            kpi_id = create_kpi_set(
                session,
                api_url=api_url,
                reason=args.reason,
                dry_run=False,
            )

            # Phase 5 — Wait for the KPI workers to finish exporting the frozen
            # selection before reporting the new immutable kpi_id.
            wait_for_kpi(
                session,
                api_url=api_url,
                kpi_id=kpi_id,
                minimum_records=indexed_count,
                timeout_s=args.timeout,
            )
            print(kpi_id)
    except (DatasetValidationError, RuntimeError, TimeoutError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
