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

"""The join between the three legs of REQ-007, which unit tests missed.

The broker envelope, the decoder pairing and the parent extraction were each
covered on their own, and deleting the one line in ``process_batch_vlm`` that
joins them left the whole suite green. That line is what actually delivers the
requirement.

The pass-through test is the one that matters more: it drives the pass-through
exit, which leaves the traced path before any root span exists. Nothing pops the
transport key there, so it rode into the publisher and, as raw bytes, into the
Elasticsearch document -- where the serializer raised and the sink swallowed it,
losing the alert. Tracing must not cost an event (REQ-019).
"""

import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

_ALERT_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(_ALERT_ROOT), str(_ALERT_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import enhance_alert_with_vlm as eavw  # noqa: E402
from tracing import spans as tracing_spans  # noqa: E402

TRACEPARENT = "00-" + "a" * 32 + "-00f067aa0ba902b7-01"


def _kafka_record(sensor, with_headers=True):
    payload = {
        "sensorId": sensor,
        "category": "loitering",
        "timestamp": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T00:00:02Z",
        "objectIds": [],
    }
    headers = [("traceparent", TRACEPARENT.encode())] if with_headers else None
    return payload, headers


def _enhancer(passthrough=False):
    stub = Mock(spec=eavw.AnomalyEnhancer)
    stub.config = {"alert_agent": {"verify_only_finished_events": False}}
    stub.source_type = "kafka"
    stub.async_io_enabled = False
    stub.vst_pass_through_mode = passthrough
    stub.redis_handler = None
    stub._vlm_rate_limit_enabled = False
    stub._apply_vlm_rate_limit = lambda msgs: msgs
    stub._process_single_message_with_mode = Mock()
    stub._process_media_passthrough = Mock()
    stub._vst_handler = Mock()
    stub.pipeline_mode = "sync"
    return stub


def _drive(monkeypatch, stub, payload, headers, enabled=True):
    """Run the real process_batch_vlm over one decoded record."""
    from mdx.kafka_message_broker import KafkaMessage

    record = KafkaMessage(b"k1", b"unused", 1700000000000, headers)
    monkeypatch.setattr(
        eavw, "protobuf_anomalies_to_json_string_list",
        lambda *a, **k: [(json.dumps(payload), record)],
    )
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: enabled)

    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, {"t-0": [record]}, "incident")


def test_an_out_of_range_timestamp_does_not_cost_its_neighbours(monkeypatch):
    """TS-026 for the payload class that gets past ParseFromString.

    Decoding is two protobuf calls, not one. A payload can parse cleanly and
    still fail on `MessageToJson`, which raises `SerializeToJsonError` -- not a
    `DecodeError` -- and both ingress types carry producer-filled Timestamps.
    The classic trigger is epoch milliseconds in a seconds field: 1.7e12 is far
    past protobuf's 2.53e11 ceiling. Where the tombstone case needs a compacted
    topic, this needs only one upstream unit confusion, and it cost every other
    alert in the poll.
    """
    from mdx.kafka_message_broker import KafkaMessage
    from mdx.protobuf import Incident as NvIncident

    def record(sensor, trace_char, seconds=None):
        pb = NvIncident()
        pb.sensorId = sensor
        if seconds is not None:
            pb.timestamp.seconds = seconds
        return KafkaMessage(
            sensor.encode(), pb.SerializeToString(), 1700000000000,
            (("traceparent", f"00-{trace_char * 32}-00f067aa0ba902b7-01".encode()),),
        )

    batch = [record("cam-1", "a"),
             record("cam-2", "b", seconds=1700000000000),   # epoch millis
             record("cam-3", "c")]

    stub = _enhancer()
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: True)

    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, {"t-0": batch}, "incident")

    dispatched = {call[0][1]["sensorId"]
                  for call in stub._process_single_message_with_mode.call_args_list}
    assert dispatched == {"cam-1", "cam-3"}, (
        f"one unserialisable timestamp took the batch with it; got {dispatched}"
    )


