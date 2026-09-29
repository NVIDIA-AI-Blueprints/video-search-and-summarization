# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Live filling boundary tests: provenance, lifecycle, units and honest pending evidence."""

from __future__ import annotations

from copy import deepcopy
import json

from click.testing import CliRunner
import httpx
import pytest

from vss_cli import config as config_mod
from vss_cli.filling import group
from vss_cli.group import Context
from vss_core.filling import FillingClient
from vss_core.filling import FillingError

STREAM = "c96e2b29-2101-4f06-9f64-d76081147c34"
OTHER = "5815509a-7317-4e8d-907e-a9f3d1c508fa"
SESSION = "b80b90e6-f5f0-4ed1-89e7-b08e63df2165"
REQUEST = "f3ab9e6f-cc47-4e8d-895f-7b05b7d9d70b"
MEASUREMENT = {
    "engine": "rfdetr",
    "algorithm": "rfdetr-live-cycle-v1",
    "model_hashes": {"bottle": "a" * 64, "liquid": "b" * 64},
    "pipeline_sha256": "c" * 64,
    "calibration_sha256": "d" * 64,
    "reference_level": 0.72803,
    "tolerance": 0.055,
    "overflow_engine": "calibrated-exterior-color-signal-v1",
}
CLOCK = {"method": "receiver-utc-estimate", "verified": False}
SUMMARY = {"total": 1, "normal": 0, "underfill": 1, "overflow": 0, "uncertain": 0, "incomplete": 0}
ENVELOPE = {
    "mode": "live",
    "session_id": SESSION,
    "stream_id": STREAM,
    "measurement": MEASUREMENT,
    "clock": CLOCK,
    "summary": SUMMARY,
}
EVENT = {
    **ENVELOPE,
    "event_id": "event-1",
    "seq": 1,
    "epoch": 0,
    "track_id": "track-1",
    "kind": "cycle.finalized",
    "status": "underfill",
    "source_pts_seconds": 10.0,
    "start_pts_seconds": 1.0,
    "end_pts_seconds": 10.0,
    "occurred_at_utc": "2026-09-23T15:00:10Z",
    "final_level": 0.6,
    "reference_level": 0.72803,
    "reason": "settled visible height below reference",
    "evidence_status": "pending",
    "video_evidences": [],
}


class LiveFixture:
    def __init__(self):
        self.calls = []
        self.registry = {
            "mode": "live",
            "sources": [
                {
                    "stream_id": STREAM,
                    "name": "live-station",
                    "state": "online",
                    "width": 1280,
                    "height": 720,
                    "fps": 24,
                    "profile_id": "single-station",
                    "approved": True,
                }
            ],
        }
        self.state = deepcopy(
            {
                **ENVELOPE,
                "type": "status",
                "status": "running",
                "epoch": 0,
                "source": {"name": "live-station", "profile_id": "single-station"},
                "current": {"phase": "filling", "level": 0.4, "confidence": 0.9},
                "stats": {},
                "error": None,
            }
        )
        self.events = deepcopy({**ENVELOPE, "events": [EVENT], "next_seq": 1})
        self.answer = deepcopy(
            {
                **ENVELOPE,
                "answer": "One finalized underfill; current bottle remains provisional.",
                "matches": [EVENT],
                "video_evidences": [],
                "evidence_status": "pending",
            }
        )
        self.query_mutation = None
        self.failure = None

    def handle(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, dict(request.url.params), body))
        if self.failure:
            if isinstance(self.failure, Exception):
                raise self.failure
            return httpx.Response(self.failure, json={"detail": "fixture typed failure"})
        if path == "/filling/api/live/sources":
            result = self.registry
        elif path == "/filling/api/live/status" or path == "/filling/api/live/start":
            result = self.state
        elif path == "/filling/api/live/stop":
            self.state["status"] = "stopped"
            result = self.state
        elif path == "/filling/api/live/events":
            result = self.events
        elif path == "/filling/api/live/query":
            result = deepcopy(self.answer)
            if self.query_mutation:
                self.query_mutation(self.state)
        else:
            raise AssertionError("Live code must not call recorded routes: " + path)
        return httpx.Response(200, json=result)

    def client(self):
        return FillingClient("https://vss.test/filling", transport=httpx.MockTransport(self.handle))


