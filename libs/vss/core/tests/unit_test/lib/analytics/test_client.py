# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for Video Analytics API translation and normalization."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from vss_core.analytics import AnalyticsClient
from vss_core.analytics import AnalyticsError
from vss_core.analytics import AnalyticsInvalidInputError
from vss_core.analytics import AnalyticsNotFoundError
from vss_core.analytics import AnalyticsTimeoutError
from vss_core.analytics import client as client_mod


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]]:
    calls: list[tuple[str, dict[str, object] | None]] = []
    responses: dict[str, object] = {}

    async def fake(
        _session: aiohttp.ClientSession,
        base_url: str,
        path: str,
        *,
        params: dict[str, object] | None,
        operation: str,
        timeout_seconds: float,
    ) -> object:
        assert base_url == "https://vss.test/video-analytics-api"
        assert operation
        assert timeout_seconds == 30
        calls.append((path, params))
        return responses[path]

    monkeypatch.setattr(client_mod, "_request_json", fake)
    return calls, responses


@pytest.fixture
def client() -> AnalyticsClient:
    return AnalyticsClient("https://vss.test/video-analytics-api")


@pytest.mark.asyncio
async def test_incident_list_translates_api_parameters_and_includes(
    client: AnalyticsClient,
    transport: tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]],
) -> None:
    calls, responses = transport
    responses["incidents"] = {
        "incidents": [
            {
                "id": "i-1",
                "timestamp": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:00:01Z",
                "sensorId": "cam",
                "info": {"verdict": "confirmed"},
                "ignored": True,
            }
        ]
    }
    rows = await client.incidents(
        source="cam",
        source_type="sensor",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T01:00:00Z",
        limit=10,
        includes=("info",),
        vlm_verdict="confirmed",
    )
    assert rows == [
        {
            "id": "i-1",
            "timestamp": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T00:00:01Z",
            "sensorId": "cam",
            "info": {"verdict": "confirmed"},
        }
    ]
    assert calls == [
        (
            "incidents",
            {
                "maxResultSize": 10,
                "sensorId": "cam",
                "fromTimestamp": "2026-01-01T00:00:00Z",
                "toTimestamp": "2026-01-01T01:00:00Z",
                "vlmVerified": "true",
                "vlmVerdict": "confirmed",
            },
        )
    ]


@pytest.mark.asyncio
async def test_incident_lookup_and_not_found(
    client: AnalyticsClient,
    transport: tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]],
) -> None:
    calls, responses = transport
    responses["incidents"] = {"incidents": [{"Id": "i-1", "category": "entry"}]}
    assert await client.incident("i-1", ("category",)) == {"Id": "i-1", "category": "entry"}
    assert calls[0][1] == {"queryString": 'Id:"i-1" OR id:"i-1"', "maxResultSize": 2}

    responses["incidents"] = {"incidents": []}
    with pytest.raises(AnalyticsNotFoundError):
        await client.incident("missing")
    assert calls[-2][1] == {"queryString": 'Id:"missing" OR id:"missing"', "maxResultSize": 2}
    assert calls[-1][1] == {
        "queryString": 'Id:"missing" OR id:"missing"',
        "maxResultSize": 2,
        "vlmVerified": "true",
    }


@pytest.mark.asyncio
async def test_incident_lookup_falls_back_to_vlm_index(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object] | None] = []

    async def fake(
        _session: aiohttp.ClientSession,
        _base_url: str,
        _path: str,
        *,
        params: dict[str, object] | None,
        operation: str,
        timeout_seconds: float,
    ) -> object:
        assert operation == "incident lookup"
        assert timeout_seconds == 30
        calls.append(params)
        if params and params.get("vlmVerified") == "true":
            return {"incidents": [{"Id": "vlm-1"}]}
        return {"incidents": []}

    monkeypatch.setattr(client_mod, "_request_json", fake)
    assert await AnalyticsClient("https://vss.test/video-analytics-api").incident("vlm-1") == {"Id": "vlm-1"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_sensor_and_place_normalization(
    client: AnalyticsClient,
    transport: tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]],
) -> None:
    calls, responses = transport
    responses["config/calibration"] = {
        "sensors": [
            {
                "id": "cam-2",
                "place": [
                    {"name": "building", "value": "Warehouse"},
                    {"name": "room", "value": "Room-1"},
                    {"name": "aisle", "value": "A"},
                ],
            },
            {
                "id": "cam-1",
                "place": [
                    {"name": "building", "value": "Warehouse"},
                    {"name": "room", "value": "Room-2"},
                ],
            },
            {
                "id": "cam-3",
                "place": [{"name": "building", "value": "Annex"}],
            },
            {
                "id": "cam-10",
                "place": [
                    {"name": "building", "value": "Warehouse"},
                    {"name": "room", "value": "Room-10"},
                ],
            },
            {"id": "orphan"},
        ]
    }
    assert await client.sensors() == ["cam-1", "cam-10", "cam-2", "cam-3", "orphan"]
    assert await client.sensors("building=Warehouse") == ["cam-1", "cam-10", "cam-2"]
    assert await client.sensors("building=Warehouse/room=Room-1") == ["cam-2"]
    assert await client.sensors("building=Warehouse/room=Room-10") == ["cam-10"]
    assert await client.sensors("Warehouse") == []
    places = await client.places()
    assert places == [
        "building=Annex",
        "building=Warehouse/room=Room-1/aisle=A",
        "building=Warehouse/room=Room-10",
        "building=Warehouse/room=Room-2",
    ]
    responses["incidents"] = {"incidents": []}
    await client.incidents(source=places[1], source_type="place")
    assert calls[-1][1] == {
        "maxResultSize": 10,
        "place": "building=Warehouse/room=Room-1/aisle=A",
    }


