# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read-only client for the VSS Video Analytics API."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
import datetime
from itertools import groupby
import json
from typing import TYPE_CHECKING
from typing import Any

import aiohttp

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import LibraryError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_INCIDENT_FIELDS = ("Id", "id", "timestamp", "end", "sensorId")
QueryValue = str | int | float


class AnalyticsError(BackendUnreachableError):
    """The Video Analytics API could not answer a read."""

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__("video_analytics", message, cause)


class AnalyticsNotFoundError(LibraryError):
    """A requested analytics record does not exist."""


class AnalyticsInvalidInputError(LibraryError):
    """The Video Analytics API rejected caller-supplied input."""


class AnalyticsTimeoutError(LibraryError):
    """The Video Analytics API exceeded the bounded request timeout."""


def _error_text(text: str) -> str:
    try:
        payload = json.loads(text)
    except ValueError:
        return text.strip()
    if isinstance(payload, dict):
        return str(payload.get("message") or payload.get("error") or payload)
    return str(payload)


async def _request_json(
    session: aiohttp.ClientSession,
    base_url: str,
    path: str,
    *,
    params: dict[str, QueryValue] | None,
    operation: str,
    timeout_seconds: float,
) -> object:
    """Issue one bounded GET and return parsed JSON."""
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with session.get(url, params=params) as response:
            text = await response.text()
            detail = f"Video Analytics API {operation} returned HTTP {response.status}: {_error_text(text)}"
            if response.status == 404:
                raise AnalyticsNotFoundError(detail)
            if 400 <= response.status < 500:
                raise AnalyticsInvalidInputError(detail)
            if response.status < 200 or response.status >= 300:
                raise AnalyticsError(detail)
            try:
                return json.loads(text)
            except ValueError as exc:
                raise AnalyticsError(f"Video Analytics API {operation} returned malformed JSON") from exc
    except (aiohttp.ServerTimeoutError, TimeoutError) as exc:
        raise AnalyticsTimeoutError(f"Video Analytics API {operation} timed out after {timeout_seconds:g}s") from exc
    except AnalyticsError:
        raise
    except aiohttp.ClientError as exc:
        raise AnalyticsError(f"Video Analytics API {operation} connection failed: {exc}", exc) from exc