def test_start_resolves_approved_registered_source_and_preserves_idempotency_key():
    fixture = LiveFixture()
    with fixture.client() as client:
        state = client.live_start(STREAM, request_id=REQUEST)
    assert state["session_id"] == SESSION
    assert state["stream_id"] == STREAM
    posts = [call for call in fixture.calls if call[0] == "POST"]
    assert posts == [("POST", "/filling/api/live/start", {}, {"stream_id": STREAM, "request_id": REQUEST})]
    assert all("/live/" in path for _, path, _, _ in fixture.calls)


def test_start_missing_source_never_mutates():
    fixture = LiveFixture()
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_start(OTHER)
    assert error.value.code == 5
    assert all(method == "GET" for method, _, _, _ in fixture.calls)


def test_idle_discovery_is_valid_but_cannot_satisfy_explicit_identity():
    fixture = LiveFixture()
    fixture.state = {"type": "status", "mode": "live", "status": "idle", "session_id": None, "stream_id": None}
    with fixture.client() as client:
        assert client.live_status()["status"] == "idle"
        with pytest.raises(FillingError) as error:
            client.live_status(SESSION)
    assert error.value.code == 4


@pytest.mark.parametrize("status", ["connecting", "running", "reconnecting", "stopped", "error"])
def test_status_reports_lifecycle_without_claiming_startup_or_reconnect_is_completion(status):
    fixture = LiveFixture()
    fixture.state["status"] = status
    fixture.state["current"]["level"] = None
    with fixture.client() as client:
        actual = client.live_status(SESSION, stream_id=STREAM)
    assert actual["status"] == status
    assert actual["current"]["level"] is None
    assert actual["clock"] == CLOCK
    assert "provisional" in actual["measurement_units"]["current"]


@pytest.mark.parametrize("status,code", [(404, 5), (409, 4), (422, 2), (503, 3)])
def test_live_http_errors_remain_typed_not_empty_success(status, code):
    fixture = LiveFixture()
    fixture.failure = status
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_status(SESSION)
    assert error.value.code == code


def test_live_timeout_remains_typed():
    fixture = LiveFixture()
    fixture.failure = httpx.ReadTimeout("fixture timeout")
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_status(SESSION)
    assert error.value.code == 7


def test_live_empty_registry_is_a_success():
    fixture = LiveFixture()
    fixture.registry["sources"] = []
    with fixture.client() as client:
        assert client.live_sources()["sources"] == []


@pytest.mark.parametrize(
    "row_mutation",
    [
        lambda row: row.update(approved=False),
        lambda row: row.update(stream_id="not-a-uuid"),
        lambda row: row.pop("profile_id"),
    ],
)
def test_live_sources_reject_unapproved_or_invalid_registry(row_mutation):
    fixture = LiveFixture()
    row_mutation(fixture.registry["sources"][0])
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_sources()
    assert error.value.code == 4


def test_stop_is_scoped_to_exact_requested_session():
    fixture = LiveFixture()
    with fixture.client() as client:
        result = client.live_stop(SESSION, stream_id=STREAM)
    assert result["status"] == "stopped"
    assert next(call[3] for call in fixture.calls if call[0] == "POST") == {"session_id": SESSION}


def test_stop_source_conflict_never_mutates():
    fixture = LiveFixture()
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_stop(SESSION, stream_id=OTHER)
    assert error.value.code == 4
    assert all(method == "GET" for method, _, _, _ in fixture.calls)


def test_query_keeps_real_units_nulls_and_provisional_state_separate():
    fixture = LiveFixture()
    fixture.answer["matches"][0].update(final_level=None, status="uncertain")
    with fixture.client() as client:
        answer = client.live_query(SESSION, "Which finalized bottles were underfilled?", stream_id=STREAM)
    assert answer["summary"] == SUMMARY
    assert answer["matches"][0]["final_level"] is None
    assert answer["measurement"]["reference_level"] == 0.72803
    assert answer["measurement_units"]["display_percent"].startswith("100 * level")
    assert answer["video_evidences"] == []
    assert answer["evidence_status"] == "pending"
    assert answer["matches"][0]["evidence_status"] == "pending"
    assert next(call[3] for call in fixture.calls if call[0] == "POST") == {
        "session_id": SESSION,
        "question": "Which finalized bottles were underfilled?",
        "limit": 10,
    }


@pytest.mark.parametrize("field,value", [("mode", "analyzed-replay"), ("session_id", OTHER), ("stream_id", OTHER)])
def test_query_rejects_foreign_response_before_returning_measurements(field, value):
    fixture = LiveFixture()
    fixture.answer[field] = value
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, "underfills")
    assert error.value.code == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", OTHER),
        ("stream_id", OTHER),
        ("measurement", {**MEASUREMENT, "pipeline_sha256": "e" * 64}),
        ("clock", {"method": "different-clock", "verified": False}),
    ],
)
def test_query_rechecks_live_identity_after_request(field, value):
    fixture = LiveFixture()
    fixture.query_mutation = lambda state: state.update({field: value})
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, "underfills")
    assert error.value.code == 4