def test_a_malformed_batch_does_not_flood_the_log(caplog):
    """One traceback and one summary per batch, not a line per record.

    Both the single-record decoder and the batch caller used to log, so a
    schema drift emitted N+2 error records per batch -- burying the one useful
    traceback and loading the log pipeline during the exact incident that needs
    it. The reporting belongs to the caller, which bounds it.
    """
    import logging as _logging
    from utils.schema_util import protobuf_anomalies_to_json_string_list

    batch = {"t-0": [(b"k", b"not-a-protobuf", 1700000000000, ()) for _ in range(100)]}

    with caplog.at_level(_logging.DEBUG):
        assert protobuf_anomalies_to_json_string_list(batch, "incident") == []

    loud = [r for r in caplog.records if r.levelno >= _logging.WARNING]
    assert len(loud) == 2, (
        f"100 bad records produced {len(loud)} loud log lines, not 2: "
        f"{[r.getMessage()[:60] for r in loud]}"
    )
    assert sum(1 for r in loud if r.exc_info) == 1, "expected exactly one traceback"
    assert "the entire batch was lost" in loud[-1].getMessage()
    # Losing the whole batch is an ERROR, not a warning: an alerting rule keyed
    # on ERROR goes silent if this is downgraded, and nothing else would notice.
    assert loud[-1].levelno == _logging.ERROR, (
        f"the whole batch was lost but it was logged at {loud[-1].levelname}"
    )


def test_a_mixed_failure_batch_still_explains_itself(caplog):
    """The two skip paths must not eat each other's one detail line.

    Both bounds used to share the `skipped` counter, so whichever failure came
    first consumed the batch's single loud slot. A compacted-topic tombstone
    followed by records from an incompatible schema -- the ordinary shape of a
    drift -- reported the tombstone and nothing else: the summary said 100 of
    100 failed while the traceback that explains WHY was never emitted.
    """
    import logging as _logging
    from utils.schema_util import protobuf_anomalies_to_json_string_list

    batch = {"t-0": [(b"k", None, 1700000000000, ())]                 # tombstone first
                    + [(b"k", b"not-a-protobuf", 1700000000000, ())] * 99}

    with caplog.at_level(_logging.DEBUG):
        assert protobuf_anomalies_to_json_string_list(batch, "incident") == []

    loud = [r for r in caplog.records if r.levelno >= _logging.WARNING]
    assert sum(1 for r in loud if r.exc_info) == 1, (
        "the decode traceback was suppressed by the tombstone: "
        f"{[r.getMessage()[:60] for r in loud]}"
    )
    # Still bounded: one line per distinct cause, plus the summary.
    assert len(loud) == 3, [r.getMessage()[:60] for r in loud]
    assert "NoneType" in loud[0].getMessage()
    assert "100 of 100" in loud[-1].getMessage()


