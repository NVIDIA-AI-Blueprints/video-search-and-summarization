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
Smaller-scope E2E for the dynamic-config flow.

Assumes the integration-test docker-compose stack is up (Kafka + an
``mdx-analytics`` container running a real ``main_*_app.py``) and acts as a
"fake video analytics api" against the live ``mdx-notification`` topic.

Scenarios
---------

1.  **Bootstrap (Flow B)** -- replay the listener's ``request-config``
    from topic offset 0, reply with ``upsert-all`` carrying the full
    config (including read-only sections like ``kafka``). Verify the
    listener strips read-only sections, writes the filtered subset, and
    every watchdog (main + each worker process) applies it.
2.  **Upsert success -- app** -- valid ``app[]`` patch ->
    ``ack=success``, file landed, all watchdogs applied.
3.  **Upsert success -- sensors** -- valid ``sensors[]`` patch -> same.
4.  **Upsert success -- mixed types** -- one valid item per
    value-validator helper kind (bool / int / float / enum / datetime /
    JSON-list / Pydantic-JSON / non-empty string) in a single upsert;
    full acceptance path through the live wire.
5.  **Upsert empty patch** -- ``{}`` -> ``ack=success`` (legitimate
    no-op, no file). The operator explicitly said "no changes" and the
    listener did exactly that; ``error`` stays ``null``.