@pytest.mark.parametrize("field,value", [("session_id", OTHER), ("stream_id", OTHER), ("epoch", 9)])
def test_event_identity_or_future_epoch_cannot_be_smuggled_through_valid_envelope(field, value):
    fixture = LiveFixture()
    fixture.events["events"][0][field] = value
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_events(SESSION)
    assert error.value.code == 4


def test_pending_evidence_never_exposes_unverified_plausible_urls():
    fixture = LiveFixture()
    speculative = {
        "session_id": SESSION,
        "stream_id": STREAM,
        "event_id": "event-1",
        "verified": False,
        "public_url": "https://unverified.test/guessed.mp4",
    }
    fixture.answer["video_evidences"] = [speculative]
    fixture.answer["matches"][0]["video_evidences"] = [speculative]
    with fixture.client() as client:
        answer = client.live_query(SESSION, "evidence")
    assert answer["video_evidences"] == []
    assert answer["matches"][0]["video_evidences"] == []
    assert "guessed.mp4" not in json.dumps(answer)
    assert answer["evidence_status"] == "pending"


def test_foreign_evidence_is_a_conflict_even_if_unverified():
    fixture = LiveFixture()
    fixture.answer["video_evidences"] = [{"session_id": OTHER, "stream_id": STREAM, "verified": False}]
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, "evidence")
    assert error.value.code == 4


def test_receiver_clock_cannot_be_mislabeled_verified():
    fixture = LiveFixture()
    fixture.state["clock"]["verified"] = True
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_status(SESSION)
    assert error.value.code == 4


def test_future_verified_clip_requires_matching_event_and_returned_url():
    fixture = LiveFixture()
    clock = {"method": "explicitly-verified-vios-pts-relation", "verified": True, "mapping_id": "reviewed-mapping"}
    for data in (fixture.state, fixture.answer, fixture.answer["matches"][0]):
        data["clock"] = clock
    clip = {
        "verified": True,
        "session_id": SESSION,
        "stream_id": STREAM,
        "event_id": "event-1",
        "public_url": "https://vss.test/vst/real-clip.mp4",
    }
    fixture.answer["video_evidences"] = [clip]
    fixture.answer["matches"][0]["video_evidences"] = [clip]
    with fixture.client() as client:
        answer = client.live_query(SESSION, "evidence")
        fixture.answer["video_evidences"][0]["event_id"] = "another-event"
        with pytest.raises(FillingError) as error:
            client.live_query(SESSION, "evidence")
    assert answer["video_evidences"] == [{**clip, "event_id": "event-1"}]
    assert answer["evidence_status"] == "verified"
    assert error.value.code == 4


def test_events_preserve_early_overflow_and_incomplete_without_incrementing_counts():
    fixture = LiveFixture()
    fixture.events["events"] = [
        {**deepcopy(EVENT), "kind": "overflow.detected", "status": "overflow", "final_level": None},
        {
            **deepcopy(EVENT),
            "event_id": "event-2",
            "seq": 2,
            "kind": "cycle.incomplete",
            "status": "incomplete",
            "final_level": None,
        },
    ]
    fixture.events["next_seq"] = 2
    with fixture.client() as client:
        result = client.live_events(SESSION, limit=2)
    assert result["summary"] == SUMMARY  # Never derives a second bottle count from an early overflow event.
    assert [event["kind"] for event in result["events"]] == ["overflow.detected", "cycle.incomplete"]
    assert result["next_seq"] == 2
    call = next(call for call in fixture.calls if call[1].endswith("/events"))
    assert call[2] == {"session_id": SESSION, "after_seq": "0", "limit": "2"}


@pytest.mark.parametrize(
    "kind,status", [("cycle.incomplete", "normal"), ("cycle.finalized", "incomplete"), ("overflow.detected", "normal")]
)
def test_invalid_provisional_finalized_semantics_rejected(kind, status):
    fixture = LiveFixture()
    fixture.events["events"][0].update(kind=kind, status=status)
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_events(SESSION)
    assert error.value.code == 4