def test_the_real_dedup_filter_preserves_the_association(monkeypatch, tmp_path):
    """TS-063 against the filter that actually ships, not a lambda.

    The mixed-batch test below installs stub filters, so the contract it claims
    to pin -- that dedup returns the *same dict objects* rather than rebuilt
    ones -- lives in `DedupStateHandler.filter_new_events` and was pinned
    nowhere. Mutating the real filter to copy survivors while dropping the
    transport key left every test in this file green: a real, silent REQ-007
    regression that no assertion could see.

    Here the real handler runs, with a real TTL cache, and drops the middle
    record as a genuine duplicate -- identity-preserving and length-changing,
    which is exactly the shape TS-063 specifies.
    """
    import yaml
    from clients.dedup_state import DedupStateHandler
    from mdx.kafka_message_broker import KafkaMessage
    from mdx.protobuf import Incident as NvIncident

    config = tmp_path / "dedup.yaml"
    config.write_text(yaml.dump({
        "event_bridge": {"redis_source": {
            "host": "localhost", "port": 6379, "db": 0, "dedup_ttl_seconds": 3600,
        }},
    }))

    def record(sensor, trace_char):
        pb = NvIncident()
        pb.sensorId = sensor
        return KafkaMessage(
            sensor.encode(), pb.SerializeToString(), 1700000000000,
            (("traceparent", f"00-{trace_char * 32}-00f067aa0ba902b7-01".encode()),),
        )

    # The middle record repeats the first record's cohort key, so the real
    # filter drops exactly one and the remaining two must keep their own.
    batch = [record("cam-1", "a"), record("cam-1", "b"), record("cam-3", "c")]

    stub = _enhancer()
    stub.redis_handler = DedupStateHandler(config_file=str(config))
    stub._run_redis_operation_with_mode = lambda _n, fn, msgs, **kw: fn(msgs, **kw)
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: True)

    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, {"t-0": batch}, "incident")

    carried = [call[0][1].get(tracing_spans.KAFKA_HEADERS_KEY)
               for call in stub._process_single_message_with_mode.call_args_list]
    assert len(carried) == 2, (
        f"the real dedup filter did not drop exactly one record: {len(carried)}"
    )
    assert all(c is not None for c in carried), (
        "a survivor came back from the real dedup filter without its headers; "
        "the filter is rebuilding dicts instead of returning the same objects"
    )
    assert carried[0] != carried[1], "survivors were collapsed onto one parent"


def test_a_tombstone_does_not_cost_its_neighbours(monkeypatch):
    """TS-026 for the one bad payload that is not a DecodeError.

    A producer may legally publish a null value, and confluent-kafka hands that
    back as `None`. `ParseFromString(None)` raises `TypeError`, not
    `DecodeError`, so it escaped the per-record skip, reached the caller's
    blanket except, and took the whole batch with it -- offsets already
    committed. Both reviewers found this independently.

    This is also the only test that runs a real `KafkaMessage` through the real
    decoder AND the real attach in one pass. The decoder tests use bare tuples
    and the attach tests stub the decoder, and that seam is where a regression
    can hide from both.
    """
    from mdx.kafka_message_broker import KafkaMessage
    from mdx.protobuf import Incident as NvIncident

    _PROTOBUF = object()

    def record(sensor, trace_char, value=_PROTOBUF):
        if value is _PROTOBUF:
            pb = NvIncident()
            pb.sensorId = sensor
            value = pb.SerializeToString()
        return KafkaMessage(
            sensor.encode(), value, 1700000000000,
            (("traceparent", f"00-{trace_char * 32}-00f067aa0ba902b7-01".encode()),),
        )

    batch = [record("cam-1", "a"),
             record("cam-2", "b", value=None),      # the tombstone
             record("cam-3", "c")]

    stub = _enhancer()
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: True)

    # No decoder stub: the real one runs.
    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, {"t-0": batch}, "incident")

    dispatched = {
        call[0][1]["sensorId"]: call[0][1].get(tracing_spans.KAFKA_HEADERS_KEY)
        for call in stub._process_single_message_with_mode.call_args_list
    }
    assert set(dispatched) == {"cam-1", "cam-3"}, (
        f"the tombstone took its neighbours with it; dispatched {set(dispatched)}"
    )
    assert dispatched == {
        "cam-1": [("traceparent", "00-" + "a" * 32 + "-00f067aa0ba902b7-01")],
        "cam-3": [("traceparent", "00-" + "c" * 32 + "-00f067aa0ba902b7-01")],
    }, f"survivors lost or swapped their parents: {dispatched}"