def _object(payload: object, operation: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AnalyticsError(f"Video Analytics API {operation} returned {type(payload).__name__}, expected an object")
    return payload


def _list_field(payload: object, field: str, operation: str) -> list[Any]:
    body = _object(payload, operation)
    value = body.get(field, [])
    if not isinstance(value, list):
        raise AnalyticsError(f"Video Analytics API {operation} returned a non-array {field!r}")
    return value


def _incident_fields(incident: dict[str, Any], includes: tuple[str, ...]) -> dict[str, Any]:
    wanted = set(_DEFAULT_INCIDENT_FIELDS)
    wanted.update(includes)
    return {key: value for key, value in incident.items() if key in wanted}


def _sensor_rows(calibration: object) -> list[dict[str, Any]]:
    body = _object(calibration, "calibration read")
    sensors = body.get("sensors", [])
    if not isinstance(sensors, list):
        raise AnalyticsError("Video Analytics API calibration read returned a non-array 'sensors'")
    return [row for row in sensors if isinstance(row, dict)]


def _place_path(sensor: dict[str, Any]) -> str | None:
    places = sensor.get("place", [])
    if not isinstance(places, list) or not places:
        return None
    levels = []
    for entry in places:
        if not isinstance(entry, dict) or entry.get("name") is None or entry.get("value") is None:
            return None
        levels.append(f"{entry['name']}={entry['value']}")
    return "/".join(levels)


def _places_from_sensors(sensors: list[dict[str, Any]]) -> list[str]:
    return sorted({place for sensor in sensors if (place := _place_path(sensor)) is not None})


def _place_matches(sensor_place: str, place: str) -> bool:
    return sensor_place == place or sensor_place.startswith(f"{place}/")


def _merge_histograms(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine per-sensor FOV histograms for a place deterministically."""
    if not results:
        return {"bucketSizeInSec": None, "histogram": []}
    bucket_size = results[0].get("bucketSizeInSec")
    buckets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        histogram = result.get("histogram", [])
        if not isinstance(histogram, list):
            raise AnalyticsError("Video Analytics API FOV histogram returned a non-array 'histogram'")
        for bucket in histogram:
            if not isinstance(bucket, dict):
                continue
            start, end = str(bucket.get("start", "")), str(bucket.get("end", ""))
            objects = bucket.get("objects", [])
            if not isinstance(objects, list):
                continue
            for item in objects:
                if not isinstance(item, dict) or item.get("type") is None:
                    continue
                value = item.get("averageCount", 0)
                if isinstance(value, int | float) and not isinstance(value, bool):
                    buckets[(start, end)][str(item["type"])].append(float(value))
    histogram = []
    for (start, end), objects in sorted(buckets.items()):
        histogram.append(
            {
                "start": start,
                "end": end,
                "objects": [{"type": name, "averageCount": sum(values)} for name, values in sorted(objects.items())],
            }
        )
    return {"bucketSizeInSec": bucket_size, "histogram": histogram}


def _overlap_result(
    incidents: list[dict[str, Any]],
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    window_start = datetime.datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    window_end = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    events: list[tuple[datetime.datetime, int]] = []
    for incident in incidents:
        try:
            start = datetime.datetime.fromisoformat(str(incident["timestamp"]).replace("Z", "+00:00"))
            end = datetime.datetime.fromisoformat(str(incident["end"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        start = max(start, window_start)
        end = min(end, window_end)
        if end < start:
            continue
        events.extend(((start, 1), (end, -1)))
    events.sort(key=lambda event: (event[0], -event[1]))
    grouped_events = [
        (instant, sum(delta for _, delta in group)) for instant, group in groupby(events, key=lambda event: event[0])
    ]
    count = 0
    maximum = 0
    minimum: int | None = None
    maximum_at: datetime.datetime | None = None
    minimum_at: datetime.datetime | None = None
    for index, (instant, delta) in enumerate(grouped_events):
        count += delta
        if index == len(grouped_events) - 1:
            continue
        if count > maximum:
            maximum, maximum_at = count, instant
        if count > 0 and (minimum is None or count < minimum):
            minimum, minimum_at = count, instant
    return {
        "incident_count": len(incidents),
        "valid_incident_count": len(events) // 2,
        "maximum_overlap": maximum,
        "maximum_overlap_at": maximum_at.isoformat() if maximum_at else None,
        "minimum_overlap": minimum if minimum is not None else 0,
        "minimum_overlap_at": minimum_at.isoformat() if minimum_at else None,
    }


class AnalyticsClient:
    """Typed, read-only Video Analytics API operations."""

    def __init__(self, base_url: str, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        self._base_url = base_url
        self._timeout_seconds = timeout_seconds
        self._session: aiohttp.ClientSession | None = None

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[None]:
        if self._session is not None:
            yield
            return
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            self._session = session
            try:
                yield
            finally:
                self._session = None

    async def _get(self, path: str, operation: str, params: dict[str, QueryValue] | None = None) -> object:
        async with self._session_scope():
            assert self._session is not None
            return await _request_json(
                self._session,
                self._base_url,
                path,
                params=params,
                operation=operation,
                timeout_seconds=self._timeout_seconds,
            )

    async def incidents(
        self,
        *,
        source: str | None = None,
        source_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 10,
        includes: tuple[str, ...] = (),
        vlm_verdict: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, QueryValue] = {"maxResultSize": limit}
        if source is not None and source_type is not None:
            params["sensorId" if source_type == "sensor" else "place"] = source
        if start_time is not None and end_time is not None:
            params |= {"fromTimestamp": start_time, "toTimestamp": end_time}
        if vlm_verdict is not None:
            params |= {"vlmVerified": "true", "vlmVerdict": vlm_verdict}
        rows = _list_field(await self._get("incidents", "incident list", params), "incidents", "incident list")
        return [_incident_fields(row, includes) for row in rows if isinstance(row, dict)]

    async def incident(self, incident_id: str, includes: tuple[str, ...] = ()) -> dict[str, Any]:
        escaped = incident_id.replace("\\", "\\\\").replace('"', '\\"')
        common_params: dict[str, QueryValue] = {
            "queryString": f'Id:"{escaped}" OR id:"{escaped}"',
            "maxResultSize": 2,
        }
        async with self._session_scope():
            for params in (common_params, common_params | {"vlmVerified": "true"}):
                rows = _list_field(
                    await self._get("incidents", "incident lookup", params),
                    "incidents",
                    "incident lookup",
                )
                for row in rows:
                    if isinstance(row, dict) and incident_id in (
                        row.get("Id"),
                        row.get("id"),
                    ):
                        return _incident_fields(row, includes)
        raise AnalyticsNotFoundError(f"incident {incident_id!r} was not found")

    async def _calibration_sensors(self) -> list[dict[str, Any]]:
        return _sensor_rows(await self._get("config/calibration", "calibration read"))

    async def sensors(self, place: str | None = None) -> list[str]:
        rows = await self._calibration_sensors()
        sensors = {
            str(row["id"])
            for row in rows
            if row.get("id") is not None
            and (
                place is None
                or ((sensor_place := _place_path(row)) is not None and _place_matches(sensor_place, place))
            )
        }
        return sorted(sensors)

    async def places(self) -> list[str]:
        return _places_from_sensors(await self._calibration_sensors())

    async def fov_histogram(
        self,
        *,
        source: str,
        source_type: str,
        start_time: str,
        end_time: str,
        object_type: str | None = None,
        bucket_count: int = 10,
    ) -> dict[str, Any]:
        try:
            async with asyncio.timeout(self._timeout_seconds), self._session_scope():
                sensor_ids = [source] if source_type == "sensor" else await self.sensors(place=source)
                requests = []
                for sensor_id in sensor_ids:
                    params: dict[str, QueryValue] = {
                        "sensorId": sensor_id,
                        "fromTimestamp": start_time,
                        "toTimestamp": end_time,
                        "bucketCount": bucket_count,
                    }
                    if object_type:
                        params["objectType"] = object_type
                    requests.append(
                        self._get(
                            "metrics/occupancy/fov/histogram",
                            "FOV histogram",
                            params,
                        )
                    )
                payloads = await asyncio.gather(*requests)
        except TimeoutError as exc:
            raise AnalyticsTimeoutError(
                f"Video Analytics API FOV histogram timed out after {self._timeout_seconds:g}s"
            ) from exc
        results = [_object(payload, "FOV histogram") for payload in payloads]
        return _merge_histograms(results)

    async def average_speed(
        self,
        *,
        source: str,
        source_type: str,
        start_time: str,
        end_time: str,
    ) -> dict[str, Any]:
        params: dict[str, QueryValue] = {
            "fromTimestamp": start_time,
            "toTimestamp": end_time,
            "sensorId" if source_type == "sensor" else "place": source,
        }
        return _object(await self._get("metrics/average-speed", "average speed", params), "average speed")

    async def analyze(
        self,
        *,
        source: str,
        source_type: str,
        start_time: str,
        end_time: str,
        analysis_type: str,
    ) -> dict[str, Any]:
        if analysis_type == "max-min-incidents":
            incidents = await self.incidents(
                source=source,
                source_type=source_type,
                start_time=start_time,
                end_time=end_time,
                limit=1001,
                includes=("timestamp", "end"),
            )
            has_more = len(incidents) > 1000
            result = _overlap_result(
                incidents[:1000],
                start_time=start_time,
                end_time=end_time,
            )
            result["has_more"] = has_more
            summary = (
                f"Analyzed {result['valid_incident_count']} incidents; maximum overlap "
                f"{result['maximum_overlap']}, minimum overlap {result['minimum_overlap']}."
            )
        elif analysis_type == "average-speed":
            result = await self.average_speed(
                source=source,
                source_type=source_type,
                start_time=start_time,
                end_time=end_time,
            )
            metrics = result.get("metrics", [])
            summary = (
                "Average speeds: "
                + ", ".join(
                    f"{row.get('direction')}: {row.get('averageSpeed')}" for row in metrics if isinstance(row, dict)
                )
                if isinstance(metrics, list) and metrics
                else "No speed data available."
            )
        else:
            object_type = "Person" if analysis_type == "avg-num-people" else "Vehicle"
            histogram = await self.fov_histogram(
                source=source,
                source_type=source_type,
                start_time=start_time,
                end_time=end_time,
                object_type=object_type,
            )
            counts = [
                float(item["averageCount"])
                for bucket in histogram.get("histogram", [])
                if isinstance(bucket, dict)
                for item in bucket.get("objects", [])
                if isinstance(item, dict)
                and item.get("type") == object_type
                and isinstance(item.get("averageCount"), int | float)
                and not isinstance(item.get("averageCount"), bool)
            ]
            average = sum(counts) / len(counts) if counts else None
            result = {"object_type": object_type, "average_count": average, "bucket_count": len(counts)}
            summary = (
                f"The average number of {object_type.lower()} objects was {average:.2f}."
                if average is not None
                else f"No {object_type.lower()} objects were detected."
            )
        return {"analysis_type": analysis_type, "result": result, "summary": summary}


__all__ = [
    "AnalyticsClient",
    "AnalyticsError",
    "AnalyticsInvalidInputError",
    "AnalyticsNotFoundError",
    "AnalyticsTimeoutError",
]