@pytest.mark.parametrize("cursor", [-1, 0, 2])
def test_bad_returned_cursor_cannot_skip_or_replay_events(cursor):
    fixture = LiveFixture()
    fixture.events["next_seq"] = cursor
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_events(SESSION)
    assert error.value.code == 4


@pytest.mark.parametrize("after,limit", [(-1, 100), (0, 0), (0, 201), (True, 100)])
def test_invalid_cursor_request_fails_before_network(after, limit):
    fixture = LiveFixture()
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_events(SESSION, after_seq=after, limit=limit)
    assert error.value.code == 2
    assert fixture.calls == []


def test_empty_live_event_window_stays_empty():
    fixture = LiveFixture()
    fixture.events.update(events=[], next_seq=9)
    with fixture.client() as client:
        assert client.live_events(SESSION, after_seq=9)["events"] == []


def test_live_cli_grammar_binding_and_optional_status_discovery(monkeypatch):
    fixture = LiveFixture()
    deployment = config_mod.Deployment(
        base_url="https://vss.test", services={"filling": config_mod.Service("https://vss.test/filling")}
    )
    monkeypatch.setattr(group, "context_from", lambda values: Context(deployment, values.get("pretty")))
    monkeypatch.setattr(group, "FillingClient", lambda _endpoint: fixture.client())
    cli = group.FILLING.cli()
    assert set(cli.commands["live"].commands) == {"sources", "start", "status", "stop", "events", "query"}
    runner = CliRunner()
    for command in ("start", "stop", "events", "query"):
        assert runner.invoke(cli, ["live", command]).exit_code == 2
    assert runner.invoke(cli, ["live", "status"]).exit_code == 0
    assert runner.invoke(cli, ["live", "start", "--stream-id", STREAM, "--request-id", REQUEST]).exit_code == 0
    answer = runner.invoke(
        cli,
        ["live", "query", "--session-id", SESSION, "--stream-id", STREAM, "--question", "underfills"],
        catch_exceptions=False,
    )
    assert answer.exit_code == 0, answer.output
    assert json.loads(answer.stdout)["session_id"] == SESSION
    assert json.loads(answer.stdout)["video_evidences"] == []
    assert runner.invoke(cli, ["live", "events", "--session-id", SESSION, "--limit", "201"]).exit_code == 2
    assert "--endpoint" not in runner.invoke(cli, ["live", "query", "--help"]).output


@pytest.mark.parametrize("session_id", [None, "", "not-a-session"])
@pytest.mark.parametrize("operation", ["stop", "events", "query"])
def test_only_status_can_discover_without_an_explicit_session(session_id, operation):
    fixture = LiveFixture()
    with fixture.client() as client, pytest.raises(FillingError) as error:
        if operation == "query":
            client.live_query(session_id, "underfills")
        else:
            getattr(client, "live_" + operation)(session_id)
    assert error.value.code == 2
    assert fixture.calls == []


@pytest.mark.parametrize("verdict", ["normal", "underfill"])
def test_unknown_final_height_cannot_be_claimed_normal_or_underfilled(verdict):
    fixture = LiveFixture()
    fixture.answer["matches"][0].update(final_level=None, status=verdict)
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, "finalized bottles")
    assert error.value.code == 4


def lookup_fixture(number=305, epoch=1):
    fixture = LiveFixture()
    fixture.state["epoch"] = epoch
    track = f"{SESSION}:epoch-{epoch}:cycle-{number}"
    fixture.answer["matches"][0].update(track_id=track, event_id=track + ":inspection", epoch=epoch, seq=999)
    fixture.answer.update(
        query_status="ok",
        requested_cycle_ids=[f"cycle-{number}"],
        candidates=[],
        total_matches=1,
        truncated=False,
        current={},
    )
    return fixture


def test_exact_cycle_lookup_is_source_session_epoch_bound_and_not_event_sequence():
    fixture = lookup_fixture()
    with fixture.client() as client:
        answer = client.live_query(SESSION, cycle_id="cycle-0305", stream_id=STREAM, epoch=1, limit=3)
    assert answer["matches"][0]["track_id"].endswith(":cycle-305")
    assert answer["matches"][0]["seq"] == 999
    body = next(call[3] for call in fixture.calls if call[0] == "POST")
    assert body == {
        "session_id": SESSION,
        "question": "What happened with the requested bottle cycle?",
        "cycle_id": "cycle-0305",
        "epoch": 1,
        "limit": 3,
    }