def test_a_mixed_batch_keeps_each_record_on_its_own_parent(monkeypatch):
    """TS-026 + TS-063: the pairing, not just the attach, driven end to end.

    Every other test here drives `process_batch_vlm` with a single record, so
    the attach line was pinned but the *pairing* was not: rewriting it to read
    `decoded_messages[0]`'s headers for every record -- a batch-wide default,
    the exact thing TS-026 forbids -- left all 3400 tests green. This test is
    what fails on that mutation.

    It also runs the dedup filter for real rather than setting `redis_handler`
    to None, because TS-063 requires the association to survive a filter that
    preserves identity while changing length: the middle record drops out, and
    the survivors must not shift onto each other's parents.
    """
    from mdx.kafka_message_broker import KafkaMessage

    sensors = ["cam-1", "cam-2", "cam-3"]
    traceparents = {s: "00-" + c * 32 + "-00f067aa0ba902b7-01"
                    for s, c in zip(sensors, "abc")}
    decoded = []
    for sensor in sensors:
        payload, _ = _kafka_record(sensor)
        record = KafkaMessage(
            sensor.encode(), b"unused", 1700000000000,
            (("traceparent", traceparents[sensor].encode()),),
        )
        decoded.append((json.dumps(payload), record))

    stub = _enhancer()
    # Drop the middle record, keeping the surviving dicts by identity -- the
    # shape the real dedup filter has.
    stub.redis_handler = Mock()
    stub.redis_handler.filter_by_end_time_delta = lambda msgs, **kw: msgs
    stub.redis_handler.filter_new_events = (
        lambda msgs, **kw: [m for m in msgs if m["sensorId"] != "cam-2"]
    )
    stub._run_redis_operation_with_mode = lambda _name, fn, msgs, **kw: fn(msgs, **kw)

    monkeypatch.setattr(eavw, "protobuf_anomalies_to_json_string_list",
                        lambda *a, **k: decoded)
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: True)

    eavw.AnomalyEnhancer.process_batch_vlm(
        stub, 0, {"t-0": [r for _, r in decoded]}, "incident")

    dispatched = {
        call[0][1]["sensorId"]: call[0][1].get(tracing_spans.KAFKA_HEADERS_KEY)
        for call in stub._process_single_message_with_mode.call_args_list
    }
    assert set(dispatched) == {"cam-1", "cam-3"}, (
        f"the filter did not run as intended; dispatched {set(dispatched)}"
    )
    assert dispatched == {
        "cam-1": [("traceparent", traceparents["cam-1"])],
        "cam-3": [("traceparent", traceparents["cam-3"])],
    }, f"records were re-paired onto the wrong parents: {dispatched}"


def test_the_inbound_headers_reach_the_parsed_message(monkeypatch):
    """Delete the attach and this is the only test that notices."""
    stub = _enhancer()
    payload, headers = _kafka_record("cam-1")

    _drive(monkeypatch, stub, payload, headers)

    stub._process_single_message_with_mode.assert_called()
    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert tracing_spans.KAFKA_HEADERS_KEY in dispatched, (
        "the record's headers never reached the parsed message; REQ-007 is wired "
        "at the broker and at open_root_span but not between them"
    )
    assert dispatched[tracing_spans.KAFKA_HEADERS_KEY] == [("traceparent", TRACEPARENT)], (
        "headers must be carried decoded; raw bytes on a payload dict break the "
        "Elasticsearch serializer and the sink swallows the failure"
    )


def test_tracestate_without_traceparent_is_not_attached(monkeypatch):
    """Traffic that can never be parented must not carry the key.

    `decode_kafka_headers` keeps both W3C names, so a record with a bare
    `tracestate` produced a non-empty list and got the key attached -- but
    extraction returns None without a `traceparent`, so the key was pure
    exposure on a payload dict, on exactly the traffic that cannot use it.
    """
    stub = _enhancer()
    payload, _ = _kafka_record("cam-1")

    _drive(monkeypatch, stub, payload, [("tracestate", b"vendor=1")])

    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert tracing_spans.KAFKA_HEADERS_KEY not in dispatched


def test_a_mixed_case_traceparent_is_still_attached(monkeypatch):
    """The gate must fold case, because everything around it does.

    `decode_kafka_headers` filters case-insensitively but keeps the name as the
    producer sent it, and extraction folds case too -- so `TraceParent` parents
    a record perfectly well. A case-sensitive gate in front of them dropped the
    key for exactly that record and cost it its parent, silently.
    """
    stub = _enhancer()
    payload, _ = _kafka_record("cam-1")

    _drive(monkeypatch, stub, payload, [("TraceParent", TRACEPARENT.encode())])

    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert dispatched.get(tracing_spans.KAFKA_HEADERS_KEY) == [
        ("TraceParent", TRACEPARENT)
    ], "a mixed-case traceparent was gated out and lost its parent"


