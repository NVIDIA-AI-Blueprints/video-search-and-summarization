# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
End-to-end driver for the dynamic-calibration flow.

Assumes the integration-test docker-compose stack is up (Kafka + an
``mdx-analytics`` container running a real ``main_*_app.py``) and acts as a
"fake video analytics api" against the live ``mdx-notification`` topic with
``key="calibration"``.

Scenarios
---------

1.  **upsert-all valid** -- full snapshot replacement with one sensor;
    file lands in ``CALIBRATION_DIR``, container logs show
    ``Reloading new calibration information...`` then
    ``Loaded N sensors in total``.
2.  **upsert valid** -- single-sensor merge; file lands, logs show
    ``upsert N sensors``.
3.  **delete valid** -- single-sensor removal; file lands, logs show
    ``delete N sensors``.
4.  **upsert-all schema-invalid** -- payload missing top-level
    ``calibrationType``; the listener validates pre-write, rejects
    (``calibration schema violation`` logged), and **no file** lands.
    Previously-good calibration on the worker side is untouched.
5.  **upsert schema-invalid** -- same trick on the per-sensor merge
    path; ``upsert`` uses the full schema, so a missing
    ``calibrationType`` is rejected identically.
6.  **delete schema-invalid** -- ``sensors[]`` items missing ``id``;
    the minimal delete schema rejects pre-write, no file lands.
7.  **stale timestamp** -- notification with a timestamp earlier than
    ``last_insert_timestamp``; listener skips it (no file written).

Each "valid" scenario asserts that the watcher's ``on_moved`` handler ran
``reload_data`` by polling for the corresponding container log line, with a
generous wall-clock deadline.

Usage::

    pipenv run python3 tests/integration/dynamic_calibration/dynamic_calibration_e2e.py \\
        [--bootstrap-servers localhost:9092] \\
        [--container mdx-analytics] \\
        [--calibration-dir /tmp/checkpoint/calibration] \\
        [--reload-timeout-sec 10] \\
        [--verbose]

Exit code is ``0`` if every scenario passes, ``1`` on the first hard
failure.
"""

import argparse
import json
import logging
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from confluent_kafka import Producer

NOTIFICATION_TOPIC = "mdx-notification"
CALIBRATION_KEY = "calibration"

# Default config for warehouse_2d / kafka integration test profile.
DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"
DEFAULT_CONTAINER = "mdx-analytics"
DEFAULT_CALIBRATION_DIR = "/tmp/checkpoint/calibration"
DEFAULT_RELOAD_TIMEOUT_SEC = 10.0

logger = logging.getLogger("dynamic_calibration_e2e")


# ---------------------------------------------------------------------------
# Producer helpers
# ---------------------------------------------------------------------------

def _make_producer(bootstrap_servers: str) -> Producer:
    return Producer({"bootstrap.servers": bootstrap_servers})


def _publish(producer: Producer, event_type: str, ts: datetime, body: dict[str, Any]) -> None:
    """Mirror CalibrationListener's wire contract.

    Key is the literal string ``"calibration"`` (filters out non-calibration
    notifications in the same topic). Headers carry ``event.type`` and
    ``timestamp`` (ISO-8601 with Z suffix). Value is the JSON body.
    """
    iso = ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    producer.produce(
        topic=NOTIFICATION_TOPIC,
        key=CALIBRATION_KEY,
        value=json.dumps(body).encode("utf-8"),
        headers=[
            ("event.type", event_type.encode("utf-8")),
            ("timestamp", iso.encode("utf-8")),
        ],
    )
    producer.flush(timeout=5)


def _now_iso() -> datetime:
    return datetime.now(timezone.utc)


def _filename_iso(ts: datetime) -> str:
    """Match how CalibrationListener constructs filenames (colons replaced)."""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H_%M_%S.%fZ")


# ---------------------------------------------------------------------------
# Container introspection helpers
# ---------------------------------------------------------------------------

def _calibration_files_in_container(container: str, calibration_dir: str) -> list[str]:
    """List ``.json`` files in the container's calibration directory."""
    proc = subprocess.run(
        [
            "docker", "exec", container, "python3", "-c",
            f"import os; "
            f"d={calibration_dir!r}; "
            f"print('\\n'.join(sorted(p for p in os.listdir(d) if p.endswith('.json'))) "
            f"if os.path.isdir(d) else '')",
        ],
        capture_output=True, text=True, timeout=10,
    )
    return [p for p in proc.stdout.splitlines() if p.endswith(".json")] if proc.returncode == 0 else []


def _container_logs_since(container: str, since: str) -> str:
    proc = subprocess.run(
        ["docker", "logs", "--since", since, container],
        capture_output=True, text=True, timeout=15,
    )
    return proc.stdout + proc.stderr