@pytest.mark.parametrize("question", ["What happened with bottle cycle-305?", "What happened with bottle cyce-305?"])
def test_natural_cycle_question_requires_exact_typed_lookup(question):
    fixture = lookup_fixture()
    with fixture.client() as client:
        assert client.live_query(SESSION, question)["query_status"] == "ok"
    fixture.answer["matches"][0]["track_id"] = f"{SESSION}:epoch-1:cycle-306"
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, question)
    assert error.value.code == 4


@pytest.mark.parametrize(
    "options,code",
    [
        ({"cycle_id": "305"}, 2),
        ({"cycle_id": "cycle-0"}, 2),
        ({"cycle_id": f"{OTHER}:epoch-1:cycle-305"}, 4),
        ({"cycle_id": f"{SESSION}:epoch-2:cycle-305", "epoch": 1}, 4),
        ({"epoch": 0}, 2),
        ({"epoch": True}, 2),
        ({"limit": 0}, 2),
        ({"limit": 51}, 2),
        ({"limit": True}, 2),
    ],
)
def test_invalid_or_conflicting_cycle_scope_fails_before_network(options, code):
    fixture = lookup_fixture()
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, "What happened?", **options)
    assert error.value.code == code
    assert fixture.calls == []


@pytest.mark.parametrize("status", ["not_found", "unsupported"])
def test_missing_or_unsupported_cycle_returns_typed_answer_without_other_cycles(status):
    fixture = lookup_fixture()
    fixture.answer.update(query_status=status, matches=[], total_matches=0)
    with fixture.client() as client:
        answer = client.live_query(SESSION, cycle_id="cycle-305")
    assert answer["query_status"] == status and answer["matches"] == []


def test_in_progress_cycle_is_current_only_not_a_finalized_match():
    fixture = lookup_fixture()
    fixture.answer.update(
        query_status="in_progress",
        matches=[],
        total_matches=0,
        epoch=1,
        current={
            "track_id": f"{SESSION}:epoch-1:cycle-305",
            "epoch": 1,
            "level": 0.2,
            "phase": "filling",
            "provisional": True,
        },
    )
    with fixture.client() as client:
        answer = client.live_query(SESSION, cycle_id="cycle-305")
    assert answer["matches"] == [] and answer["current"]["phase"] == "filling"


def test_ambiguous_epochs_preserve_candidates_without_silently_selecting_current():
    fixture = lookup_fixture(epoch=2)
    history = [{"epoch": i, "measurement": deepcopy(MEASUREMENT), "clock": deepcopy(CLOCK)} for i in [1, 2]]
    fixture.state["measurement_history"] = history
    fixture.answer.update(
        query_status="ambiguous",
        matches=[],
        total_matches=2,
        measurement_history=deepcopy(history),
        candidates=[
            {
                "session_id": SESSION,
                "stream_id": STREAM,
                "epoch": i,
                "track_id": f"{SESSION}:epoch-{i}:cycle-305",
                "status": "normal",
            }
            for i in [1, 2]
        ],
    )
    with fixture.client() as client:
        answer = client.live_query(SESSION, cycle_id="cycle-305")
    assert len(answer["candidates"]) == 2 and not answer["matches"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.update(requested_cycle_ids=["cycle-306"]),
        lambda a: a.update(requested_cycle_ids=["cycle-305", "cycle-305"]),
        lambda a: a.update(query_status="not_found"),
        lambda a: a.update(query_status="in_progress", matches=[], current={}),
        lambda a: a.update(total_matches=-1),
        lambda a: a.update(truncated="false"),
        lambda a: a["matches"][0].update(epoch=2),
        lambda a: a["matches"][0].update(track_id=f"{OTHER}:epoch-1:cycle-305"),
    ],
)
def test_cycle_lookup_rejects_wrong_identity_or_lookup_semantics(mutation):
    fixture = lookup_fixture()
    mutation(fixture.answer)
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305")
    assert error.value.code == 4


def historical_fixture():
    fixture = lookup_fixture()
    old = deepcopy(MEASUREMENT)
    old["pipeline_sha256"] = "e" * 64
    fixture.state["epoch"] = 2
    history = [
        {"epoch": 1, "measurement": old, "clock": deepcopy(CLOCK)},
        {"epoch": 2, "measurement": deepcopy(MEASUREMENT), "clock": deepcopy(CLOCK)},
    ]
    fixture.state["measurement_history"] = deepcopy(history)
    fixture.answer["measurement_history"] = deepcopy(history)
    fixture.answer["matches"][0]["measurement"] = deepcopy(old)
    return fixture