def test_nothing_is_attached_when_tracing_is_off(monkeypatch):
    """REQ-019: a disabled deployment never sees the key at all."""
    stub = _enhancer()
    payload, headers = _kafka_record("cam-1")

    _drive(monkeypatch, stub, payload, headers, enabled=False)

    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert tracing_spans.KAFKA_HEADERS_KEY not in dispatched


def test_the_passthrough_exit_does_not_carry_the_key_to_the_publisher(monkeypatch):
    """The exit that leaves the traced path before a root span exists.

    `_process_media_passthrough` publishes the message object directly. With the
    key still on it -- and as bytes -- the Elasticsearch serializer raised,
    `_store_success` swallowed the exception, and the alert was never indexed.
    """
    stub = _enhancer(passthrough=True)
    payload, headers = _kafka_record("cam-1")

    _drive(monkeypatch, stub, payload, headers)

    stub._process_media_passthrough.assert_called_once()
    handed_over = stub._process_media_passthrough.call_args[0][1]
    assert handed_over, "nothing was handed over; the assertion below would be vacuous"
    for message in handed_over:
        assert tracing_spans.KAFKA_HEADERS_KEY not in message, (
            "the transport key reached the pass-through publisher; as bytes it "
            "makes the ES document unserialisable and the alert is dropped"
        )


def test_a_decoded_header_block_is_json_serialisable():
    """The property that makes a missed pop survivable rather than fatal."""
    from elasticsearch.serializer import JSONSerializer

    decoded = tracing_spans.decode_kafka_headers(
        [("traceparent", TRACEPARENT.encode())]
    )
    JSONSerializer().dumps({"sensorId": "cam", tracing_spans.KAFKA_HEADERS_KEY: decoded})

    from elasticsearch.exceptions import SerializationError
    with pytest.raises(SerializationError):
        JSONSerializer().dumps(
            {"sensorId": "cam",
             tracing_spans.KAFKA_HEADERS_KEY: [("traceparent", TRACEPARENT.encode())]}
        )


def test_the_gate_initialises_rather_than_merely_asking(monkeypatch):
    """The attach gate runs before the first `open_root_span` in the process.

    Nothing calls `init_tracing()` in a pipeline worker -- `open_root_span`
    initialises lazily -- so a gate on the passive `is_enabled()` reads False for
    the whole first batch of every worker. With N workers that is N batches of
    unparented events per restart, invisible to any test that has already opened
    a span.

    Driven by making the two calls disagree exactly as they do on a fresh
    process: `is_enabled()` False, `ensure_initialised()` True.
    """
    stub = _enhancer()
    payload, headers = _kafka_record("cam-1")

    monkeypatch.setattr(eavw.tracing, "is_enabled", lambda: False)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: True)

    from mdx.kafka_message_broker import KafkaMessage
    record = KafkaMessage(b"k1", b"unused", 1700000000000, headers)
    monkeypatch.setattr(
        eavw, "protobuf_anomalies_to_json_string_list",
        lambda *a, **k: [(json.dumps(payload), record)],
    )
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, {"t-0": [record]}, "incident")

    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert tracing_spans.KAFKA_HEADERS_KEY in dispatched, (
        "the gate asked is_enabled() instead of initialising; on a fresh worker "
        "the first batch loses its inbound parent"
    )


def test_only_propagation_headers_are_carried():
    """The payload dict is not a place to park arbitrary upstream data.

    Before this filter the pass-through leak carried whatever the producer had
    put on the record -- a routing hint, a schema id, a 100 KB blob -- straight
    at the Elasticsearch document. Narrowing to the W3C keys makes the second
    defensive layer smaller as well as safer: there is nothing else it needs to
    survive.
    """
    carried = tracing_spans.decode_kafka_headers([
        ("traceparent", TRACEPARENT.encode()),
        ("x-app-routing", b"internal-only"),
        ("__TypeId__", b"com.example.Event"),
        ("tracestate", b"vendor=1"),
        ("x-blob", b"y" * 100_000),
    ])

    assert carried == [("traceparent", TRACEPARENT), ("tracestate", "vendor=1")]
    assert tracing_spans.decode_kafka_headers([("x-app", b"junk")]) is None