6.  **Upsert non-dict** -- ``config=[1,2,3]`` -> ``ack=failure`` ("not
    a JSON object"), **no file**.
7.  **Upsert pure-forbidden** -- ``{"kafka": {...}}`` (no app/sensors)
    -> ``ack=failure`` ("read-only"), **no file**.
8.  **Upsert pure-bad-items** -- ``{"app": [{name: "", value: "x"}]}``
    -> ``ack=failure`` (per-item rejection), **no file**.
9.  **Upsert sensor-with-empty-configs** -- ``{"sensors": [{"id":
    "cam1", "configs": []}]}`` -> ``ack=failure`` ("empty configs not
    allowed"), **no file**. The shape is ambiguous (no-op vs wipe-all)
    so the validator forces the operator to disambiguate -- either
    omit the entry (no-op) or use a future explicit delete event.
10. **Upsert pure-non-allowlisted** -- ``{"app": [{name: "numProcesses",
    value: "8"}]}`` (a restart-required key) -> ``ack=failure``,
    ``error`` contains ``"not allowlisted"``, **no file**.
11. **Upsert pure-invalid-value** -- ``{"app": [{name:
    "behaviorMaxPoints", value: "0"}]}`` (below min) -> ``ack=failure``
    ("invalid"), **no file**.
12. **Upsert partial-with-forbidden** -- ``{"kafka", "app": [good]}``
    -> ``ack=partial-success``, file contains only the ``app`` subset
    (no ``kafka``), all watchdogs applied.
13. **Upsert partial-with-bad-item** -- ``{"app": [good, bad]}`` ->
    ``ack=partial-success``, file contains only the good item, all
    watchdogs applied.
14. **Upsert partial-with-non-allowlisted** -- mix of an allowlisted
    key and a restart-required key -> ``ack=partial-success``, file
    contains only the allowlisted item, all watchdogs applied.
15. **Upsert partial-with-invalid-value** -- mix of a valid item and a
    value-rejection (``trajDirectionMode="5"``) -> ``ack=partial-success``,
    file contains only the valid item, all watchdogs applied.
16. **Bootstrap empty-reply in window** -- restart the container, then
    deliver an empty ``upsert-all`` (``status=failure``, ``config=null``)
    within the new 15s bootstrap window. Verify the listener logs
    ``video analytics api had no config to send`` (and NOT
    ``bootstrap timed out``), no file lands, no apply happens.
    **MUST run last** -- the restart invalidates earlier-scenario state.

Each "all watchdogs applied" assertion counts ``applied config from
<filename>`` lines in the container's docker logs and requires
``--expected-worker-applies`` matches (default 5: 1 main + 4 worker
processes for ``warehouse_2d`` with kafka).

Usage::

    pipenv run python3 tests/integration/dynamic_config/dynamic_config_e2e.py \\
        [--bootstrap-servers localhost:9092] \\
        [--container mdx-analytics] \\
        [--config-dir /tmp/checkpoint/config] \\
        [--bootstrap-window-sec 5] \\
        [--ack-timeout-sec 15] \\
        [--expected-worker-applies 5] \\
        [--verbose]

Exit code is ``0`` if every scenario passes (Bootstrap may legitimately
``SKIP`` if the listener's request-config can't be found), ``1`` on the
first hard failure.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

from confluent_kafka import Consumer, KafkaError, Producer


logger = logging.getLogger("dynamic_config_e2e")

# Wire-format constants (match :mod:`config_publisher` / :mod:`config_listener`).
NOTIFICATION_TOPIC: str = "mdx-notification"
CONFIG_KEY: bytes = b"behavior-analytics-config"
EVENT_TYPE_REQUEST_CONFIG: str = "request-config"
EVENT_TYPE_UPSERT: str = "upsert"
EVENT_TYPE_UPSERT_ALL: str = "upsert-all"
EVENT_TYPE_ACK: str = "ack"


@dataclass
class ConfigEvent:
    """Decoded view of one ``mdx-notification`` message we care about."""
    event_type: str
    reference_id: str
    body: dict[str, Any]
    offset: int
    partition: int

    @property
    def status(self) -> str | None:
        return self.body.get("status")

    @property
    def config(self) -> dict[str, Any] | None:
        return self.body.get("config")

    @property
    def error(self) -> str | None:
        return self.body.get("error")


def _decode_message(msg) -> ConfigEvent | None:
    """Return ``None`` for messages this driver does not care about."""
    if msg.error() is not None:
        return None
    if msg.key() != CONFIG_KEY:
        return None
    headers = dict(msg.headers() or [])
    event_type_raw = headers.get("event.type")
    reference_id_raw = headers.get("reference-id")
    if event_type_raw is None or reference_id_raw is None:
        return None
    try:
        event_type = event_type_raw.decode("utf-8")
        reference_id = reference_id_raw.decode("utf-8")
        body = json.loads(msg.value().decode("utf-8")) if msg.value() else {}
    except Exception as exc:
        logger.warning("malformed message at offset=%s: %s", msg.offset(), exc)
        return None
    return ConfigEvent(
        event_type=event_type,
        reference_id=reference_id,
        body=body if isinstance(body, dict) else {},
        offset=msg.offset(),
        partition=msg.partition(),
    )


def _publish(
    producer: Producer,
    event_type: str,
    reference_id: str,
    body: dict[str, Any] | list[Any],
    *,
    timestamp_ms: int | None = None,
) -> None:
    """Write one message to the notification topic, mirroring ConfigPublisher's wire shape.

    :param int | None timestamp_ms: Override the Kafka record timestamp.
        Used by scenarios that exercise the listener's broker-timestamp
        handling (kafka direct-publisher reconstruction; out-of-range
        guard). When ``None`` (default), Kafka assigns its own ms-epoch.
    """
    produce_kwargs: dict[str, Any] = {
        "topic": NOTIFICATION_TOPIC,
        "key": CONFIG_KEY,
        "value": json.dumps(body).encode("utf-8"),
        "headers": [
            ("event.type", event_type.encode("utf-8")),
            ("reference-id", reference_id.encode("utf-8")),
        ],
    }
    if timestamp_ms is not None:
        produce_kwargs["timestamp"] = timestamp_ms
    producer.produce(**produce_kwargs)
    producer.flush(timeout=5)


def _wire_payload(config: Any) -> dict[str, Any]:
    """Build the on-the-wire body shape: ``{status, config, error}``."""
    return {"status": None, "config": config, "error": None}


def _config_files_in_container(container: str, config_dir: str) -> list[str]:
    """List ``.json`` files in the container's CONFIG_DIR."""
    proc = subprocess.run(
        [
            "docker", "exec", container, "python3", "-c",
            f"import os; "
            f"d={config_dir!r}; "
            f"print('\\n'.join(sorted(p for p in os.listdir(d) if p.endswith('.json'))) "
            f"if os.path.isdir(d) else '')",
        ],
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.splitlines() if p.endswith(".json")]


def _read_config_file_in_container(container: str, config_dir: str, name: str) -> str:
    """Read a single ``.json`` file from the container's CONFIG_DIR."""
    proc = subprocess.run(
        [
            "docker", "exec", container, "python3", "-c",
            f"import os; print(open(os.path.join({config_dir!r}, {name!r})).read())",
        ],
        capture_output=True, text=True, timeout=10,
    )
    return proc.stdout if proc.returncode == 0 else ""


def _count_apply_log_lines(container: str, filename: str) -> int:
    """
    Count ``applied config from <filename>`` lines in the container's docker logs.

    Each ``ConfigFileMonitor.on_moved`` that successfully runs ``apply`` emits
    one such line, so a fully-applied file produces ``N`` lines where ``N`` is
    the number of watchdogs (main + each worker process).
    """
    proc = subprocess.run(
        ["docker", "logs", container],
        capture_output=True, text=True, timeout=15,
    )
    needle = f"applied config from {filename}"
    return sum(1 for line in (proc.stdout + proc.stderr).splitlines() if needle in line)


def _restart_container(container: str) -> None:
    """``docker restart <container>``. Blocks until the daemon completes the restart."""
    subprocess.run(["docker", "restart", container], check=True, timeout=60)


def _container_logs_since(container: str, since: str) -> str:
    """Get container logs newer than ``since`` (e.g. ``"30s"`` or an ISO timestamp)."""
    proc = subprocess.run(
        ["docker", "logs", "--since", since, container],
        capture_output=True, text=True, timeout=15,
    )
    return proc.stdout + proc.stderr


def _wait_for_log_line(container: str, needle: str, since: str, timeout: float) -> bool:
    """Poll ``docker logs --since=<since>`` until ``needle`` appears, or timeout."""
    return bool(_wait_for(
        lambda: needle in _container_logs_since(container, since),
        timeout=timeout,
    ))


def _wait_for(predicate, timeout: float, interval: float = 0.5):
    """Poll ``predicate`` until truthy or timeout. Returns the truthy value or False."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return False


def _drain_with_timeout(consumer: Consumer, timeout: float, until=lambda ev: True):
    """Yield each :class:`ConfigEvent` up to ``timeout`` seconds; stop when ``until`` is truthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = consumer.poll(timeout=0.5)
        if msg is None:
            continue
        if msg.error() and msg.error().code() != KafkaError._PARTITION_EOF:
            logger.warning("consumer error: %s", msg.error())
            continue
        ev = _decode_message(msg)
        if ev is None:
            continue
        yield ev
        if until(ev):
            return


def _make_consumer(bootstrap_servers: str) -> Consumer:
    """
    Build a Kafka consumer for the notification topic.

    A unique group id per invocation makes each run independent.
    ``auto.offset.reset=earliest`` ensures we never race partition
    assignment against an already-published ack -- we always start from
    offset 0 and rely on per-scenario ``reference-id`` uniqueness to
    identify the ack we care about. Historical traffic is ignored by the
    filter; the topic is small enough that the scan is fast.
    """
    consumer = Consumer({
        "bootstrap.servers": bootstrap_servers,
        "group.id": f"dynamic-config-e2e-{uuid.uuid4().hex}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    })
    consumer.subscribe([NOTIFICATION_TOPIC])
    return consumer


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def _wait_for_ack(
    consumer: Consumer, ref_id: str, timeout: float, scenario_tag: str,
) -> ConfigEvent | None:
    """Drain the consumer until we see an ack with the target reference-id, or timeout."""
    for ev in _drain_with_timeout(
        consumer,
        timeout=timeout,
        until=lambda ev: ev.event_type == EVENT_TYPE_ACK and ev.reference_id == ref_id,
    ):
        if ev.event_type == EVENT_TYPE_ACK and ev.reference_id == ref_id:
            return ev
    logger.error("[%s] no ack with ref=%s within %ss", scenario_tag, ref_id, timeout)
    return None


def _wait_for_new_file(
    args, before: set[str], prefix: str, scenario_tag: str,
) -> str | None:
    """Wait for a new ``<prefix>*.json`` file to appear in CONFIG_DIR."""
    new_file = _wait_for(
        lambda: next(
            (
                f for f in _config_files_in_container(args.container, args.config_dir)
                if f.startswith(prefix) and f not in before
            ),
            None,
        ),
        timeout=args.ack_timeout_sec,
    )
    if not new_file:
        logger.error("[%s] no %s*.json file appeared within %ss",
                     scenario_tag, prefix, args.ack_timeout_sec)
    return new_file


def _verify_workers_applied(args, filename: str, scenario_tag: str) -> bool:
    """Verify all expected watchdogs ran ``apply`` on the given file."""
    expected = args.expected_worker_applies
    got = _wait_for(
        lambda: _count_apply_log_lines(args.container, filename) >= expected,
        timeout=args.ack_timeout_sec,
    )
    if not got:
        actual = _count_apply_log_lines(args.container, filename)
        logger.error(
            "[%s] only %d/%d watchdogs applied %s",
            scenario_tag, actual, expected, filename,
        )
        return False
    logger.info("[%s] %d watchdogs applied %s", scenario_tag, expected, filename)
    return True


def _no_new_files(args, before: set[str], scenario_tag: str, settle_sec: float = 1.5) -> bool:
    """Sleep briefly to let any (incorrect) writes happen, then assert nothing new appeared."""
    time.sleep(settle_sec)
    after = set(_config_files_in_container(args.container, args.config_dir))
    delta = sorted(after - before)
    if delta:
        logger.error("[%s] unexpected new files: %s", scenario_tag, delta)
        return False
    return True


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_bootstrap(args, producer: Producer) -> str:
    """Replay the listener's request-config and reply with upsert-all carrying the full config."""
    tag = "bootstrap"
    logger.info("[%s] looking for the listener's request-config in the topic...", tag)
    consumer = _make_consumer(args.bootstrap_servers)
    try:
        bootstrap_ref: str | None = None
        for ev in _drain_with_timeout(consumer, timeout=args.bootstrap_window_sec):
            if ev.event_type == EVENT_TYPE_REQUEST_CONFIG:
                bootstrap_ref = ev.reference_id
                logger.info("[%s] captured request-config (ref=%s, offset=%s)", tag, ev.reference_id, ev.offset)
                break
        if bootstrap_ref is None:
            logger.warning("[%s] no request-config seen within %ss; SKIP", tag, args.bootstrap_window_sec)
            return "SKIP"

        before = set(_config_files_in_container(args.container, args.config_dir))

        # the video analytics api typically sends the FULL config -- listener should extract
        # only app + sensors and ignore the rest.
        full_config = {
            "app": [{"name": "behaviorWatermarkSec", "value": "60"}],
            "sensors": [],
            "kafka": {"brokers": "should-be-ignored"},
            "redisStream": {"clientId": "should-be-ignored"},
        }
        _publish(producer, EVENT_TYPE_UPSERT_ALL, bootstrap_ref, _wire_payload(full_config))

        new_file = _wait_for_new_file(args, before, prefix="upsert-all-config-", scenario_tag=tag)
        if not new_file:
            return "FAIL"
        body = json.loads(_read_config_file_in_container(args.container, args.config_dir, new_file))
        if "kafka" in body or "redisStream" in body:
            logger.error("[%s] file body retained read-only sections: %s", tag, body)
            return "FAIL"
        if not _verify_workers_applied(args, new_file, tag):
            return "FAIL"
        logger.info("[%s] file landed: %s, body=%s", tag, new_file, body)
        return "PASS"
    finally:
        consumer.close()


def scenario_upsert_success_app(args, producer: Producer) -> str:
    """Valid app patch -> ack=success, file landed, all watchdogs applied."""
    tag = "upsert-success-app"
    return _run_upsert_scenario_with_file(
        args, producer, tag,
        config={"app": [{"name": "behaviorMaxPoints", "value": "400"}], "sensors": []},
        expected_status="success",
        verify_body=lambda body: (
            "kafka" not in body
            and any(item.get("name") == "behaviorMaxPoints" for item in body.get("app", []))
        ),
    )


def scenario_upsert_success_sensors(args, producer: Producer) -> str:
    """Valid sensors patch -> ack=success, file landed, all watchdogs applied."""
    tag = "upsert-success-sensors"
    return _run_upsert_scenario_with_file(
        args, producer, tag,
        config={
            "app": [],
            "sensors": [
                {"id": "e2eSensorId", "configs": [{"name": "tripwireMinPoints", "value": "10"}]},
            ],
        },
        expected_status="success",
        verify_body=lambda body: (
            any(s.get("id") == "e2eSensorId" for s in body.get("sensors", []))
        ),
    )


def scenario_upsert_success_mixed_types(args, producer: Producer) -> str:
    """One valid item per value-validator helper kind in a single upsert.

    Exercises the *acceptance* path through the live wire for each helper
    type that ConfigValidator dispatches to:

    | helper                     | key (value)                                          |
    |----------------------------|------------------------------------------------------|
    | ``_bool``                  | ``inSimulationMode = "true"``                        |
    | ``_int(min=0)``            | ``behaviorWatermarkSec = "45"``                      |
    | ``_float(min=0, max=1)``   | ``clusterThreshold = "0.85"``                        |
    | ``_enum("0","1","2")``     | ``trajDirectionMode = "1"``                          |
    | ``_datetime_iso8601_z``    | ``behaviorTimeThreshold = "2026-05-01T00:00:00.000Z"``|
    | ``_json_list_of_str``      | ``stateManagementFilter = '["Person","Forklift"]'``  |
    | ``_non_empty_str``         | ``fovCountViolationIncidentObjectType = "Person"``   |
    | ``_json_pydantic``         | ``anomalySpeedViolation`` (default Pydantic dump)    |

    All items must validate and apply, the file must contain every item,
    and every watchdog must run apply on it.

    Two helper kinds are NOT covered here because their failure paths are
    already exercised by other scenarios:

    * ``_int`` failure -> ``upsert-pure-invalid-value`` (#9).
    * ``_enum`` failure -> ``upsert-partial-with-invalid-value`` (#13).
    """
    tag = "upsert-success-mixed-types"

    # Lazy import: SpeedViolationConfig lives in the analytics package and
    # we import it just to dump a known-valid Pydantic JSON value rather
    # than hand-rolling and bit-rotting a literal.
    from mdx.analytics.core.schema.config import SpeedViolationConfig

    speed_violation_json = SpeedViolationConfig().model_dump_json()
    expected_app_keys = {
        "inSimulationMode",
        "behaviorWatermarkSec",
        "clusterThreshold",
        "trajDirectionMode",
        "behaviorTimeThreshold",
        "stateManagementFilter",
        "fovCountViolationIncidentObjectType",
    }
    return _run_upsert_scenario_with_file(
        args, producer, tag,
        config={
            "app": [
                {"name": "inSimulationMode", "value": "true"},
                {"name": "behaviorWatermarkSec", "value": "45"},
                {"name": "clusterThreshold", "value": "0.85"},
                {"name": "trajDirectionMode", "value": "1"},
                {"name": "behaviorTimeThreshold", "value": "2026-05-01T00:00:00.000Z"},
                {"name": "stateManagementFilter", "value": '["Person", "Forklift"]'},
                {"name": "fovCountViolationIncidentObjectType", "value": "Person"},
            ],
            "sensors": [
                {
                    "id": "e2eSensorId",
                    "configs": [
                        {"name": "anomalySpeedViolation", "value": speed_violation_json},
                    ],
                },
            ],
        },
        expected_status="success",
        verify_body=lambda body: (
            {item["name"] for item in body.get("app", [])} == expected_app_keys
            and any(
                s.get("id") == "e2eSensorId"
                and any(c.get("name") == "anomalySpeedViolation" for c in s.get("configs", []))
                for s in body.get("sensors", [])
            )
        ),
    )


def scenario_upsert_empty_patch(args, producer: Producer) -> str:
    """Empty patch -> ack=success (no-op), no file. The operator
    explicitly said "no changes" and the listener did exactly that;
    error stays ``null`` so the ack is not confused with a real
    failure on the wire."""
    tag = "upsert-empty-patch"
    return _run_upsert_scenario_no_file(
        args, producer, tag,
        config={},
        expected_status="success",
        expected_error_substr=None,
    )


def scenario_upsert_non_dict(args, producer: Producer) -> str:
    """config field is a list, not a dict -> ack=failure, no file."""
    tag = "upsert-non-dict"
    return _run_upsert_scenario_no_file(
        args, producer, tag,
        config=[1, 2, 3],
        expected_status="failure",
        expected_error_substr="JSON object",
    )


def scenario_upsert_pure_forbidden(args, producer: Producer) -> str:
    """Only forbidden sections (no app/sensors) -> ack=failure, no file."""
    tag = "upsert-pure-forbidden"
    return _run_upsert_scenario_no_file(
        args, producer, tag,
        config={"kafka": {"brokers": "x"}},
        expected_status="failure",
        expected_error_substr="kafka",
    )


def scenario_upsert_pure_bad_items(args, producer: Producer) -> str:
    """All app items malformed -> ack=failure, no file."""
    tag = "upsert-pure-bad-items"
    return _run_upsert_scenario_no_file(
        args, producer, tag,
        config={"app": [{"name": "", "value": "missing-name"}]},
        expected_status="failure",
        expected_error_substr="app[0]",
    )


def scenario_upsert_sensor_with_empty_configs(args, producer: Producer) -> str:
    """Sensor entry with id but zero configs -> ack=failure. The shape
    is ambiguous (no-op for x vs wipe-all-of-x), so the validator
    rejects it and asks the operator to disambiguate -- either omit
    the sensor entry (no-op) or wait for an explicit delete event."""
    tag = "upsert-sensor-with-empty-configs"
    return _run_upsert_scenario_no_file(
        args, producer, tag,
        config={"sensors": [{"id": "cam1", "configs": []}]},
        expected_status="failure",
        expected_error_substr="empty sensor configs not allowed",
    )


def scenario_upsert_partial_with_forbidden(args, producer: Producer) -> str:
    """Forbidden section + valid app item -> partial-success, file has only app."""
    tag = "upsert-partial-with-forbidden"
    return _run_upsert_scenario_with_file(
        args, producer, tag,
        config={
            "kafka": {"brokers": "should-be-rejected"},
            "app": [{"name": "behaviorStateTimeout", "value": "20"}],
        },
        expected_status="partial-success",
        expected_error_substr="kafka",
        verify_body=lambda body: (
            "kafka" not in body
            and any(item.get("name") == "behaviorStateTimeout" for item in body.get("app", []))
        ),
    )


def scenario_upsert_partial_with_bad_item(args, producer: Producer) -> str:
    """Mixed valid + bad app items -> partial-success, file has only the valid one."""
    tag = "upsert-partial-with-bad-item"
    return _run_upsert_scenario_with_file(
        args, producer, tag,
        config={
            "app": [
                {"name": "behaviorStateValidInterval", "value": "12"},
                {"name": "", "value": "missing-name"},
            ],
        },
        expected_status="partial-success",
        expected_error_substr="app[1]",
        verify_body=lambda body: (
            len(body.get("app", [])) == 1
            and body["app"][0]["name"] == "behaviorStateValidInterval"
        ),
    )


def scenario_upsert_pure_non_allowlisted(args, producer: Producer) -> str:
    """An app key not in ALLOWED_APP_KEYS (e.g. restart-required ``numProcesses``) -> ack=failure, no file."""
    tag = "upsert-pure-non-allowlisted"
    return _run_upsert_scenario_no_file(
        args, producer, tag,
        config={"app": [{"name": "numProcesses", "value": "8"}]},
        expected_status="failure",
        expected_error_substr="not allowlisted",
    )


def scenario_upsert_pure_invalid_value(args, producer: Producer) -> str:
    """Allowlisted key with a value that fails its per-key rule -> ack=failure, no file."""
    tag = "upsert-pure-invalid-value"
    return _run_upsert_scenario_no_file(
        args, producer, tag,
        # behaviorMaxPoints requires int >= 1; "0" is below the floor.
        config={"app": [{"name": "behaviorMaxPoints", "value": "0"}]},
        expected_status="failure",
        expected_error_substr="invalid",
    )


def scenario_upsert_partial_with_invalid_value(args, producer: Producer) -> str:
    """Allowlisted+valid alongside allowlisted+invalid -> partial-success; file holds only the valid item."""
    tag = "upsert-partial-with-invalid-value"
    return _run_upsert_scenario_with_file(
        args, producer, tag,
        config={
            "app": [
                {"name": "behaviorWatermarkSec", "value": "45"},  # valid int
                {"name": "trajDirectionMode", "value": "5"},      # invalid (enum 0/1/2)
            ],
        },
        expected_status="partial-success",
        expected_error_substr="invalid",
        verify_body=lambda body: (
            len(body.get("app", [])) == 1
            and body["app"][0]["name"] == "behaviorWatermarkSec"
        ),
    )


def scenario_upsert_partial_with_non_allowlisted(args, producer: Producer) -> str:
    """Allowlisted + non-allowlisted -> partial-success, file holds only the allowlisted item."""
    tag = "upsert-partial-with-non-allowlisted"
    return _run_upsert_scenario_with_file(
        args, producer, tag,
        config={
            "app": [
                {"name": "behaviorTimeThreshold", "value": "2026-01-01T00:00:00.000Z"},
                {"name": "numProcesses", "value": "8"},  # restart-required, must be rejected
            ],
        },
        expected_status="partial-success",
        expected_error_substr="not allowlisted",
        verify_body=lambda body: (
            len(body.get("app", [])) == 1
            and body["app"][0]["name"] == "behaviorTimeThreshold"
        ),
    )


def scenario_upsert_kafka_source_type(args, producer: Producer) -> str:
    """Direct-publisher upsert from the kafka source layer.

    When the active ``sourceType`` is ``kafka`` and the inbound
    ``reference-id`` is the literal string ``"kafka"`` (i.e. not the
    ``video-analytics-api-`` prefix), the listener accepts the message
    and rewrites the reference-id to ``f"kafka-{broker_timestamp_ms}"``
    so downstream dispatch and the ack carry a unique value.

    We force the Kafka record timestamp via :func:`_publish` so the
    rewritten ref-id is deterministic and the ack matcher can use it.
    """
    tag = "upsert-kafka-source-type"
    ts_ms = int(time.time() * 1000)
    body = _wire_payload({"app": [{"name": "behaviorMaxPoints", "value": "350"}], "sensors": []})
    return _run_raw_body_scenario(
        args, producer, tag,
        body=body,
        ref_id="kafka",
        timestamp_ms=ts_ms,
        expected_status="success",
        expect_file=True,
    )


def scenario_upsert_missing_status(args, producer: Producer) -> str:
    """Body omits the ``status`` key entirely. ``ConfigMessage.status``
    defaults to ``None``, so the envelope is well-formed and the listener
    runs the per-payload validator on ``config={}`` -- success no-op."""
    tag = "upsert-missing-status"
    return _run_raw_body_scenario(
        args, producer, tag,
        body={"config": {}, "error": None},
        expected_status="success",
        expected_error_substr=None,
    )


def scenario_upsert_missing_config(args, producer: Producer) -> str:
    """Body omits ``config`` -- envelope gate rejects with
    ``no config to update``."""
    tag = "upsert-missing-config"
    return _run_raw_body_scenario(
        args, producer, tag,
        body={"status": None, "error": None},
        expected_status="failure",
        expected_error_substr="no config to update",
    )


def scenario_upsert_null_config(args, producer: Producer) -> str:
    """Body has ``config=null`` -- envelope gate rejects (an upsert with
    no config to apply is a producer error, not a no-op)."""
    tag = "upsert-null-config"
    return _run_raw_body_scenario(
        args, producer, tag,
        body={"status": None, "config": None, "error": None},
        expected_status="failure",
        expected_error_substr="no config to update",
    )


def scenario_upsert_config_as_list(args, producer: Producer) -> str:
    """Body has ``config=[]`` -- pydantic accepts (``config: Any``); the
    per-payload validator rejects the non-dict with
    ``payload is not a JSON object``."""
    tag = "upsert-config-as-list"
    return _run_raw_body_scenario(
        args, producer, tag,
        body={"status": None, "config": [], "error": None},
        expected_status="failure",
        expected_error_substr="not a JSON object",
    )


def scenario_upsert_error_as_object(args, producer: Producer) -> str:
    """Body has ``error`` typed as a dict (producer-side typing bug) --
    pydantic rejects with ``invalid envelope shape``."""
    tag = "upsert-error-as-object"
    return _run_raw_body_scenario(
        args, producer, tag,
        body={"status": None, "config": {}, "error": {"message": "bad"}},
        expected_status="failure",
        expected_error_substr="invalid envelope shape",
    )


def scenario_upsert_invalid_broker_timestamp(args, producer: Producer) -> str:
    """Kafka record timestamp set to a value well beyond
    ``datetime.fromtimestamp`` can represent. The deserializer guards
    this with ``try/except (OverflowError, ValueError, OSError)`` and
    drops the message at the wire layer -- no ack is emitted (the
    listener never even constructed a ``ConfigMessage`` to dispatch
    against)."""
    tag = "upsert-invalid-broker-timestamp"
    body = _wire_payload({"app": [], "sensors": []})
    return _run_raw_body_scenario(
        args, producer, tag,
        body=body,
        timestamp_ms=10**18,  # ~31_700_000_000 years past epoch
        expected_status=None,
        expect_no_ack=True,
    )


def scenario_upsert_extra_envelope_field(args, producer: Producer) -> str:
    """Body carries a top-level key outside the expected envelope set --
    rejected with ``unrecognized envelope keys`` so the operator
    notices typos and contract drift in the ack."""
    tag = "upsert-extra-envelope-field"
    return _run_raw_body_scenario(
        args, producer, tag,
        body={"status": None, "config": {}, "error": None, "extra": True},
        expected_status="failure",
        expected_error_substr="unrecognized envelope keys",
    )


def scenario_bootstrap_empty_reply_in_window(args, producer: Producer) -> str:
    """
    Restart mdx-analytics, then deliver an empty ``upsert-all`` reply within
    its fresh 15s bootstrap window.

    Verifies the listener's "video analytics api had no config to send" branch:

    - Logs the warning ``video analytics api had no config to send``.
    - ``bootstrap timed out`` does NOT appear in the new startup's log
      section -- our reply unblocked ``start()`` before the 15s elapsed.
    - No new file lands in CONFIG_DIR.
    - No additional ``applied config from ...`` lines (no apply happened).

    NOTE: this scenario restarts the container, so it must run after every
    other scenario.
    """
    tag = "bootstrap-empty-reply-in-window"
    logger.info("[%s] capturing pre-restart state...", tag)
    pre_files = set(_config_files_in_container(args.container, args.config_dir))

    # Snapshot the topic's current high-water offset so we can identify the
    # NEW listener's request-config (vs. all the historical ones).
    # _drain_with_timeout's default ``until`` returns on the first event, so
    # use an always-False predicate to drain until the timeout actually elapses.
    # We also need a generous timeout because confluent-kafka's subscribe() is
    # lazy: the first 1-2s after consumer creation are spent on partition
    # assignment before any data flows.
    pre_consumer = _make_consumer(args.bootstrap_servers)
    pre_max_offset = -1
    for ev in _drain_with_timeout(pre_consumer, timeout=10.0, until=lambda ev: False):
        pre_max_offset = max(pre_max_offset, ev.offset)
    pre_consumer.close()
    logger.info("[%s] pre-restart topic high-water offset=%d", tag, pre_max_offset)
    if pre_max_offset < 0:
        # A truly fresh topic could in theory have nothing, but in this E2E
        # we ran 9 scenarios before this one, so we expect dozens of messages.
        logger.error(
            "[%s] pre-restart drain found no messages (subscription likely "
            "did not complete in time) -- bump the timeout further", tag,
        )
        return "FAIL"

    # Use ``docker logs --since`` to scope our log assertions to the new run.
    # Use the relative ``0s`` that snapshots "now" before the restart.
    pre_restart_marker = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    logger.info("[%s] restarting %s...", tag, args.container)
    try:
        _restart_container(args.container)
    except subprocess.CalledProcessError as exc:
        logger.error("[%s] docker restart failed: %s", tag, exc)
        return "FAIL"

    # Listen for the new listener's request-config (offset > pre_max_offset).
    consumer = _make_consumer(args.bootstrap_servers)
    new_ref: str | None = None
    deadline = time.time() + 60  # generous: container restart + app warm-up
    try:
        while time.time() < deadline and new_ref is None:
            msg = consumer.poll(timeout=0.5)
            if msg is None or msg.error() or msg.key() != CONFIG_KEY:
                continue
            ev = _decode_message(msg)
            if ev is None:
                continue
            if ev.event_type == EVENT_TYPE_REQUEST_CONFIG and ev.offset > pre_max_offset:
                new_ref = ev.reference_id
                logger.info("[%s] captured fresh request-config (ref=%s, offset=%d)",
                            tag, new_ref, ev.offset)
        if new_ref is None:
            logger.error("[%s] no fresh request-config seen within 60s after restart", tag)
            return "FAIL"

        # The listener's Kafka consumer uses ``auto.offset.reset=latest``, and
        # the consumer's subscription is async (first poll triggers join). If
        # we publish too fast, our reply can land at an offset that's ``latest``
        # at the moment of subscription -- the consumer starts AFTER it and
        # never sees our message. A small delay lets the listener's consumer
        # finish subscribing past the request-config's offset before we reply.
        # 3 seconds is well within the listener's 15s bootstrap window.
        time.sleep(3)

        _publish(producer, EVENT_TYPE_UPSERT_ALL, new_ref, {
            "status": "failure",
            "config": None,
            "error": "DB empty (test)",
        })
        logger.info("[%s] sent empty upsert-all (status=failure, config=null)", tag)
    finally:
        consumer.close()

    # Listener should log the warning quickly.
    if not _wait_for_log_line(
        args.container,
        "video analytics api had no config to send",
        since=pre_restart_marker,
        timeout=15,
    ):
        logger.error("[%s] missing 'video analytics api had no config to send' log", tag)
        return "FAIL"

    # The bootstrap-window timeout must NOT have fired -- we replied in time.
    # Wait the full window plus a margin to be sure no late timeout creeps in.
    # ``bootstrap completed via upsert-all reply`` IS expected here: ``start()``
    # logs that line whenever the bootstrap-done event fires (including via the
    # empty-reply path, which sets the event in its ``finally`` block).
    time.sleep(16)
    new_logs = _container_logs_since(args.container, since=pre_restart_marker)
    if "bootstrap timed out" in new_logs:
        logger.error("[%s] 'bootstrap timed out' fired despite in-window reply", tag)
        return "FAIL"

    # No new file should land.
    after_files = set(_config_files_in_container(args.container, args.config_dir))
    delta = sorted(after_files - pre_files)
    if delta:
        logger.error("[%s] unexpected new files: %s", tag, delta)
        return "FAIL"

    logger.info("[%s] in-window empty reply handled correctly: warning logged, "
                "no timeout, no apply, no file written", tag)
    return "PASS"


# ---------------------------------------------------------------------------
# Reusable scenario runners
# ---------------------------------------------------------------------------


def _run_upsert_scenario_with_file(
    args, producer: Producer, tag: str, *,
    config: Any,
    expected_status: str,
    verify_body=lambda body: True,
    expected_error_substr: str | None = None,
) -> str:
    """Common flow for scenarios that should land a file and apply on every watchdog."""
    logger.info("[%s] publishing...", tag)
    consumer = _make_consumer(args.bootstrap_servers)
    consumer.poll(timeout=0.5)

    before = set(_config_files_in_container(args.container, args.config_dir))
    ref_id = f"video-analytics-api-e2e-{tag}-{uuid.uuid4().hex[:8]}"
    _publish(producer, EVENT_TYPE_UPSERT, ref_id, _wire_payload(config))

    try:
        ack = _wait_for_ack(consumer, ref_id, args.ack_timeout_sec, tag)
        if ack is None:
            return "FAIL"
        logger.info("[%s] ack: status=%s error=%r", tag, ack.status, ack.error)
        if ack.status != expected_status:
            logger.error("[%s] expected status=%s, got %r", tag, expected_status, ack.status)
            return "FAIL"
        if expected_error_substr and (not ack.error or expected_error_substr not in ack.error):
            logger.error("[%s] error must contain %r: got %r",
                         tag, expected_error_substr, ack.error)
            return "FAIL"

        new_file = _wait_for_new_file(args, before, prefix="upsert-config-", scenario_tag=tag)
        if not new_file:
            return "FAIL"
        body_text = _read_config_file_in_container(args.container, args.config_dir, new_file)
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError as exc:
            logger.error("[%s] file body is not JSON: %s\n%s", tag, exc, body_text)
            return "FAIL"
        if not verify_body(body):
            logger.error("[%s] file body did not match expectations: %s", tag, body)
            return "FAIL"
        if not _verify_workers_applied(args, new_file, tag):
            return "FAIL"
        logger.info("[%s] file landed: %s, body=%s", tag, new_file, body)
        return "PASS"
    finally:
        consumer.close()


def _run_raw_body_scenario(
    args, producer: Producer, tag: str, *,
    body: Any,
    ref_id: str | None = None,
    expected_status: str | None,
    expected_error_substr: str | None = None,
    expect_no_ack: bool = False,
    expect_file: bool = False,
    timestamp_ms: int | None = None,
) -> str:
    """Generic envelope-level scenario runner -- the caller controls the
    full wire body so we can exercise paths that the ``config``-only
    helpers cannot reach (missing/null/extra envelope keys, error-as-object,
    broker-timestamp guard, etc.).

    :param dict | list body: Exact JSON body to send -- NOT wrapped via
        ``_wire_payload``. Callers building a "normal" envelope should
        include ``status`` / ``config`` / ``error`` themselves.
    :param str | None ref_id: Inbound reference-id. Defaults to a unique
        ``e2e-<tag>-<hex>``; pass an exact value for the kafka-source
        scenario (``ref_id="kafka"``).
    :param str | None expected_status: Expected ack status when
        ``expect_no_ack`` is False. ``None`` here means "the ack carries
        ``error=null``" (success no-op shape); set
        ``expect_no_ack=True`` if no ack should arrive at all.
    :param bool expect_no_ack: True when the listener should drop the
        message at the deserializer (no ack emitted) -- used by the
        invalid-broker-timestamp scenario.
    :param bool expect_file: True when the scenario should leave a
        ``upsert-config-*.json`` file in CONFIG_DIR (success path).
    :param int | None timestamp_ms: Override Kafka record timestamp;
        forwarded to :func:`_publish`.
    """
    logger.info("[%s] publishing...", tag)
    consumer = _make_consumer(args.bootstrap_servers)
    consumer.poll(timeout=0.5)

    before = set(_config_files_in_container(args.container, args.config_dir))
    if ref_id is None:
        ref_id = f"video-analytics-api-e2e-{tag}-{uuid.uuid4().hex[:8]}"
    _publish(producer, EVENT_TYPE_UPSERT, ref_id, body, timestamp_ms=timestamp_ms)

    try:
        if expect_no_ack:
            # The listener's deserializer dropped the record -- no ack
            # was emitted. Confirm absence within the timeout window.
            ack = _wait_for_ack(consumer, ref_id, args.ack_timeout_sec, tag)
            if ack is not None:
                logger.error("[%s] expected no ack, got status=%s error=%r",
                             tag, ack.status, ack.error)
                return "FAIL"
            if not _no_new_files(args, before, tag):
                return "FAIL"
            logger.info("[%s] no ack and no file (correct)", tag)
            return "PASS"

        # Match the ack -- either exact (default) or by inferred prefix
        # for the kafka source-type case where the listener rewrites the
        # reference-id to ``f"{ref_id}-{epoch_ms}"``.
        expected_ack_ref = (
            f"{ref_id}-{timestamp_ms}" if ref_id == "kafka" and timestamp_ms is not None
            else ref_id
        )
        ack = _wait_for_ack(consumer, expected_ack_ref, args.ack_timeout_sec, tag)
        if ack is None:
            return "FAIL"
        logger.info("[%s] ack: status=%s error=%r", tag, ack.status, ack.error)
        if ack.status != expected_status:
            logger.error("[%s] expected status=%s, got %r",
                         tag, expected_status, ack.status)
            return "FAIL"
        if expected_error_substr and (not ack.error or expected_error_substr not in ack.error):
            logger.error("[%s] error must contain %r: got %r",
                         tag, expected_error_substr, ack.error)
            return "FAIL"

        if expect_file:
            new_file = _wait_for_new_file(args, before, prefix="upsert-config-", scenario_tag=tag)
            if not new_file:
                return "FAIL"
            if not _verify_workers_applied(args, new_file, tag):
                return "FAIL"
            logger.info("[%s] file landed: %s", tag, new_file)
        else:
            if not _no_new_files(args, before, tag):
                return "FAIL"
            logger.info("[%s] no new files (correct)", tag)
        return "PASS"
    finally:
        consumer.close()


def _run_upsert_scenario_no_file(
    args, producer: Producer, tag: str, *,
    config: Any,
    expected_status: str,
    expected_error_substr: str | None,
) -> str:
    """Common flow for scenarios that should NOT land a file."""
    logger.info("[%s] publishing...", tag)
    consumer = _make_consumer(args.bootstrap_servers)
    consumer.poll(timeout=0.5)

    before = set(_config_files_in_container(args.container, args.config_dir))
    ref_id = f"video-analytics-api-e2e-{tag}-{uuid.uuid4().hex[:8]}"
    _publish(producer, EVENT_TYPE_UPSERT, ref_id, _wire_payload(config))

    try:
        ack = _wait_for_ack(consumer, ref_id, args.ack_timeout_sec, tag)
        if ack is None:
            return "FAIL"
        logger.info("[%s] ack: status=%s error=%r", tag, ack.status, ack.error)
        if ack.status != expected_status:
            logger.error("[%s] expected status=%s, got %r", tag, expected_status, ack.status)
            return "FAIL"
        if expected_error_substr and (not ack.error or expected_error_substr not in ack.error):
            logger.error("[%s] error must contain %r: got %r",
                         tag, expected_error_substr, ack.error)
            return "FAIL"
        if expected_error_substr is None and ack.error is not None:
            logger.error("[%s] expected null error, got %r", tag, ack.error)
            return "FAIL"
        if not _no_new_files(args, before, tag):
            return "FAIL"
        logger.info("[%s] no new files (correct)", tag)
        return "PASS"
    finally:
        consumer.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


SCENARIOS = [
    ("bootstrap", scenario_bootstrap),
    ("upsert-success-app", scenario_upsert_success_app),
    ("upsert-success-sensors", scenario_upsert_success_sensors),
    ("upsert-success-mixed-types", scenario_upsert_success_mixed_types),
    ("upsert-empty-patch", scenario_upsert_empty_patch),
    ("upsert-non-dict", scenario_upsert_non_dict),
    ("upsert-pure-forbidden", scenario_upsert_pure_forbidden),
    ("upsert-pure-bad-items", scenario_upsert_pure_bad_items),
    ("upsert-sensor-with-empty-configs", scenario_upsert_sensor_with_empty_configs),
    ("upsert-pure-non-allowlisted", scenario_upsert_pure_non_allowlisted),
    ("upsert-pure-invalid-value", scenario_upsert_pure_invalid_value),
    ("upsert-partial-with-forbidden", scenario_upsert_partial_with_forbidden),
    ("upsert-partial-with-bad-item", scenario_upsert_partial_with_bad_item),
    ("upsert-partial-with-non-allowlisted", scenario_upsert_partial_with_non_allowlisted),
    ("upsert-partial-with-invalid-value", scenario_upsert_partial_with_invalid_value),
    # Envelope-level rejection scenarios. The listener's pre-validate
    # gate catches each producer-side mistake and emits a structured
    # ``failure`` ack (or, for the broker-timestamp case, drops at the
    # wire layer with no ack).
    ("upsert-kafka-source-type", scenario_upsert_kafka_source_type),
    ("upsert-missing-status", scenario_upsert_missing_status),
    ("upsert-missing-config", scenario_upsert_missing_config),
    ("upsert-null-config", scenario_upsert_null_config),
    ("upsert-config-as-list", scenario_upsert_config_as_list),
    ("upsert-error-as-object", scenario_upsert_error_as_object),
    ("upsert-invalid-broker-timestamp", scenario_upsert_invalid_broker_timestamp),
    ("upsert-extra-envelope-field", scenario_upsert_extra_envelope_field),
    # MUST run last -- restarts the mdx-analytics container.
    ("bootstrap-empty-reply-in-window", scenario_bootstrap_empty_reply_in_window),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bootstrap-servers", default=os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"))
    parser.add_argument("--container", default="mdx-analytics", help="App container name")
    parser.add_argument("--config-dir", default="/tmp/checkpoint/config", help="CONFIG_DIR inside the container")
    parser.add_argument(
        "--bootstrap-window-sec", type=float, default=5.0,
        help="How long to scan from offset 0 for a request-config before declaring SKIP",
    )
    parser.add_argument(
        "--ack-timeout-sec", type=float, default=15.0,
        help="How long to wait for an ack / file landing / worker apply per scenario",
    )
    parser.add_argument(
        "--expected-worker-applies", type=int, default=5,
        help="Expected count of 'applied config from <name>' log lines per file "
             "(1 main + N worker processes; default 5 matches warehouse_2d/kafka).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    producer = Producer({"bootstrap.servers": args.bootstrap_servers})

    results: dict[str, str] = {}
    for name, fn in SCENARIOS:
        results[name] = fn(args, producer)

    print()
    print("=" * 60)
    print("Dynamic config E2E summary")
    print("=" * 60)
    for name, outcome in results.items():
        marker = {"PASS": "✓", "SKIP": "~", "FAIL": "✗"}.get(outcome, "?")
        print(f"  {marker} {name}: {outcome}")
    print("=" * 60)

    # Exit code: 1 if any FAIL, 0 otherwise (SKIP doesn't fail the run).
    return 1 if "FAIL" in results.values() else 0


if __name__ == "__main__":
    sys.exit(main())