def test_historical_cycle_preserves_its_own_declared_pipeline_after_session_resume():
    fixture = historical_fixture()
    with fixture.client() as client:
        answer = client.live_query(SESSION, cycle_id=f"{SESSION}:epoch-1:cycle-305")
    assert answer["matches"][0]["measurement"]["pipeline_sha256"] == "e" * 64
    assert answer["measurement"]["pipeline_sha256"] == "c" * 64


@pytest.mark.parametrize(
    "mutation",
    [
        lambda f: f.state.pop("measurement_history"),
        lambda f: f.state["measurement_history"].append(deepcopy(f.state["measurement_history"][0])),
        lambda f: f.answer["matches"][0]["clock"].update(qualification="invented"),
        lambda f: f.answer["matches"][0]["measurement"].update(recorded_pipeline_sha256="f" * 64),
        lambda f: f.answer["measurement_history"][0]["measurement"].update(pipeline_sha256="f" * 64),
    ],
)
def test_historical_cycle_requires_exact_authoritative_epoch_history(mutation):
    fixture = historical_fixture()
    mutation(fixture)
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305")
    assert error.value.code == 4


def test_history_cannot_override_source_binding_with_extra_identity_fields():
    fixture = historical_fixture()
    fixture.state["measurement_history"][0]["stream_id"] = OTHER
    fixture.answer["measurement_history"] = deepcopy(fixture.state["measurement_history"])
    fixture.answer["matches"][0]["stream_id"] = OTHER
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305")
    assert error.value.code == 4


def test_historical_identity_is_rechecked_after_query():
    fixture = historical_fixture()
    fixture.query_mutation = lambda state: state["measurement_history"][0]["measurement"].update(
        pipeline_sha256="f" * 64
    )
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305")
    assert error.value.code == 4


def test_live_cycle_cli_flags_allow_exact_lookup_without_a_synthetic_user_question(monkeypatch):
    fixture = lookup_fixture()
    deployment = config_mod.Deployment(
        base_url="https://vss.test", services={"filling": config_mod.Service("https://vss.test/filling")}
    )
    monkeypatch.setattr(group, "context_from", lambda values: Context(deployment, values.get("pretty")))
    monkeypatch.setattr(group, "FillingClient", lambda _endpoint: fixture.client())
    runner = CliRunner()
    cli = group.FILLING.cli()
    answer = runner.invoke(
        cli,
        [
            "live",
            "query",
            "--session-id",
            SESSION,
            "--stream-id",
            STREAM,
            "--cycle-id",
            "cycle-305",
            "--epoch",
            "1",
            "--limit",
            "5",
        ],
        catch_exceptions=False,
    )
    assert answer.exit_code == 0, answer.output
    assert json.loads(answer.stdout)["matches"][0]["seq"] == 999
    assert runner.invoke(cli, ["live", "query", "--session-id", SESSION]).exit_code == 2
    assert (
        runner.invoke(
            cli, ["live", "query", "--session-id", SESSION, "--cycle-id", "cycle-305", "--epoch", "0"]
        ).exit_code
        == 2
    )


def test_exact_decoded_snapshot_is_usable_without_claiming_verified_vios_video_time():
    fixture = lookup_fixture()
    snapshot = {
        "session_id": SESSION,
        "stream_id": STREAM,
        "event_id": fixture.answer["matches"][0]["event_id"],
        "frame_id": 500,
        "source_pts_seconds": 10.0,
        "verified": True,
        "verification": "exact-decoded-frame",
        "mime_type": "image/jpeg",
        "public_url": "https://vss.test/filling/api/live/evidence?actual=returned",
    }
    fixture.answer["snapshot_evidences"] = [snapshot]
    fixture.answer["matches"][0]["snapshot_evidences"] = [snapshot]
    with fixture.client() as client:
        answer = client.live_query(SESSION, cycle_id="cycle-305")
    assert answer["snapshot_evidences"] == [snapshot]
    assert answer["clock"]["verified"] is False
    assert answer["video_evidences"] == [] and answer["evidence_status"] == "pending"
    assert answer["snapshot_evidence_status"] == "verified"


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", OTHER),
        ("stream_id", OTHER),
        ("event_id", "another-event"),
        ("verification", "approximate-frame"),
        ("frame_id", 0),
        ("frame_id", True),
        ("source_pts_seconds", -1.0),
        ("mime_type", "video/mp4"),
        ("public_url", "javascript:bad"),
    ],
)
def test_snapshot_links_require_exact_returned_frame_and_source_event_identity(field, value):
    fixture = lookup_fixture()
    snapshot = {
        "session_id": SESSION,
        "stream_id": STREAM,
        "event_id": fixture.answer["matches"][0]["event_id"],
        "frame_id": 500,
        "source_pts_seconds": 10.0,
        "verified": True,
        "verification": "exact-decoded-frame",
        "mime_type": "image/jpeg",
        "public_url": "https://vss.test/actual.jpg",
    }
    snapshot[field] = value
    fixture.answer["snapshot_evidences"] = [snapshot]
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305")
    assert error.value.code == 4