def test_the_decoded_shape_is_what_open_root_span_parents_on():
    """The join nobody tested: the pipeline attaches str, the parser reads it.

    Every parenting test feeds `open_root_span` the bytes shape the pipeline no
    longer produces. That leaves a change to `decode_kafka_headers`' output --
    a dict, lowercased keys, this very filter -- able to unparent production
    while every parenting test stays green.
    """
    import subprocess

    script = (
        "import sys, json; sys.path[:0] = [%r, %r]\n"
        "import tracing\n"
        "from tracing import spans\n"
        "from opentelemetry.sdk.trace.export import SimpleSpanProcessor\n"
        "from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter\n"
        "tracing.init_tracing('join')\n"
        "exp = InMemorySpanExporter(); tracing._provider.add_span_processor(SimpleSpanProcessor(exp))\n"
        "raw = [('traceparent', b'00-' + b'a'*32 + b'-00f067aa0ba902b7-01'), ('x-app', b'junk')]\n"
        "decoded = spans.decode_kafka_headers(raw)\n"
        "msg = {'sensorId': 'cam', spans.KAFKA_HEADERS_KEY: decoded}\n"
        "h = spans.open_root_span(msg, pipeline_mode='sync')\n"
        "h.close(); h.detach()\n"
        "print(json.dumps({'traces': [format(s.context.trace_id, '032x') "
        "for s in exp.get_finished_spans() if s.name == 'Alert Verification'], "
        "'key_left': spans.KAFKA_HEADERS_KEY in msg}))\n"
        % (str(_ALERT_ROOT), str(_ALERT_ROOT / "src"))
    )
    env = {
        **__import__("os").environ,
        "ENABLE_OTEL_MONITORING": "true",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_METRICS_EXPORTER": "none",
    }
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, env=env, timeout=90)
    result = json.loads(out.stdout.strip().splitlines()[-1])

    assert result["traces"] == ["a" * 32], (
        f"the decoded shape did not parent the root span: {result['traces']}"
    )
    assert result["key_left"] is False


