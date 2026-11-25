"""Bucket lifecycle backstop for archived ITS frames (backend.api.sc_lifecycle)."""

import asyncio

import httpx
import pytest

from backend.api import sc_lifecycle
from backend.api.sc_lifecycle import (
    RULE_ID,
    expiry_days,
    frame_tagging,
    lifecycle_rule,
    parse_version,
    sync_lifecycle_rule,
)
from backend.config import settings

OTHER = {"ID": "someone-else", "Status": "Enabled", "Filter": {"Prefix": "tmp/"}, "Expiration": {"Days": 1}}


class FakeBucket:
    def __init__(self, rules=None):
        self.rules = list(rules or [])
        self.writes: list[list[dict]] = []

    async def get_lifecycle_rules(self):
        return list(self.rules)

    async def put_lifecycle_rules(self, rules):
        self.writes.append(list(rules))
        self.rules = list(rules)


@pytest.fixture(autouse=True)
def lifecycle_settings(monkeypatch):
    monkeypatch.setattr(settings, "sc_frame_tagging", True)
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", True)
    monkeypatch.setattr(settings, "sc_gc_enabled", True)
    monkeypatch.setattr(settings, "sc_gc_retention_days", 14.0)
    monkeypatch.setattr(settings, "sc_gc_hold_s", 48 * 3600)
    monkeypatch.setattr(settings, "its_object_prefix", "deft-its")


def master(monkeypatch, version="30GB 4.48 1a2b3c", status=200):
    real = httpx.AsyncClient
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, json={"Version": version, "Topology": {}})

    monkeypatch.setattr(settings, "seaweedfs_master_url", "http://master:9333/")
    monkeypatch.setattr(
        sc_lifecycle.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


# ------------------------------------------------------------------ tagging


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("its-train", "deft-retention=its-frame"),
        (None, "deft-retention=its-frame"),
        ("kpi", None),
        ("kpi-golden", None),
    ],
)
def test_every_frame_but_a_kpi_one_is_tagged(tag, expected):
    assert frame_tagging(tag) == expected


def test_tagging_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "sc_frame_tagging", False)
    assert frame_tagging("its-train") is None


# --------------------------------------------------------------------- rule


@pytest.mark.parametrize(
    ("retention", "hold_s", "days"),
    [(14.0, 48 * 3600, 17), (14.5, 48 * 3600, 18), (7.0, 0, 8), (1.0, 3600, 3)],
)
def test_frames_expire_a_day_after_retention_plus_hold(monkeypatch, retention, hold_s, days):
    monkeypatch.setattr(settings, "sc_gc_retention_days", retention)
    monkeypatch.setattr(settings, "sc_gc_hold_s", hold_s)
    assert expiry_days() == days


def test_the_rule_needs_both_the_its_prefix_and_the_tag():
    rule = lifecycle_rule()
    assert rule["Filter"] == {
        "And": {
            "Prefix": "deft-its/",
            "Tags": [{"Key": "deft-retention", "Value": "its-frame"}],
        }
    }
    assert rule["Expiration"] == {"Days": 17}
    assert rule["ID"] == RULE_ID and rule["Status"] == "Enabled"


@pytest.mark.parametrize(
    ("text", "version"),
    [
        ("30GB 4.48 1a2b3c", (4, 48)),
        ("8000GB 3.90 ", (3, 90)),
        ("30GB 4.24", (4, 24)),
        ("", None),
        ("unknown", None),
    ],
)
def test_parse_version(text, version):
    assert parse_version(text) == version


# --------------------------------------------------------------------- sync


async def test_the_rule_is_added_next_to_other_rules(monkeypatch):
    seen = master(monkeypatch)
    bucket = FakeBucket([OTHER])

    outcome = await sync_lifecycle_rule(bucket)

    assert outcome.startswith("set")
    assert bucket.rules == [OTHER, lifecycle_rule()]
    assert seen == ["http://master:9333/dir/status"]


async def test_an_identical_rule_is_not_written_again(monkeypatch):
    master(monkeypatch)
    stored = lifecycle_rule()
    # A store answers with defaults filled in and tags in any order.
    stored["Expiration"]["ExpiredObjectDeleteMarker"] = False
    stored["Filter"]["And"]["ObjectSizeGreaterThan"] = 0
    bucket = FakeBucket([OTHER, stored])

    assert await sync_lifecycle_rule(bucket) == "unchanged"
    assert bucket.writes == []