def test_current_short_cycle_label_requires_explicit_response_epoch():
    fixture = lookup_fixture()
    fixture.answer.update(
        query_status="in_progress",
        matches=[],
        total_matches=0,
        epoch=1,
        current={"track_id": "cycle-305", "phase": "filling", "provisional": True, "level": 0.3},
    )
    with fixture.client() as client:
        answer = client.live_query(SESSION, cycle_id="cycle-305")
    assert answer["current"]["track_id"] == "cycle-305"
    assert answer["epoch"] == 1 and answer["matches"] == []
    fixture.answer["epoch"] = 2
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305")
    assert error.value.code == 4


def operator_fixture():
    fixture = historical_fixture()
    fixture.answer["answer"] = "cycle-305 was underfilled at 60.0% visible bottle height."
    fixture.answer["current"] = {"quality": {"reason": "unrelated current glare"}, "source_pts_seconds": 5000}
    event = fixture.answer["matches"][0]
    snapshots = [
        {
            "session_id": SESSION,
            "stream_id": STREAM,
            "event_id": event["event_id"],
            "frame_id": frame,
            "source_pts_seconds": 10.0,
            "verified": True,
            "verification": "exact-decoded-frame",
            "mime_type": "image/jpeg",
            "public_url": f"https://vss.test/filling/api/live/evidence?session_id={SESSION}&event_id={event['event_id']}&frame_id={frame}",
        }
        for frame in range(100, 107)
    ]
    event["snapshot_evidences"] = snapshots
    event["diagnostics"] = {"internal_path": "/private/history.json", "quality": "raw diagnostic"}
    fixture.answer["snapshot_evidences"] = [snapshots[0], snapshots[3], snapshots[6]]
    return fixture


def test_operator_view_uses_authoritative_answer_and_only_representative_images():
    fixture = operator_fixture()
    with fixture.client() as client:
        actual = client.live_query(SESSION, cycle_id="cycle-305", operator_view=True)
    assert actual["answer"] == fixture.answer["answer"]
    assert actual["display_markdown"].startswith(fixture.answer["answer"] + "\n\n")
    assert actual["session_id"] == SESSION and actual["stream_id"] == STREAM
    assert [s["frame_id"] for s in actual["snapshot_evidences"]] == [100, 103, 106]
    assert len(actual["ui_artifacts"]) == 3
    for artifact, snapshot in zip(actual["ui_artifacts"], actual["snapshot_evidences"], strict=True):
        envelope = json.loads(artifact.removeprefix("<vss-ui-artifact>").removesuffix("</vss-ui-artifact>"))
        assert envelope["kind"] == "vss.media.image"
        assert envelope["payload"]["media_url"] == snapshot["public_url"].removeprefix("https://vss.test")
        assert snapshot["public_url"] in actual["display_markdown"]
    assert actual["matches"][0]["epoch"] == 1
    for omitted in [
        "current",
        "measurement",
        "measurement_history",
        "source_pts_seconds",
        "diagnostics",
        "private",
        "glare",
    ]:
        assert omitted not in actual
        if omitted in {"private", "glare"}:
            assert omitted not in json.dumps(actual)
    assert actual["video_evidences"] == [] and actual["evidence_status"] == "pending"


def test_operator_view_caps_top_level_images_without_collecting_nested_frames():
    fixture = operator_fixture()
    fixture.answer["snapshot_evidences"] = fixture.answer["matches"][0]["snapshot_evidences"]
    with fixture.client() as client:
        actual = client.live_query(SESSION, cycle_id="cycle-305", operator_view=True)
    assert [s["frame_id"] for s in actual["snapshot_evidences"]] == [100, 101, 102]
    fixture.answer["snapshot_evidences"] = []
    with fixture.client() as client:
        actual = client.live_query(SESSION, cycle_id="cycle-305", operator_view=True)
    assert not actual["ui_artifacts"] and not actual["snapshot_evidences"]
    assert actual["display_markdown"] == fixture.answer["answer"]