def _wait_for(predicate, timeout: float, interval: float = 0.25):
    """Poll ``predicate`` until truthy or timeout. Returns the truthy value or False."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(interval)
    return False


def _wait_for_log_line(container: str, needle: str, since: str, timeout: float) -> bool:
    return bool(_wait_for(
        lambda: needle in _container_logs_since(container, since),
        timeout=timeout,
    ))


def _wait_for_new_file(container: str, calibration_dir: str, before: set[str], timeout: float) -> str | None:
    """Poll until a new ``.json`` appears in the calibration dir, return its name."""
    res = _wait_for(
        lambda: next(iter(set(_calibration_files_in_container(container, calibration_dir)) - before), None),
        timeout=timeout,
    )
    return res or None


def _no_new_files(container: str, calibration_dir: str, before: set[str], settle_sec: float = 2.0) -> bool:
    """Assert no new files appear within ``settle_sec`` -- for negative scenarios."""
    time.sleep(settle_sec)
    after = set(_calibration_files_in_container(container, calibration_dir))
    return after == before


# ---------------------------------------------------------------------------
# Sample calibration payloads
# ---------------------------------------------------------------------------

def _good_sensor(sensor_id: str) -> dict[str, Any]:
    """A sensor object that satisfies the full vendored schema."""
    return {
        "type": "camera",
        "id": sensor_id,
        "origin": {"lat": 0.0, "lng": 0.0},
        "geoLocation": {"lat": 0.0, "lng": 0.0},
        "coordinates": {"x": 0.0, "y": 0.0},
        "scaleFactor": 1.0,
        "attributes": [],
        "place": [],
        "imageCoordinates": [],
        "globalCoordinates": [],
    }


def _good_upsert_all() -> dict[str, Any]:
    return {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "image",
        "sensors": [_good_sensor("e2e-sensor-A"), _good_sensor("e2e-sensor-B")],
    }


def _good_upsert(sensor_id: str) -> dict[str, Any]:
    return {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "image",
        "sensors": [_good_sensor(sensor_id)],
    }


def _good_delete(sensor_ids: list[str]) -> dict[str, Any]:
    return {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "image",
        "sensors": [{"id": sid} for sid in sensor_ids],
    }


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _snapshot_before(container: str, calibration_dir: str) -> tuple[set[str], str]:
    """Return (set-of-files, log-since-marker) snapshots."""
    files = set(_calibration_files_in_container(container, calibration_dir))
    since = "1s"  # newest 1 second; per-scenario re-anchored
    return files, since


def _run_positive(
    tag: str, producer: Producer, args, event_type: str, body: dict[str, Any],
    expected_log: str,
) -> str:
    """A "valid" scenario: publish, wait for new file, wait for the expected log line."""
    before, _ = _snapshot_before(args.container, args.calibration_dir)
    ts = _now_iso()
    _publish(producer, event_type, ts, body)
    fname = _wait_for_new_file(args.container, args.calibration_dir, before, args.reload_timeout_sec)
    if not fname:
        return f"FAIL {tag}: no new calibration file appeared in {args.reload_timeout_sec}s"
    if not _wait_for_log_line(args.container, expected_log, since="30s", timeout=args.reload_timeout_sec):
        return f"FAIL {tag}: expected log {expected_log!r} not seen after {fname}"
    logger.info(f"  -> file={fname} log seen ({expected_log!r})")
    return f"PASS {tag}"


def _run_rejected(
    tag: str, producer: Producer, args, event_type: str, body: dict[str, Any],
    expected_rejection_log: str,
) -> str:
    """A "valid envelope, invalid content" scenario: publish, expect the
    listener to reject pre-write -- NO file lands, the listener logs a
    rejection. Watcher-side validation never gets a chance to run.
    """
    before, _ = _snapshot_before(args.container, args.calibration_dir)
    ts = _now_iso()
    _publish(producer, event_type, ts, body)
    # First confirm the rejection log so we know the listener actually saw
    # and rejected the notification (otherwise "no new file" could just
    # mean Kafka hasn't delivered it yet).
    if not _wait_for_log_line(args.container, expected_rejection_log, since="30s", timeout=args.reload_timeout_sec):
        return f"FAIL {tag}: expected listener rejection log {expected_rejection_log!r} not seen"
    # Then confirm the listener did NOT write a file for the bad payload.
    if not _no_new_files(args.container, args.calibration_dir, before, settle_sec=2.0):
        return f"FAIL {tag}: listener wrote a file for a schema-violating payload"
    logger.info(f"  -> no file written, rejection seen ({expected_rejection_log!r})")
    return f"PASS {tag}"


def _run_silently_skipped(
    tag: str, producer: Producer, args, event_type: str, body: dict[str, Any], ts: datetime,
) -> str:
    """A stale-timestamp scenario: publish, expect NO new file."""
    before, _ = _snapshot_before(args.container, args.calibration_dir)
    _publish(producer, event_type, ts, body)
    if not _no_new_files(args.container, args.calibration_dir, before, settle_sec=3.0):
        return f"FAIL {tag}: a stale-timestamp notification produced a file (should have been skipped)"
    logger.info(f"  -> no new file (correctly skipped)")
    return f"PASS {tag}"


def scenario_upsert_all_valid(producer: Producer, args) -> str:
    return _run_positive(
        "upsert-all-valid", producer, args,
        event_type="upsert-all",
        body=_good_upsert_all(),
        expected_log="upsert-all 2 sensors",
    )


def scenario_upsert_valid(producer: Producer, args) -> str:
    # calibration_base.py:317 logs `<action> <pre_merge_count> sensors: dict_keys(...)`.
    # Count is state-dependent; `: dict_keys` is the stable per-action suffix
    # that distinguishes upsert/delete from `upsert-all` (which logs at line 310
    # with a different format).
    return _run_positive(
        "upsert-valid", producer, args,
        event_type="upsert",
        body=_good_upsert("e2e-upsert-sensor"),
        expected_log="- upsert ",
    )


def scenario_delete_valid(producer: Producer, args) -> str:
    return _run_positive(
        "delete-valid", producer, args,
        event_type="delete",
        body=_good_delete(["e2e-upsert-sensor"]),
        expected_log="- delete ",
    )


def scenario_upsert_all_schema_invalid(producer: Producer, args) -> str:
    bad = _good_upsert_all()
    del bad["calibrationType"]  # required top-level field
    return _run_rejected(
        "upsert-all-schema-invalid", producer, args,
        event_type="upsert-all",
        body=bad,
        expected_rejection_log="calibration schema violation",
    )


def scenario_upsert_schema_invalid(producer: Producer, args) -> str:
    # `upsert` action uses the full vendored schema (same as `upsert-all`); a
    # missing top-level `calibrationType` is enough to trip it.
    bad = _good_upsert("e2e-upsert-bad")
    del bad["calibrationType"]
    return _run_rejected(
        "upsert-schema-invalid", producer, args,
        event_type="upsert",
        body=bad,
        expected_rejection_log="calibration schema violation",
    )


def scenario_delete_schema_invalid(producer: Producer, args) -> str:
    bad = {
        "version": "1.0",
        "osmURL": "",
        "calibrationType": "image",
        "sensors": [{"type": "camera"}],  # missing id -> delete schema rejects
    }
    return _run_rejected(
        "delete-schema-invalid", producer, args,
        event_type="delete",
        body=bad,
        expected_rejection_log="calibration schema violation",
    )


def scenario_stale_timestamp(producer: Producer, args) -> str:
    """Publish with a timestamp earlier than the listener's last_insert_timestamp.

    This relies on prior scenarios having landed a notification with a "now"
    timestamp, so an explicit ``now - 1h`` should be older than the listener's
    tracked watermark.
    """
    stale_ts = _now_iso() - timedelta(hours=1)
    return _run_silently_skipped(
        "stale-timestamp", producer, args,
        event_type="upsert",
        body=_good_upsert(f"e2e-stale-{uuid.uuid4().hex[:6]}"),
        ts=stale_ts,
    )


SCENARIOS = [
    scenario_upsert_all_valid,
    scenario_upsert_valid,
    scenario_delete_valid,
    scenario_upsert_all_schema_invalid,
    scenario_upsert_schema_invalid,
    scenario_delete_schema_invalid,
    scenario_stale_timestamp,
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bootstrap-servers", default=DEFAULT_BOOTSTRAP_SERVERS)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--calibration-dir", default=DEFAULT_CALIBRATION_DIR)
    parser.add_argument(
        "--reload-timeout-sec", type=float, default=DEFAULT_RELOAD_TIMEOUT_SEC,
        help="Per-scenario wall-clock deadline for file-appears + reload-log.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    # Pre-flight: container must exist
    proc = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", args.container],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        logger.error(f"container {args.container!r} is not running; start the integration test stack first")
        return 1

    producer = _make_producer(args.bootstrap_servers)
    logger.info(f"Connected to Kafka at {args.bootstrap_servers}; targeting container {args.container!r}")

    results: list[str] = []
    failed = False
    for scenario in SCENARIOS:
        name = scenario.__name__.removeprefix("scenario_").replace("_", "-")
        logger.info(f"--- {name} ---")
        try:
            result = scenario(producer, args)
        except Exception as e:  # pragma: no cover (defensive: harness errors)
            result = f"FAIL {name}: harness exception: {e}"
        results.append(result)
        if result.startswith("FAIL"):
            logger.error(result)
            failed = True
            # Continue running the rest -- a single failure is informative,
            # but seeing the full picture (which others also fail) is more so.
        else:
            logger.info(result)

    logger.info("")
    logger.info("=== summary ===")
    for r in results:
        logger.info(f"  {r}")
    n_pass = sum(1 for r in results if r.startswith("PASS"))
    n_fail = sum(1 for r in results if r.startswith("FAIL"))
    logger.info(f"{n_pass} pass / {n_fail} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