async def test_a_stale_rule_is_replaced(monkeypatch):
    master(monkeypatch)
    stale = lifecycle_rule()
    stale["Expiration"] = {"Days": 30}
    bucket = FakeBucket([stale, OTHER])

    await sync_lifecycle_rule(bucket)

    assert bucket.rules == [OTHER, lifecycle_rule()]


@pytest.mark.parametrize("version", ["30GB 4.17 abc", "30GB 4.23 abc", "8000GB 3.90 abc"])
async def test_older_seaweedfs_never_gets_the_rule(monkeypatch, version):
    """4.17 and older make it a TTL on the whole bucket; 4.18-4.23 never run it."""
    master(monkeypatch, version=version)
    bucket = FakeBucket([OTHER])

    outcome = await sync_lifecycle_rule(bucket)

    assert outcome.startswith("refused")
    assert bucket.writes == []


@pytest.mark.parametrize("problem", ["no-url", "unreachable", "no-version"])
async def test_an_unknown_version_never_gets_the_rule(monkeypatch, problem):
    if problem == "no-url":
        monkeypatch.setattr(settings, "seaweedfs_master_url", "")
    elif problem == "unreachable":
        master(monkeypatch, status=503)
    else:
        master(monkeypatch, version="")
    bucket = FakeBucket()

    assert (await sync_lifecycle_rule(bucket)).startswith("refused")
    assert bucket.writes == []


async def test_the_rule_needs_the_retention_pass(monkeypatch):
    master(monkeypatch)
    monkeypatch.setattr(settings, "sc_gc_enabled", False)
    bucket = FakeBucket()

    assert (await sync_lifecycle_rule(bucket)).startswith("refused")
    assert bucket.writes == []


async def test_switching_off_removes_only_this_rule(monkeypatch):
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", False)
    bucket = FakeBucket([OTHER, lifecycle_rule()])

    assert await sync_lifecycle_rule(bucket) == "removed"
    assert bucket.rules == [OTHER]


async def test_switching_off_the_only_rule_leaves_no_configuration(monkeypatch):
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", False)
    bucket = FakeBucket([lifecycle_rule()])

    await sync_lifecycle_rule(bucket)

    assert bucket.writes == [[]]


async def test_off_with_no_rule_writes_nothing(monkeypatch):
    monkeypatch.setattr(settings, "sc_lifecycle_enabled", False)
    bucket = FakeBucket([OTHER])

    assert await sync_lifecycle_rule(bucket) == "off"
    assert bucket.writes == []


# ---------------------------------------------------------------- startup


@pytest.mark.parametrize(
    ("lifecycle", "gc", "synced"),
    [(False, False, False), (True, False, True), (False, True, True)],
    ids=["aoi-only", "lifecycle-on", "gc-on"],
)
def test_only_an_its_backend_touches_the_bucket_lifecycle(monkeypatch, lifecycle, gc, synced):
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi.testclient import TestClient

    from backend.application import app

    monkeypatch.setattr(settings, "sc_lifecycle_enabled", lifecycle)
    monkeypatch.setattr(settings, "sc_gc_enabled", gc)
    monkeypatch.setattr(settings, "sc_consumer_enabled", False)
    sync = AsyncMock(return_value="off")
    with patch("backend.application.KafkaService", return_value=MagicMock(start=AsyncMock(), stop=AsyncMock())), \
         patch("backend.application.AoiClientService", return_value=MagicMock(close=AsyncMock())), \
         patch("backend.application.ElasticsearchService", return_value=MagicMock(close=AsyncMock())), \
         patch("backend.application.StorageService", return_value=MagicMock()), \
         patch("backend.application.gc_loop", AsyncMock()), \
         patch("backend.application.sync_lifecycle_rule", sync), \
         TestClient(app) as client:
        task = app.state.sc_lifecycle_task
        assert (task is not None) == synced
        if task is not None:
            client.portal.call(asyncio.wait_for, task, 5)

    assert sync.await_count == (1 if synced else 0)