def test_operator_view_does_not_change_full_engineering_output():
    fixture = operator_fixture()
    with fixture.client() as client:
        actual = client.live_query(SESSION, cycle_id="cycle-305")
    assert actual["current"]["quality"]["reason"] == "unrelated current glare"
    assert len(actual["matches"][0]["snapshot_evidences"]) == 7
    assert "ui_artifacts" not in actual and "display_markdown" not in actual


def test_operator_view_still_validates_omitted_nested_evidence():
    fixture = operator_fixture()
    fixture.answer["matches"][0]["snapshot_evidences"][5]["stream_id"] = OTHER
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305", operator_view=True)
    assert error.value.code == 4


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://other.test/filling/api/live/evidence",
        "https://vss.test/private/image.jpg",
        "https://vss.test/filling/api/live/evidence?frame_id=999",
    ],
)
def test_operator_artifact_requires_same_origin_and_exact_frame_query(bad_url):
    fixture = operator_fixture()
    fixture.answer["snapshot_evidences"][0]["public_url"] = bad_url
    with fixture.client() as client, pytest.raises(FillingError) as error:
        client.live_query(SESSION, cycle_id="cycle-305", operator_view=True)
    assert error.value.code == 4


def test_operator_view_preserves_only_explicit_provisional_current():
    fixture = lookup_fixture()
    fixture.answer.update(
        query_status="in_progress",
        matches=[],
        total_matches=0,
        epoch=1,
        current={"track_id": "cycle-305", "phase": "filling", "provisional": True, "level": 0.3, "diagnostics": {}},
    )
    with fixture.client() as client:
        actual = client.live_query(SESSION, cycle_id="cycle-305", operator_view=True)
    assert actual["current"] == {"track_id": "cycle-305", "phase": "filling", "provisional": True, "level": 0.3}
    assert actual["epoch"] == 1 and actual["matches"] == []


def test_live_operator_cli_flag_is_opt_in_and_never_sent_to_api(monkeypatch):
    fixture = operator_fixture()
    deployment = config_mod.Deployment(
        base_url="https://vss.test", services={"filling": config_mod.Service("https://vss.test/filling")}
    )
    monkeypatch.setattr(group, "context_from", lambda values: Context(deployment, values.get("pretty")))
    monkeypatch.setattr(group, "FillingClient", lambda _endpoint: fixture.client())
    result = CliRunner().invoke(
        group.FILLING.cli(),
        [
            "live",
            "query",
            "--session-id",
            SESSION,
            "--cycle-id",
            "cycle-305",
            "--operator-view",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["view"] == "operator"
    post = next(call[3] for call in fixture.calls if call[0] == "POST")
    assert "operator_view" not in post


def test_selected_track_pair_preserves_full_identity_and_rejects_other_bottle():
    fixture = lookup_fixture()
    first = fixture.answer['matches'][0]
    second = deepcopy(first)
    second.update(event_id='second', seq=1000, track_id=f'{SESSION}:epoch-1:cycle-306', status='normal', final_level=.72803)
    selected = [first['track_id'], second['track_id']]
    fixture.answer.update(matches=[first, second], requested_cycle_ids=['cycle-305', 'cycle-306'], selected_track_ids=selected, total_matches=2)
    with fixture.client() as client:
        actual = client.live_query(SESSION, 'Compare these two bottles.', cycle_ids=selected, operator_view=True)
    assert [row['track_id'] for row in actual['matches']] == selected
    assert actual['render_policy'] == 'authoritative_measurement'
    post = next(call[3] for call in fixture.calls if call[0] == 'POST')
    assert post['cycle_ids'] == selected and 'epoch' not in post
    fixture.answer['matches'][1]['track_id'] = f'{SESSION}:epoch-1:cycle-307'
    with fixture.client() as client, pytest.raises(FillingError):
        client.live_query(SESSION, 'Compare these two bottles.', cycle_ids=selected, operator_view=True)


def test_selected_cycles_require_full_nonconflicting_identities():
    fixture = lookup_fixture()
    with fixture.client() as client:
        with pytest.raises(FillingError):
            client.live_query(SESSION, 'What happened with this bottle?', cycle_ids=['cycle-305'])
        with pytest.raises(FillingError):
            client.live_query(SESSION, 'What happened with this bottle?', cycle_ids=[f'{OTHER}:epoch-1:cycle-305'])
        with pytest.raises(FillingError):
            client.live_query(SESSION, 'What happened with this bottle?', cycle_ids=[f'{SESSION}:epoch-1:cycle-305'], epoch=2)