def test_a_rest_body_cannot_parent_the_on_demand_root(tmp_path):
    """The on-demand path's `message` is the HTTP request body.

    `AlertVerificationRequest` sets `extra = "allow"`, so a caller can put the
    transport key in a POST body. Deriving a parent from it let the caller become
    the parent of AB's root span and -- because ParentBased honours a remote
    `sampled=1` -- take over the sampling budget: at sampling_ratio 0.0, 200
    injected requests exported 199 spans where 200 honest ones exported none.

    The derivation is now exclusive with `link_to`, which the on-demand path
    always passes, and the service strips the key at the boundary as well.
    """
    import subprocess

    script = (
        "import sys, json; sys.path[:0] = [%r, %r]\n"
        "import tracing\n"
        "from tracing import spans\n"
        "from opentelemetry import trace as ot\n"
        "from opentelemetry.sdk.trace.export import SimpleSpanProcessor\n"
        "from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter\n"
        "tracing.init_tracing('probe')\n"
        "exp = InMemorySpanExporter(); tracing._provider.add_span_processor(SimpleSpanProcessor(exp))\n"
        "ATT = 'd'*32\n"
        "req = ot.SpanContext(trace_id=int('a'*32,16), span_id=int('1'*16,16),\n"
        "                     is_remote=True, trace_flags=ot.TraceFlags(0x01))\n"
        "body = {'sensorId':'cam', spans.KAFKA_HEADERS_KEY:\n"
        "        [('traceparent', '00-' + ATT + '-00f067aa0ba902b7-01')]}\n"
        "h = spans.open_root_span(body, pipeline_mode='ondemand', link_to=req)\n"
        "h.close(); h.detach()\n"
        "s = exp.get_finished_spans()[0]\n"
        # Positive control: the same body with no link_to MUST parent on ATT.
        # Without it the test cannot tell "the gate blocked the takeover" from
        # "the injected payload was never a viable parent", so a typo in the
        # fixture would quietly turn the only guard on this fix into a no-op.
        "body2 = {'sensorId':'cam', spans.KAFKA_HEADERS_KEY:\n"
        "         [('traceparent', '00-' + ATT + '-00f067aa0ba902b7-01')]}\n"
        "h2 = spans.open_root_span(body2, pipeline_mode='sync')\n"
        "h2.close(); h2.detach()\n"
        "ctl = format(exp.get_finished_spans()[-1].context.trace_id,'032x')\n"
        "print(json.dumps({'trace': format(s.context.trace_id,'032x'), 'attacker': ATT,\n"
        "                  'control': ctl,\n"
        "                  'links': len(s.links), 'key_left': spans.KAFKA_HEADERS_KEY in body}))\n"
        % (str(_ALERT_ROOT), str(_ALERT_ROOT / "src"))
    )
    # sampling_ratio 1.0 so the span always records. Without it the default 0.1
    # applies and the span usually is not sampled at all -- which is itself the
    # fix working (the injected `sampled=1` is no longer honoured), but it makes
    # the assertion below unobservable.
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("alert_agent:\n  tracing:\n    sampling_ratio: 1.0\n"
                   "    include_content: false\n")
    env = {
        **__import__("os").environ,
        "ENABLE_OTEL_MONITORING": "true",
        "OTEL_TRACES_EXPORTER": "none",
        "OTEL_METRICS_EXPORTER": "none",
        "CONFIG_PATH": str(cfg),
    }
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, env=env, timeout=90)
    assert out.stdout.strip(), f"probe produced nothing; stderr: {out.stderr[-400:]}"
    r = json.loads(out.stdout.strip().splitlines()[-1])

    assert r["control"] == r["attacker"], (
        "positive control failed: the injected traceparent never was a viable "
        "parent, so the assertion below proves nothing"
    )
    assert r["trace"] != r["attacker"], (
        "a REST caller became the parent of the on-demand root span"
    )
    assert r["links"] == 1, "the on-demand span must still be linked to its request"
    assert r["key_left"] is False, "the transport key was left on the request body"


def test_the_on_demand_service_strips_the_key_from_the_body():
    """Defence at the boundary, driven rather than grepped.

    The first version asserted only that the string "KAFKA_HEADERS_KEY" appeared
    somewhere in the service source, so changing `.pop` to `.get` left it green.
    A reviewer did exactly that to make the point -- and the weak test is how the
    disabled defence nearly reached a commit. Fourth test on this branch to have
    passed for the wrong reason, in the file written to stop that happening.

    This one runs the real `prepare()`.
    """
    from unittest.mock import Mock

    from web.service.ondemand_verification_service import OnDemandVerificationService

    service = Mock(spec=OnDemandVerificationService)
    service.max_media_count = 8
    service.logger = Mock()
    service.prompt_manager = Mock()
    service.prompt_manager.get_prompts_for_message.return_value = ("user", "system")

    body = {
        "sensorId": "cam-1",
        "category": "loitering",
        "end": "2025-01-01T00:00:02Z",
        tracing_spans.KAFKA_HEADERS_KEY: [("traceparent", TRACEPARENT)],
    }

    message, user_prompt, _system = OnDemandVerificationService.prepare(service, body)

    assert user_prompt == "user", "prepare() did not run to completion"
    assert tracing_spans.KAFKA_HEADERS_KEY not in message, (
        "the on-demand service handed the transport key on from the request body; "
        "a caller could then parent AB's root span and take the sampling budget"
    )