@pytest.mark.asyncio
async def test_histogram_and_average_speed_translation(
    client: AnalyticsClient,
    transport: tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]],
) -> None:
    calls, responses = transport
    responses["metrics/occupancy/fov/histogram"] = {
        "bucketSizeInSec": 5,
        "histogram": [],
        "sensorId": "cam",
    }
    responses["metrics/average-speed"] = {"metrics": [{"direction": "North", "averageSpeed": "25 mph"}]}
    histogram = await client.fov_histogram(
        source="cam",
        source_type="sensor",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:01:00Z",
        object_type="Person",
        bucket_count=12,
    )
    speed = await client.average_speed(
        source="building=Warehouse/room=Room-1",
        source_type="place",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:01:00Z",
    )
    assert histogram == {"bucketSizeInSec": 5, "histogram": []}
    assert speed["metrics"][0]["averageSpeed"] == "25 mph"
    assert calls == [
        (
            "metrics/occupancy/fov/histogram",
            {
                "sensorId": "cam",
                "fromTimestamp": "2026-01-01T00:00:00Z",
                "toTimestamp": "2026-01-01T00:01:00Z",
                "bucketCount": 12,
                "objectType": "Person",
            },
        ),
        (
            "metrics/average-speed",
            {
                "fromTimestamp": "2026-01-01T00:00:00Z",
                "toTimestamp": "2026-01-01T00:01:00Z",
                "place": "building=Warehouse/room=Room-1",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_place_histogram_sums_sensor_occupancy(
    client: AnalyticsClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_requests = 0
    maximum_active_requests = 0

    async def sensors(place: str | None = None) -> list[str]:
        assert place == "building=Warehouse"
        return ["cam-1", "cam-2"]

    results = iter(
        [
            {
                "bucketSizeInSec": 5,
                "histogram": [
                    {
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-01T00:00:05Z",
                        "objects": [{"type": "Person", "averageCount": 2}],
                    }
                ],
            },
            {
                "bucketSizeInSec": 5,
                "histogram": [
                    {
                        "start": "2026-01-01T00:00:00Z",
                        "end": "2026-01-01T00:00:05Z",
                        "objects": [{"type": "Person", "averageCount": 3}],
                    }
                ],
            },
        ]
    )

    async def get(_path: str, _operation: str, _params: object = None) -> object:
        nonlocal active_requests, maximum_active_requests
        active_requests += 1
        maximum_active_requests = max(maximum_active_requests, active_requests)
        await asyncio.sleep(0)
        result = next(results)
        active_requests -= 1
        return result

    monkeypatch.setattr(client, "sensors", sensors)
    monkeypatch.setattr(client, "_get", get)
    body = await client.fov_histogram(
        source="building=Warehouse",
        source_type="place",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:00:05Z",
    )
    assert maximum_active_requests == 2
    assert body["histogram"][0]["objects"] == [{"type": "Person", "averageCount": 5.0}]


@pytest.mark.asyncio
async def test_place_histogram_reuses_one_session(
    client: AnalyticsClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions: set[int] = set()

    async def fake(
        session: aiohttp.ClientSession,
        _base_url: str,
        path: str,
        *,
        params: dict[str, object] | None,
        operation: str,
        timeout_seconds: float,
    ) -> object:
        sessions.add(id(session))
        assert operation
        assert timeout_seconds == 30
        if path == "config/calibration":
            return {
                "sensors": [
                    {
                        "id": "cam-1",
                        "place": [{"name": "building", "value": "Warehouse"}],
                    },
                    {
                        "id": "cam-2",
                        "place": [{"name": "building", "value": "Warehouse"}],
                    },
                ]
            }
        assert params is not None
        return {"bucketSizeInSec": 5, "histogram": []}

    monkeypatch.setattr(client_mod, "_request_json", fake)
    await client.fov_histogram(
        source="building=Warehouse",
        source_type="place",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:00:05Z",
    )
    assert len(sessions) == 1


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class _Session:
    response: _Response | Exception

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def get(self, _url: str, **_kwargs: object) -> _Response:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_type", "message"),
    [
        (_Response(400, '{"error":"invalid sensor"}'), AnalyticsInvalidInputError, "HTTP 400"),
        (_Response(404, '{"error":"not found"}'), AnalyticsNotFoundError, "HTTP 404"),
        (_Response(503, '{"error":"Elasticsearch unavailable"}'), AnalyticsError, "HTTP 503"),
        (_Response(200, "{malformed"), AnalyticsError, "malformed JSON"),
        (aiohttp.ClientConnectionError("refused"), AnalyticsError, "connection failed"),
        (TimeoutError(), AnalyticsTimeoutError, "timed out"),
    ],
)
async def test_transport_maps_operational_failures(
    monkeypatch: pytest.MonkeyPatch,
    response: _Response | Exception,
    error_type: type[Exception],
    message: str,
) -> None:
    _Session.response = response
    monkeypatch.setattr(client_mod.aiohttp, "ClientSession", _Session)
    with pytest.raises(error_type, match=message):
        await AnalyticsClient("https://vss.test/video-analytics-api", timeout_seconds=2).incidents()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "analysis_type",
    ["average-speed", "avg-num-people", "avg-num-vehicles"],
)
async def test_composed_analysis_types(
    client: AnalyticsClient,
    transport: tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]],
    analysis_type: str,
) -> None:
    _calls, responses = transport
    responses["metrics/average-speed"] = {"metrics": []}
    responses["metrics/occupancy/fov/histogram"] = {
        "bucketSizeInSec": 5,
        "histogram": [
            {
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:00:05Z",
                "objects": [
                    {"type": "Person", "averageCount": 2},
                    {"type": "Vehicle", "averageCount": 4},
                ],
            }
        ],
    }
    result = await client.analyze(
        source="cam",
        source_type="sensor",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:01:00Z",
        analysis_type=analysis_type,
    )
    assert result["analysis_type"] == analysis_type
    assert isinstance(result["summary"], str)
    if analysis_type == "avg-num-people":
        assert result["result"]["average_count"] == 2
    if analysis_type == "avg-num-vehicles":
        assert result["result"]["average_count"] == 4


@pytest.mark.asyncio
async def test_max_min_analysis_is_deterministic(
    client: AnalyticsClient,
    transport: tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]],
) -> None:
    _calls, responses = transport
    responses["incidents"] = {
        "incidents": [
            {
                "id": "one",
                "timestamp": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T00:00:10Z",
            },
            {
                "id": "two",
                "timestamp": "2026-01-01T00:00:05Z",
                "end": "2026-01-01T00:00:15Z",
            },
        ]
    }
    body = await client.analyze(
        source="cam",
        source_type="sensor",
        start_time="2026-01-01T00:00:00Z",
        end_time="2026-01-01T00:01:00Z",
        analysis_type="max-min-incidents",
    )
    assert body["result"]["maximum_overlap"] == 2
    assert body["result"]["minimum_overlap"] == 1
    assert body["result"]["minimum_overlap_at"] == "2026-01-01T00:00:00+00:00"
    assert body["result"]["valid_incident_count"] == 2


@pytest.mark.asyncio
async def test_max_min_analysis_clips_incidents_to_query_window(
    client: AnalyticsClient,
    transport: tuple[list[tuple[str, dict[str, object] | None]], dict[str, object]],
) -> None:
    _calls, responses = transport
    responses["incidents"] = {
        "incidents": [
            {
                "id": "one",
                "timestamp": "2026-01-01T00:00:00Z",
                "end": "2026-01-01T01:00:00Z",
            },
            {
                "id": "two",
                "timestamp": "2026-01-01T00:10:00Z",
                "end": "2026-01-01T01:00:00Z",
            },
        ]
    }

    body = await client.analyze(
        source="cam",
        source_type="sensor",
        start_time="2026-01-01T00:30:00Z",
        end_time="2026-01-01T00:40:00Z",
        analysis_type="max-min-incidents",
    )

    assert body["result"]["maximum_overlap"] == 2
    assert body["result"]["minimum_overlap"] == 2
    assert body["result"]["maximum_overlap_at"] == "2026-01-01T00:30:00+00:00"
    assert body["result"]["minimum_overlap_at"] == "2026-01-01T00:30:00+00:00"