def test_the_alert_path_carries_the_key_through_the_real_normalizer(monkeypatch):
    """The alert half of REQ-007, and the step no test exercised.

    Incidents skip `normalize_alert_message`; alerts do not, and it returns
    `dict(message)` -- a new object. So on the alert path the association
    survives by copy, not by the object identity the attach comment used to
    claim. A normalizer that rebuilt the dict field by field would unparent
    every alert-shaped event, and with `normalize_alert_message` stubbed to
    identity in every other test, nothing would notice.
    """
    from utils.event_utils import normalize_alert_message
    from mdx.kafka_message_broker import KafkaMessage

    stub = _enhancer()
    payload = {
        "sensor": {"id": "cam-1"},
        "analyticsModule": {"id": "loitering"},
        "timestamp": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T00:00:02Z",
        "objectIds": [],
    }
    record = KafkaMessage(b"k1", b"unused", 1700000000000,
                          (("traceparent", TRACEPARENT.encode()),))
    monkeypatch.setattr(
        eavw, "protobuf_anomalies_to_json_string_list",
        lambda *a, **k: [(json.dumps(payload), record)],
    )
    # The REAL normalizer, and an alert-shaped message so it actually runs.
    monkeypatch.setattr(eavw, "normalize_alert_message", normalize_alert_message)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: True)

    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, {"t-0": [record]}, "alert")

    stub._process_single_message_with_mode.assert_called()
    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert dispatched.get("sensorId") == "cam-1", "the normalizer did not run"
    assert dispatched.get(tracing_spans.KAFKA_HEADERS_KEY) == [("traceparent", TRACEPARENT)], (
        "the alert path lost the inbound parent crossing normalize_alert_message"
    )


def test_a_record_with_no_propagation_header_gets_no_key(monkeypatch):
    """LOW, but it is the exposure surface the two-layer defence is about.

    Schema-registry ids, `__TypeId__` and routing hints are common; gating on
    the raw header block attached the key to every such record for no benefit.
    """
    stub = _enhancer()
    payload, _ = _kafka_record("cam-1", with_headers=False)
    _drive(monkeypatch, stub, payload,
           [("__TypeId__", b"com.example.Event"), ("x-route", b"eu-west")])

    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert tracing_spans.KAFKA_HEADERS_KEY not in dispatched, (
        "the key was attached to a record carrying no trace context"
    )


def test_a_json_payload_cannot_declare_its_own_trace_context(monkeypatch):
    """The same takeover as the REST body one, through the Redis/replay door.

    `SourceRedisStream` reserialises payloads unchanged and `process_batch_vlm`
    parses them in its JSON branch. Only the protobuf branch is a Kafka record,
    so only it may put the transport key on a message; anything arriving from a
    JSON or direct-dict ingress carries whatever the payload author wrote, and
    trusting that lets them choose AB's trace id and -- since ParentBased
    honours a remote `sampled=1` -- defeat sampling_ratio entirely.
    """
    stub = _enhancer()
    forged = {
        "sensorId": "cam-1",
        "category": "loitering",
        "timestamp": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T00:00:02Z",
        "objectIds": [],
        tracing_spans.KAFKA_HEADERS_KEY: [["traceparent", TRACEPARENT]],
    }
    monkeypatch.setattr(eavw, "normalize_alert_message", lambda m: m)
    monkeypatch.setattr(eavw.tracing, "ensure_initialised", lambda: True)

    # The JSON-string branch, which is what a Redis Stream source produces.
    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, [json.dumps(forged)], "incident")
    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert tracing_spans.KAFKA_HEADERS_KEY not in dispatched, (
        "a JSON payload declared its own trace context and it was trusted"
    )

    # And the direct-dict branch, used by replay and by plugin sources.
    stub._process_single_message_with_mode.reset_mock()
    eavw.AnomalyEnhancer.process_batch_vlm(stub, 0, [dict(forged)], "incident")
    dispatched = stub._process_single_message_with_mode.call_args[0][1]
    assert tracing_spans.KAFKA_HEADERS_KEY not in dispatched, (
        "a directly-supplied dict declared its own trace context and it was trusted"
    )
