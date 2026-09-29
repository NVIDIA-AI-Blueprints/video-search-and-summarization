# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Source/session-bound client for the optional filling inspection extension."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import json
import math
import re
import time
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit
from urllib.parse import urlunsplit
from uuid import UUID

import httpx


class FillingError(Exception):
    """An actionable failure with the public CLI exit-code contract."""

    def __init__(self, message: str, code: int = 3) -> None:
        super().__init__(message)
        self.code = code


def _uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise FillingError("Use an actual stream UUID returned by filling sources.", 2) from exc


def _instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError("missing timezone")
        return result
    except (ValueError, TypeError, AttributeError) as exc:
        raise FillingError("The filling backend returned an invalid source clock.") from exc


class FillingClient:
    """Calls only the route recorded by vss configure; never performs inference locally."""

    def __init__(self, endpoint: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.http = httpx.Client(
            base_url=endpoint.rstrip("/") + "/", timeout=180, follow_redirects=False, transport=transport
        )

    def __enter__(self) -> FillingClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.http.close()

    def request(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            response = self.http.request(method, "api/" + path, **kwargs)
        except httpx.TimeoutException as exc:
            raise FillingError("Filling request timed out; use status before retrying analysis.", 7) from exc
        except httpx.HTTPError as exc:
            raise FillingError("The configured filling endpoint is unreachable.") from exc
        if not response.is_success:
            codes = {400: 2, 404: 5, 409: 4, 422: 2}
            try:
                detail = response.json().get("detail", "")
            except (ValueError, AttributeError):
                detail = ""
            raise FillingError(
                f"Filling HTTP {response.status_code}: {str(detail)[:500] or 'request failed'}",
                codes.get(response.status_code, 3),
            )
        try:
            result = response.json()
        except ValueError as exc:
            raise FillingError("Filling backend returned non-JSON data.") from exc
        if not isinstance(result, dict):
            raise FillingError("Filling backend returned a non-object result.")
        return result

    def sources(self) -> dict:
        registry = self.request("GET", "vss/sources")
        if registry.get("configured") is False:
            raise FillingError("The filling extension has no configured VIOS deployment.", 4)
        if registry.get("available") is not True or not isinstance(registry.get("sources"), list):
            raise FillingError("VIOS source discovery is unavailable; this is not an empty source list.")
        return registry

    def source(self, stream_id: str | None = None) -> dict:
        source = self.request("GET", "source")
        if stream_id is not None and source.get("stream_id") != _uuid(stream_id):
            raise FillingError("The selected source differs from --stream-id; select that registered stream first.", 4)
        if (
            source.get("origin") != "vss-vios"
            or not source.get("sha256")
            or not source.get("id")
            or not source.get("actual_start_time")
        ):
            raise FillingError("Select a verified VIOS recording before using filling measurements.", 4)
        _instant(source["actual_start_time"])
        return source

    def assert_source(self, expected: dict) -> None:
        current = self.source(expected["stream_id"])
        for key in ("id", "sha256", "actual_start_time", "sensor_id", "expected_measurement"):
            if current.get(key) != expected.get(key):
                raise FillingError(
                    "The selected recording changed during this command; no mixed-source answer returned.", 4
                )

    @staticmethod
    def assert_measurement(result: dict, source: dict) -> None:
        """Reject a same-video result from an obsolete algorithm or model pair."""
        expected = source.get("expected_measurement")
        actual = result.get("measurement")
        if expected is None:
            if isinstance(actual, dict) and actual.get("engine") == "rfdetr":
                raise FillingError("The source does not advertise a current neural measurement contract.", 4)
            return  # Older original-shot deployments remain explicitly legacy.
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            raise FillingError("Measurement identity is missing; refresh analysis for the current algorithm.", 4)
        for key in ("engine", "algorithm", "model_hashes", "segmentation_pipeline_sha256"):
            if key in expected and actual.get(key) != expected[key]:
                raise FillingError(
                    f"Measurement {key} differs from the current source contract; stale result rejected.", 4
                )
        if not actual.get("engine") or not actual.get("algorithm"):
            raise FillingError("Measurement engine or algorithm identity is missing.", 4)
        if result.get("algorithm") not in (None, actual["algorithm"]):
            raise FillingError("Measurement metadata disagrees with the result algorithm.", 4)
        if actual["engine"] == "rfdetr":
            hashes = actual.get("model_hashes")
            if not isinstance(hashes, dict):
                raise FillingError("Neural measurement checkpoint identities are missing.", 4)
            for model in ("bottle", "liquid"):
                value = hashes.get(model)
                if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                    raise FillingError("Neural measurement checkpoint identity is invalid.", 4)
                reported = result.get("models", {}).get(model, {}).get("checkpoint_sha256")
                if reported is not None and reported != value:
                    raise FillingError("Measurement checkpoint metadata disagrees with its model identity.", 4)

    def _complete_state(self, state: dict, source: dict) -> dict:
        if state.get("status") != "complete" or source.get("expected_measurement") is None:
            return state
        if state.get("measurement") is not None:
            self.assert_measurement(state, source)
            return state
        # Some backends keep large result metadata out of status. Verify the
        # actual completed cache rather than treating "complete" as provenance.
        result = self.request("GET", "analysis/result")
        self.assert_result(result, source)
        return {**state, "measurement": result["measurement"]}

    @staticmethod
    def assert_result(result: dict, source: dict) -> None:
        provenance = result.get("provenance", {})
        if not isinstance(provenance, dict):
            raise FillingError("Measurement recording provenance is invalid.", 4)
        if (
            result.get("source_sha256") != source["sha256"]
            or provenance.get("stream_id") != source["stream_id"]
            or provenance.get("actual_start_time") != source["actual_start_time"]
        ):
            raise FillingError("Measurements do not match the selected stream, video identity and recording clock.", 4)
        FillingClient.assert_measurement(result, source)

    def select(self, stream_id: str, *, refresh: bool = False) -> dict:
        stream_id = _uuid(stream_id)
        registry = self.sources()
        matches = [row for row in registry.get("sources", []) if row.get("stream_id") == stream_id]
        if len(matches) != 1:
            raise FillingError("Stream UUID is absent or ambiguous in filling sources.", 5)
        selected = self.request("POST", "vss/select", json={"stream_id": stream_id, "refresh": refresh})
        if selected.get("stream_id") != stream_id:
            raise FillingError("Source selection returned a different stream.", 4)
        current = self.source(stream_id)
        if current.get("id") != selected.get("id"):
            raise FillingError("Source changed while selection completed.", 4)
        return current

    def status(self, stream_id: str) -> dict:
        source = self.source(stream_id)
        result = self.request("GET", "analysis")
        if result.get("stream_id") != source["stream_id"] or result.get("source_id") != source["id"]:
            raise FillingError("Analysis status belongs to a different selected source.", 4)
        result = self._complete_state(result, source)
        self.assert_source(source)
        return {**result, "source": source}

    def analyze(self, stream_id: str, *, force: bool = False, timeout: float = 1200) -> dict:
        if not math.isfinite(timeout) or timeout <= 0:
            raise FillingError("Analysis timeout must be positive and finite.", 2)
        source = self.source(stream_id)
        state = self.request("POST", "analysis", json={"force": force, "source_id": source["id"]})
        deadline = time.monotonic() + timeout
        while True:
            if state.get("stream_id") != source["stream_id"] or state.get("source_id") != source["id"]:
                raise FillingError("Analysis changed source; no result returned.", 4)
            if state.get("status") == "complete":
                state = self._complete_state(state, source)
                self.assert_source(source)
                return {**state, "source": source, "mode": "recorded-analysis"}
            if state.get("status") != "running":
                raise FillingError(f"Analysis did not complete: {state.get('error') or state.get('status')}")
            if time.monotonic() >= deadline:
                raise FillingError(
                    "Analysis is still running after the wait deadline. Use filling status; do not start it again.", 7
                )
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
            state = self.request("GET", "analysis")

    @staticmethod
    def measurement_units(result: dict) -> dict:
        if result.get("measurement", {}).get("engine") != "rfdetr":
            return result
        return {
            "measurement_units": {
                "final_level": "fraction of detected visible bottle height",
                "reference_level": "fraction of the same detected visible bottle height",
                "display_percent": "100 * final_level; never final_level / reference_level",
                "null_level": "unreadable or unknown; never normal, zero or full",
            },
            **result,
        }

    def results(self, stream_id: str, *, include_samples: bool = False) -> dict:
        source = self.source(stream_id)
        result = self.request("GET", "analysis/result")
        self.assert_result(result, source)
        self.assert_source(source)
        if not include_samples:
            result = {**result, "sample_count": len(result.get("samples", [])), "samples_omitted": True}
            result.pop("samples", None)
        return {**self.measurement_units(result), "source": source}

    def _evidence(self, source: dict, start: float, end: float) -> dict:
        if not all(math.isfinite(x) for x in (start, end)) or not 0 <= start < end <= source["duration"]:
            raise FillingError("Evidence offsets must satisfy 0 <= start < end <= source duration.", 2)
        self.assert_source(source)
        clip = self.request(
            "GET", "measurement-evidence", params={"start": start, "end": end, "source_id": source["id"]}
        )
        if not clip.get("available"):
            return {"available": False, "reason": clip.get("reason", "VIOS evidence is unavailable")}
        if (
            clip.get("source_id") != source["id"]
            or clip.get("stream_id") != source["stream_id"]
            or clip.get("source_clock_origin") != source["actual_start_time"]
            or clip.get("requested", {}).get("streamId") != source["stream_id"]
            or clip.get("actual", {}).get("streamId", source["stream_id"]) != source["stream_id"]
            or clip.get("source_offsets") != {"start": start, "end": end}
        ):
            raise FillingError("Evidence identity or offsets differ from the requested measured recording.", 4)
        origin = _instant(source["actual_start_time"])
        for key, offset in (("startTime", start), ("endTime", end)):
            actual_offset = (_instant(clip["requested"].get(key)) - origin).total_seconds()
            if abs(actual_offset - offset) > 0.000001:
                raise FillingError("Evidence uses a different source clock or bottle interval.", 4)
        self.assert_source(source)
        return {**clip, "source_sha256": source["sha256"]}

    def evidence(self, stream_id: str, start: float, end: float) -> dict:
        source = self.source(stream_id)
        measurement = None
        if source.get("expected_measurement") is not None:
            result = self.request("GET", "analysis/result")
            self.assert_result(result, source)
            measurement = result["measurement"]
        clip = self._evidence(source, start, end)
        if measurement is not None:
            clip["measurement"] = measurement
        return clip

    def query(
        self,
        stream_id: str,
        question: str,
        *,
        scene_id: str | None = None,
        reference_percent: float | None = None,
        include_evidence: bool = True,
    ) -> dict:
        source = self.source(stream_id)
        payload: dict[str, Any] = {"question": question, "source_id": source["id"]}
        if scene_id is not None:
            payload["scene_id"] = scene_id
        if reference_percent is not None:
            if not math.isfinite(reference_percent) or not 0 <= reference_percent <= 100:
                raise FillingError("Reference percent must be finite and between 0 and 100.", 2)
            payload["reference_level"] = reference_percent / 100
        answer = self.request("POST", "query", json=payload)
        self.assert_result(answer, source)
        self.assert_source(source)
        answer = self.measurement_units(answer)
        answer["source"] = source
        if not include_evidence or not answer.get("supported"):
            return answer
        clips = []
        observations = answer.get("evidence", [])
        # These intervals are produced by the measurement API, never manual fixture labels.
        for observation in observations:
            label = observation.get("label", "Measured observation")
            entry = {"label": label, "bottle_id": observation.get("bottle_id"), "t": observation.get("t")}
            try:
                t = float(observation["t"])
                start = float(observation.get("evidence_start", max(0, t - 1)))
                end = float(observation.get("evidence_end", min(source["duration"], t + 2)))
                if not math.isfinite(t) or not start <= t <= end:
                    raise FillingError("The measured event is outside its evidence window.", 2)
                clip = {**self._evidence(source, start, end), **entry}
            except FillingError as exc:
                if exc.code == 4:
                    raise
                clip = {**entry, "available": False, "reason": str(exc)}
            except (KeyError, ValueError, TypeError) as exc:
                raise FillingError("Measurement API returned invalid evidence metadata.") from exc
            if answer.get("measurement") is not None:
                clip["measurement"] = answer["measurement"]
            observation["video_evidence"] = clip
            clips.append(clip)
        self.assert_source(source)
        by_bottle = {clip["bottle_id"]: clip for clip in clips if clip.get("bottle_id")}
        for cycle in answer.get("cycles", []):
            cycle["video_evidence"] = by_bottle.get(
                cycle["id"], {"available": False, "reason": "No measured evidence for this cycle"}
            )
        answer["video_evidences"] = clips
        answer["video_evidence_source"] = {
            "source_id": source["id"],
            "stream_id": source["stream_id"],
            "source_sha256": source["sha256"],
            "source_clock_origin": source["actual_start_time"],
        }
        if answer.get("measurement") is not None:
            answer["video_evidence_source"]["measurement"] = answer["measurement"]
        return answer

    @staticmethod
    def assert_segmentation(result: dict, source: dict) -> None:
        if result.get("source_id") != source["id"] or result.get("source_sha256") != source["sha256"]:
            raise FillingError("GPU masks belong to a different selected recording.", 4)
        # An unconfigured worker can report unavailable without producing any masks.
        if result.get("status") == "unavailable" and result.get("models") is None:
            return
        if (
            result.get("stream_id") != source["stream_id"]
            or result.get("source_clock_origin") != source["actual_start_time"]
        ):
            raise FillingError("GPU masks use a different stream or recording clock.", 4)
        provenance = result.get("provenance")
        if provenance is not None and (
            not isinstance(provenance, dict)
            or provenance.get("stream_id") != source["stream_id"]
            or provenance.get("source_clock_origin") != source["actual_start_time"]
        ):
            raise FillingError("GPU mask provenance does not match the selected recording clock.", 4)

    def segmentation_status(self, stream_id: str) -> dict:
        source = self.source(stream_id)
        state = self.request("GET", "segmentation", params={"source_id": source["id"]})
        self.assert_segmentation(state, source)
        self.assert_source(source)
        return {**state, "source": source, "mode": "gpu-mask-segmentation"}

    def segmentation_run(self, stream_id: str, *, force: bool = False, timeout: float = 1200) -> dict:
        if not math.isfinite(timeout) or timeout <= 0:
            raise FillingError("Segmentation timeout must be positive and finite.", 2)
        source = self.source(stream_id)
        deadline = time.monotonic() + timeout
        state = self.request("POST", "segmentation", json={"source_id": source["id"], "force": force})
        while True:
            self.assert_segmentation(state, source)
            self.assert_source(source)
            if state.get("status") == "complete":
                return {**state, "source": source, "mode": "gpu-mask-segmentation"}
            if state.get("status") != "running":
                raise FillingError(f"GPU segmentation did not complete: {state.get('error') or state.get('status')}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FillingError(
                    "GPU segmentation is still running. Use filling segmentation status; do not start it again.", 7
                )
            time.sleep(min(0.5, remaining))
            state = self.request("GET", "segmentation", params={"source_id": source["id"]})

    def segmentation_get(self, stream_id: str, *, at: float | None = None, include_samples: bool = False) -> dict:
        if at is not None and (not math.isfinite(at) or at < 0 or include_samples):
            raise FillingError("Use one finite source offset with --at, or --include-samples; not both.", 2)
        source = self.source(stream_id)
        if at is not None and at >= source["duration"]:
            raise FillingError("Segmentation sample offset must be before the recording ends.", 2)
        result = self.request("GET", "segmentation/result", params={"source_id": source["id"]})
        self.assert_segmentation(result, source)
        self.assert_source(source)
        samples = result.get("samples")
        if not isinstance(samples, list):
            raise FillingError("GPU segmentation returned an invalid sample array.")
        for sample in samples:
            if (
                not isinstance(sample, dict)
                or not isinstance(sample.get("t"), (int, float))
                or not math.isfinite(sample["t"])
                or not 0 <= sample["t"] < source["duration"]
                or not isinstance(sample.get("instances"), list)
            ):
                raise FillingError("GPU segmentation returned invalid source-timed mask samples.")
        output = {
            **result,
            "source": source,
            "sample_count": len(samples),
            "mode": "gpu-mask-segmentation",
            "interpretation": (
                "Neural bottle/liquid instance masks over recorded frames. Per-frame instances are not completed "
                "bottle-cycle counts. Pixel area is not fill height or volume. Read filling results/query for "
                "derived heights, cycle verdicts and their current measurement-engine provenance; raw masks "
                "alone do not establish those outcomes."
            ),
        }
        if at is not None:
            if not samples:
                raise FillingError("No GPU mask sample is available for this recording.", 5)
            nearest = min(samples, key=lambda sample: (abs(sample["t"] - at), sample["t"]))
            rate = result.get("sample_fps")
            if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
                raise FillingError("GPU segmentation returned an invalid sampling rate.")
            period = 1 / rate
            if abs(nearest["t"] - at) > period + 0.000001:
                raise FillingError("No GPU mask sample covers the requested source offset.", 5)
            output["samples"] = [nearest]
            output["requested_source_offset"] = at
            output["actual_source_offset"] = nearest["t"]
            output["actual_recorded_at"] = (
                _instant(source["actual_start_time"]) + timedelta(seconds=nearest["t"])
            ).isoformat()
            output["samples_omitted"] = len(samples) - 1
        elif not include_samples:
            output.pop("samples", None)
            output["samples_omitted"] = True
        return output

    @staticmethod
    def _live_id(value: str, *, session: bool = False) -> str:
        try:
            return str(UUID(value))
        except (ValueError, TypeError, AttributeError) as exc:
            kind = "session ID from live start/status" if session else "stream UUID from live sources"
            raise FillingError(f"Use an actual {kind}.", 2) from exc

    @staticmethod
    def _live_measurement(value: Any) -> dict:
        if not isinstance(value, dict) or value.get("engine") != "rfdetr" or not value.get("algorithm"):
            raise FillingError("Live measurement engine/algorithm provenance is missing.", 4)
        hashes = value.get("model_hashes")
        if not isinstance(hashes, dict):
            raise FillingError("Live measurement checkpoint provenance is missing.", 4)
        for digest in (
            hashes.get("bottle"),
            hashes.get("liquid"),
            value.get("pipeline_sha256"),
            value.get("calibration_sha256"),
        ):
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise FillingError("Live checkpoint, pipeline or calibration identity is invalid.", 4)
        for name in ("reference_level", "tolerance"):
            amount = value.get(name)
            if (
                isinstance(amount, bool)
                or not isinstance(amount, (int, float))
                or not math.isfinite(amount)
                or not 0 <= amount <= 1
            ):
                raise FillingError(f"Live {name} is not a finite visible-height fraction.")
        if value.get("overflow_engine") != "calibrated-exterior-color-signal-v1":
            raise FillingError("Live overflow must identify its separately calibrated exterior signal.", 4)
        return value

    @staticmethod
    def _live_clock(value: Any) -> dict:
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("method"), str)
            or not value["method"]
            or type(value.get("verified")) is not bool
        ):
            raise FillingError("Live source clock provenance is missing or invalid.", 4)
        if value["method"] == "receiver-utc-estimate" and value["verified"]:
            raise FillingError("A receiver UTC estimate cannot claim verified camera capture time.", 4)
        return value

    @staticmethod
    def _live_summary(value: Any) -> None:
        if not isinstance(value, dict):
            raise FillingError("Live cycle summary is missing.")
        for key in ("total", "normal", "underfill", "overflow", "uncertain", "incomplete"):
            count = value.get(key)
            if type(count) is not int or count < 0:
                raise FillingError(f"Live summary {key} must be a nonnegative count.")

    @classmethod
    def _live_envelope(cls, value: dict, *, session_id: str | None = None, stream_id: str | None = None) -> dict:
        if value.get("mode") != "live":
            raise FillingError("Recorded or untyped results cannot be returned as live measurements.", 4)
        try:
            actual_session = str(UUID(value.get("session_id")))
            actual_stream = str(UUID(value.get("stream_id")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise FillingError("Live session or source identity is missing.", 4) from exc
        if (session_id is not None and actual_session != session_id) or (
            stream_id is not None and actual_stream != stream_id
        ):
            raise FillingError("Live response belongs to a different session or stream.", 4)
        cls._live_measurement(value.get("measurement"))
        cls._live_clock(value.get("clock"))
        cls._live_summary(value.get("summary"))
        return value

    @staticmethod
    def _same_live_identity(value: dict, expected: dict) -> None:
        for field in ("session_id", "stream_id"):
            if value.get(field) != expected.get(field):
                raise FillingError("Live session/source changed during this command.", 4)
        for field in (
            "engine",
            "algorithm",
            "model_hashes",
            "pipeline_sha256",
            "calibration_sha256",
            "reference_level",
            "tolerance",
            "overflow_engine",
        ):
            if value.get("measurement", {}).get(field) != expected.get("measurement", {}).get(field):
                raise FillingError(f"Live measurement {field} changed during this command.", 4)
        for field in ("method", "verified"):
            if value.get("clock", {}).get(field) != expected.get("clock", {}).get(field):
                raise FillingError("Live clock relation changed during this command.", 4)

    @classmethod
    def _live_epoch_identity(cls, expected: dict, epoch: int) -> dict:
        """Only the status service may authorize a historical model/clock identity."""
        history = expected.get("measurement_history", [])
        if not isinstance(history, list):
            raise FillingError("Live measurement history is invalid.", 4)
        identities = {}
        for item in history:
            if (
                not isinstance(item, dict)
                or type(item.get("epoch")) is not int
                or not 0 <= item["epoch"] <= expected["epoch"]
            ):
                raise FillingError("Live measurement history has an invalid epoch.", 4)
            if item["epoch"] in identities:
                raise FillingError("Live measurement history has duplicate epochs.", 4)
            cls._live_measurement(item.get("measurement"))
            cls._live_clock(item.get("clock"))
            identities[item["epoch"]] = {
                **expected,
                "epoch": item["epoch"],
                "measurement": item["measurement"],
                "clock": item["clock"],
            }
        current = identities.get(expected["epoch"])
        if current and (current["measurement"] != expected["measurement"] or current["clock"] != expected["clock"]):
            raise FillingError("Current live identity conflicts with its declared history.", 4)
        if epoch == expected["epoch"]:
            return expected
        if epoch not in identities:
            raise FillingError("Live event epoch has no authoritative measurement history.", 4)
        return identities[epoch]

    @staticmethod
    def _live_cycle_identity(value: str) -> tuple[str | None, int | None, str]:
        """Cycle labels are track suffixes, never the global event sequence."""
        if not isinstance(value, str):
            raise FillingError("Live cycle identity must be a returned string.", 2)
        short = re.fullmatch(r"cycle-(\d+)", value, re.IGNORECASE)
        if short and int(short[1]) > 0:
            return None, None, f"cycle-{int(short[1])}"
        full = re.fullmatch(r"([0-9a-fA-F-]{36}):epoch-(\d+):cycle-(\d+)", value)
        if full and int(full[2]) >= 1 and int(full[3]) > 0:
            try:
                return str(UUID(full[1])), int(full[2]), f"cycle-{int(full[3])}"
            except ValueError:
                pass
        raise FillingError("Use a returned cycle-N label or complete session:epoch-N:cycle-N track ID.", 2)

    @staticmethod
    def _live_units(value: dict) -> dict:
        return {
            **value,
            "measurement_units": {
                "level": "fraction of detected visible bottle height",
                "final_level": "fraction of detected visible bottle height",
                "reference_level": "fraction of the same detected visible bottle height",
                "display_percent": "100 * level; never level / reference_level",
                "null_level": "unreadable or unknown; never normal, zero or full",
                "clock": "receiver UTC is an estimate, not verified camera capture time",
                "overflow": "separately calibrated exterior-color signal, not a learned spill mask",
                "current": "provisional observation; only finalized cycles increment completed counters",
            },
        }

    def live_sources(self) -> dict:
        result = self.request("GET", "live/sources")
        if result.get("mode") != "live" or not isinstance(result.get("sources"), list):
            raise FillingError("Live source discovery returned an invalid response.")
        seen = set()
        for source in result["sources"]:
            if not isinstance(source, dict) or source.get("approved") is not True or not source.get("profile_id"):
                raise FillingError("Live source is not an approved registered profile.", 4)
            try:
                stream = str(UUID(source.get("stream_id")))
            except (ValueError, TypeError, AttributeError) as exc:
                raise FillingError("Live source discovery returned an invalid stream identity.", 4) from exc
            if stream in seen:
                raise FillingError("Live source discovery returned an ambiguous stream identity.", 4)
            seen.add(stream)
        return result

    def live_status(self, session_id: str | None = None, *, stream_id: str | None = None) -> dict:
        session_id = self._live_id(session_id, session=True) if session_id is not None else None
        stream_id = self._live_id(stream_id) if stream_id is not None else None
        result = self.request("GET", "live/status", params={"session_id": session_id} if session_id is not None else {})
        if result.get("type") != "status" or result.get("mode") != "live":
            raise FillingError("Live status returned an invalid response.")
        state = result.get("status")
        if state == "idle":
            if (
                result.get("session_id") is not None
                or result.get("stream_id") is not None
                or session_id is not None
                or stream_id is not None
            ):
                raise FillingError("Idle status cannot satisfy the requested live session/source.", 4)
            return result
        if state not in {"connecting", "running", "reconnecting", "stopped", "error"}:
            raise FillingError("Live status returned an unknown lifecycle state.")
        self._live_envelope(result, session_id=session_id, stream_id=stream_id)
        if type(result.get("epoch")) is not int or result["epoch"] < 0:
            raise FillingError("Live connection epoch is invalid.", 4)
        self._live_epoch_identity(result, result["epoch"])
        current = result.get("current")
        if not isinstance(current, dict):
            raise FillingError("Live current observation is invalid.")
        level = current.get("level")
        if level is not None and (
            isinstance(level, bool)
            or not isinstance(level, (int, float))
            or not math.isfinite(level)
            or not 0 <= level <= 1
        ):
            raise FillingError("Live visible height is not a valid fraction or unknown value.")
        return self._live_units(result)

    def live_start(self, stream_id: str, *, request_id: str | None = None) -> dict:
        stream_id = self._live_id(stream_id)
        request_id = self._live_id(request_id, session=True) if request_id is not None else None
        registry = self.live_sources()
        if not any(source["stream_id"] == stream_id for source in registry["sources"]):
            raise FillingError("Stream UUID is absent from approved live sources.", 5)
        payload = {"stream_id": stream_id}
        if request_id is not None:
            payload["request_id"] = request_id
        result = self.request("POST", "live/start", json=payload)
        self._live_envelope(result, stream_id=stream_id)
        current = self.live_status(result["session_id"], stream_id=stream_id)
        self._same_live_identity(current, result)
        if current["status"] in {"error", "stopped"}:
            raise FillingError("The requested live session did not start; inspect live status.")
        return current

    def live_stop(self, session_id: str, *, stream_id: str | None = None) -> dict:
        session_id = self._live_id(session_id, session=True)
        expected = self.live_status(session_id, stream_id=stream_id)
        result = self.request("POST", "live/stop", json={"session_id": expected["session_id"]})
        self._live_envelope(result, session_id=expected["session_id"], stream_id=expected["stream_id"])
        self._same_live_identity(result, expected)
        if result.get("status") != "stopped":
            raise FillingError("Live stop did not return the requested session's stopped state.")
        return self._live_units(result)

    @classmethod
    def _live_evidence(cls, value: dict, expected: dict, *, event_ids: set[str]) -> dict:
        entries = value.get("video_evidences", [])
        if not isinstance(entries, list):
            raise FillingError("Live evidence must be a list of actual returned clips.")
        verified = []
        for clip in entries:
            if not isinstance(clip, dict):
                raise FillingError("Live evidence metadata is invalid.")
            for key in ("session_id", "stream_id"):
                if clip.get(key) not in (None, expected[key]):
                    raise FillingError("Live evidence belongs to another session or stream.", 4)
            if clip.get("verified") is not True or expected["clock"]["verified"] is not True:
                continue  # Measurements remain usable; an unverified link does not become evidence.
            if (
                any(clip.get(key) != expected[key] for key in ("session_id", "stream_id"))
                or clip.get("event_id") not in event_ids
            ):
                raise FillingError("Verified live evidence lacks exact event/source/session provenance.", 4)
            url = clip.get("public_url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise FillingError("Verified live evidence has no returned public clip URL.", 4)
            verified.append(clip)
        snapshots = value.get("snapshot_evidences", [])
        if not isinstance(snapshots, list):
            raise FillingError("Live snapshot evidence must be a list.", 4)
        verified_snapshots = []
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                raise FillingError("Live snapshot evidence metadata is invalid.", 4)
            if any(snapshot.get(key) != expected[key] for key in ("session_id", "stream_id")):
                raise FillingError("Live snapshot belongs to another source/session.", 4)
            if snapshot.get("verified") is not True:
                continue
            pts = snapshot.get("source_pts_seconds")
            if (
                snapshot.get("event_id") not in event_ids
                or snapshot.get("verification") != "exact-decoded-frame"
                or type(snapshot.get("frame_id")) is not int
                or snapshot["frame_id"] < 1
                or isinstance(pts, bool)
                or not isinstance(pts, (int, float))
                or not math.isfinite(pts)
                or pts < 0
                or snapshot.get("mime_type") != "image/jpeg"
                or not isinstance(snapshot.get("public_url"), str)
                or not snapshot["public_url"].startswith(("http://", "https://"))
            ):
                raise FillingError("Live snapshot lacks exact decoded frame/event provenance.", 4)
            verified_snapshots.append(snapshot)
        return {
            **value,
            "video_evidences": verified,
            "evidence_status": "verified" if verified else "pending",
            "snapshot_evidences": verified_snapshots,
            "snapshot_evidence_status": "verified" if verified_snapshots else "unavailable",
        }

    def _live_events_checked(self, entries: Any, expected: dict) -> list[dict]:
        if not isinstance(entries, list):
            raise FillingError("Live events/matches must be a list.")
        output = []
        seen = set()
        for event in entries:
            if not isinstance(event, dict) or event.get("mode") != "live":
                raise FillingError("Live event metadata is invalid.")
            if type(event.get("epoch")) is not int or event["epoch"] < 0:
                raise FillingError("Live event connection epoch is invalid.", 4)
            epoch_identity = self._live_epoch_identity(expected, event["epoch"])
            self._same_live_identity(event, epoch_identity)
            if event["epoch"] != expected["epoch"] and (
                event.get("measurement") != epoch_identity["measurement"]
                or event.get("clock") != epoch_identity["clock"]
            ):
                raise FillingError("Historical live event does not match its exact declared epoch identity.", 4)
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id or event_id in seen:
                raise FillingError("Live event identity is absent or duplicated.", 4)
            seen.add(event_id)
            if (
                type(event.get("seq")) is not int
                or event["seq"] < 1
                or type(event.get("epoch")) is not int
                or event["epoch"] < 0
            ):
                raise FillingError("Live event sequence or connection epoch is invalid.", 4)
            if event.get("kind") not in {"overflow.detected", "cycle.finalized", "cycle.incomplete"} or event.get(
                "status"
            ) not in {"normal", "underfill", "overflow", "uncertain", "incomplete"}:
                raise FillingError("Live event kind or verdict is invalid.")
            if ((event["kind"] == "cycle.incomplete") != (event["status"] == "incomplete")) or (
                event["kind"] == "overflow.detected" and event["status"] != "overflow"
            ):
                raise FillingError("Live event kind must preserve provisional, finalized and incomplete semantics.", 4)
            level = event.get("final_level")
            if event["status"] in {"normal", "underfill"} and level is None:
                raise FillingError("An unreadable live height cannot establish normal fill or underfill.", 4)
            if level is not None and (
                isinstance(level, bool)
                or not isinstance(level, (int, float))
                or not math.isfinite(level)
                or not 0 <= level <= 1
            ):
                raise FillingError("Live final visible height is not a valid fraction or unknown value.")
            output.append(self._live_evidence(event, epoch_identity, event_ids={event_id}))
        return output

    def live_events(
        self, session_id: str, *, stream_id: str | None = None, after_seq: int = 0, limit: int = 100
    ) -> dict:
        if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise FillingError("Use a nonnegative event cursor and a limit between 1 and 200.", 2)
        session_id = self._live_id(session_id, session=True)
        expected = self.live_status(session_id, stream_id=stream_id)
        result = self.request(
            "GET", "live/events", params={"session_id": expected["session_id"], "after_seq": after_seq, "limit": limit}
        )
        self._live_envelope(result, session_id=expected["session_id"], stream_id=expected["stream_id"])
        self._same_live_identity(result, expected)
        if (
            expected.get("measurement_history") is not None
            and result.get("measurement_history") != expected["measurement_history"]
        ):
            raise FillingError("Live events changed the authoritative measurement history.", 4)
        events = self._live_events_checked(result.get("events"), expected)
        sequences = [event["seq"] for event in events]
        if len(events) > limit or sequences != sorted(set(sequences)) or any(seq <= after_seq for seq in sequences):
            raise FillingError("Live events violate the requested bounded cursor window.", 4)
        next_seq = result.get("next_seq")
        if type(next_seq) is not int or next_seq != max([after_seq, *sequences]):
            raise FillingError("Live event cursor would skip or replay the returned window.", 4)
        current = self.live_status(expected["session_id"], stream_id=expected["stream_id"])
        self._same_live_identity(current, expected)
        self._live_events_checked(events, current)
        if any(event["epoch"] > current["epoch"] for event in events):
            raise FillingError("Live event claims a future connection epoch.", 4)
        return self._live_units({**result, "events": events})

    def _live_operator_view(self, result: dict) -> dict:
        """Project an already validated answer; do not expose unrelated live state.

        The full engineering result remains the default. Native image artifacts
        carry only returned, same-origin representative decoded-frame URLs.
        """
        answer = result.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise FillingError("Live operator answer is missing its authoritative explanation.", 4)
        fields = ("event_id", "track_id", "epoch", "kind", "status", "final_level", "reference_level")
        matches = [{key: event.get(key) for key in fields} for event in result["matches"]]
        snapshots = []
        artifacts = []
        markdown = [answer]
        origin = urlsplit(str(self.http.base_url))
        evidence_path = origin.path.rstrip("/") + "/api/live/evidence"
        for snapshot in result.get("snapshot_evidences", [])[:3]:
            url = urlsplit(snapshot["public_url"])
            params = parse_qs(url.query)
            if (
                (url.scheme, url.netloc) != (origin.scheme, origin.netloc)
                or url.path != evidence_path
                or url.fragment
                or any(params.get(key) != [str(snapshot[key])] for key in ("session_id", "event_id", "frame_id"))
            ):
                raise FillingError("Operator snapshot URL does not match its configured origin and exact frame.", 4)
            relative_url = urlunsplit(("", "", url.path, url.query, ""))
            selected = {
                key: snapshot[key]
                for key in (
                    "session_id",
                    "stream_id",
                    "event_id",
                    "frame_id",
                    "verified",
                    "verification",
                    "mime_type",
                    "public_url",
                )
            }
            snapshots.append(selected)
            alt = f"Bottle evidence frame {snapshot['frame_id']}"
            envelope = {
                "version": "1.0",
                "kind": "vss.media.image",
                "payload": {"media_url": relative_url, "mime_type": "image/jpeg", "alt": alt},
            }
            artifacts.append("<vss-ui-artifact>" + json.dumps(envelope, separators=(",", ":")) + "</vss-ui-artifact>")
            markdown.append(f"![{alt}](<{snapshot['public_url']}>)")
        output = {
            "mode": "live",
            "view": "operator",
            "render_policy": "authoritative_measurement",
            "session_id": result["session_id"],
            "stream_id": result["stream_id"],
            "query_status": result.get("query_status"),
            "answer": answer,
            "requested_cycle_ids": result.get("requested_cycle_ids", []),
            "selected_track_ids": result.get("selected_track_ids", []),
            "matches": matches,
            "total_matches": result.get("total_matches", len(matches)),
            "truncated": result.get("truncated", False),
            "height_units": "fraction of detected visible bottle height; null means unknown",
            "evidence_status": result["evidence_status"],
            "video_evidences": result["video_evidences"],
            "snapshot_evidences": snapshots,
            "snapshot_evidence_status": "verified" if snapshots else "unavailable",
            "ui_artifacts": artifacts,
            "display_markdown": "\n\n".join(markdown),
        }
        if result.get("query_status") == "ambiguous":
            candidate_fields = ("session_id", "stream_id", "epoch", "track_id", "event_id", "status")
            output["candidates"] = [
                {key: candidate[key] for key in candidate_fields if key in candidate}
                for candidate in result.get("candidates", [])
            ]
        if result.get("query_status") == "in_progress":
            output["epoch"] = result["epoch"]
            output["current"] = {
                key: result["current"].get(key) for key in ("track_id", "phase", "level", "provisional")
            }
        return output

    def live_query(
        self,
        session_id: str,
        question: str | None = None,
        *,
        stream_id: str | None = None,
        cycle_id: str | None = None,
        cycle_ids: list[str] | None = None,
        epoch: int | None = None,
        limit: int = 10,
        operator_view: bool = False,
    ) -> dict:
        session_id = self._live_id(session_id, session=True)
        if epoch is not None and (type(epoch) is not int or epoch < 1):
            raise FillingError("Use a positive connection epoch returned with the requested cycle.", 2)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise FillingError("Use a live query limit between 1 and 50.", 2)
        requested_cycles = set()
        selected_tracks = set()
        if cycle_ids:
            if cycle_id is not None or epoch is not None or len(cycle_ids) > 2:
                raise FillingError("Use one or two selected full track IDs without a separate cycle or epoch.", 2)
            for identifier in cycle_ids:
                selected_session, selected_epoch, selected_short = self._live_cycle_identity(identifier)
                if selected_session != session_id or selected_epoch is None:
                    raise FillingError("Selected cycle must carry this session and its returned epoch.", 4)
                selected_tracks.add(identifier)
                requested_cycles.add(selected_short)
            if len(selected_tracks) != len(cycle_ids):
                raise FillingError("Select distinct bottles.", 2)
        if cycle_id is not None:
            if not isinstance(cycle_id, str):
                raise FillingError("Cycle ID must be a returned track ID or cycle-N label.", 2)
            target_session, target_epoch, short = self._live_cycle_identity(cycle_id)
            if target_session is not None and target_session != session_id:
                raise FillingError("Requested cycle belongs to a different live session.", 4)
            if target_epoch is not None:
                if epoch is not None and target_epoch != epoch:
                    raise FillingError("Requested cycle and connection epoch conflict.", 4)
                epoch = target_epoch
            requested_cycles.add(short)
        if question is None and cycle_id is not None:
            question = "What happened with the requested bottle cycle?"
        if not isinstance(question, str) or not question.strip():
            raise FillingError("Provide a nonempty live measurement question or an explicit cycle ID.", 2)
        if cycle_id is None and not cycle_ids:
            requested_cycles = {
                f"cycle-{int(n)}" for n in re.findall(r"\b(?:cycle|cyce)-(\d+)\b", question, re.IGNORECASE)
            }
        expected = self.live_status(session_id, stream_id=stream_id)
        body = {"session_id": expected["session_id"], "question": question, "limit": limit}
        if cycle_id is not None:
            body["cycle_id"] = cycle_id
        if cycle_ids:
            body["cycle_ids"] = cycle_ids
        if epoch is not None:
            body["epoch"] = epoch
        result = self.request("POST", "live/query", json=body)
        self._live_envelope(result, session_id=expected["session_id"], stream_id=expected["stream_id"])
        self._same_live_identity(result, expected)
        if (
            expected.get("measurement_history") is not None
            and result.get("measurement_history") != expected["measurement_history"]
        ):
            raise FillingError("Live query changed the authoritative measurement history.", 4)
        matches = self._live_events_checked(result.get("matches"), expected)
        if selected_tracks:
            if set(result.get("selected_track_ids", [])) != selected_tracks:
                raise FillingError("Live query changed the selected bottle identities.", 4)
            if any(row.get("track_id") not in selected_tracks for row in matches):
                raise FillingError("Live query returned a bottle outside the explicit selection.", 4)
        query_status = result.get("query_status")
        if query_status is not None or requested_cycles or epoch is not None:
            if query_status not in {"ok", "not_found", "ambiguous", "in_progress", "unsupported"}:
                raise FillingError("Live query did not preserve its typed lookup outcome.", 4)
            returned_ids = result.get("requested_cycle_ids")
            if (
                not isinstance(returned_ids, list)
                or not all(isinstance(item, str) for item in returned_ids)
                or set(returned_ids) != requested_cycles
                or len(returned_ids) != len(set(returned_ids))
            ):
                raise FillingError("Live query resolved a different requested cycle label.", 4)
            if query_status in {"not_found", "ambiguous", "in_progress", "unsupported"} and matches:
                raise FillingError("Unresolved live lookup cannot return unrelated finalized cycles.", 4)
            total = result.get("total_matches")
            truncated = result.get("truncated")
            if type(total) is not int or total < len(matches) or type(truncated) is not bool or len(matches) > limit:
                raise FillingError("Live query result bounds are invalid.", 4)
            candidates = result.get("candidates", [])
            if not isinstance(candidates, list) or (query_status == "ambiguous" and not candidates):
                raise FillingError("Ambiguous live lookup must preserve its candidate identities.", 4)
            for candidate in candidates:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("session_id") != session_id
                    or candidate.get("stream_id") != expected["stream_id"]
                ):
                    raise FillingError("Live cycle candidate belongs to another source/session.", 4)
                candidate_epoch = candidate.get("epoch")
                if type(candidate_epoch) is not int:
                    raise FillingError("Live cycle candidate epoch is invalid.", 4)
                self._live_epoch_identity(expected, candidate_epoch)
            rows = [*matches, *candidates]
            observation = None
            if query_status == "in_progress":
                observation = result.get("current")
                if (
                    not isinstance(observation, dict)
                    or not observation.get("track_id")
                    or observation.get("provisional") is not True
                ):
                    raise FillingError("In-progress live lookup is missing its provisional track.", 4)
                if type(result.get("epoch")) is not int or result["epoch"] != expected["epoch"]:
                    raise FillingError("Current live observation has no matching response epoch.", 4)
                rows.append(observation)
            for row in rows:
                if not requested_cycles and epoch is None:
                    continue
                try:
                    actual_session, actual_epoch, actual_cycle = self._live_cycle_identity(row.get("track_id", ""))
                except FillingError as exc:
                    raise FillingError("Live cycle result has an invalid track identity.", 4) from exc
                if row is observation and actual_session is None:
                    # A current short label is bound by this response's explicit
                    # epoch, never by a historical event or global event seq.
                    actual_session, actual_epoch = session_id, result["epoch"]
                if actual_session != session_id or actual_epoch != row.get("epoch", actual_epoch):
                    raise FillingError("Live cycle result track conflicts with session/epoch metadata.", 4)
                if (requested_cycles and actual_cycle not in requested_cycles) or (
                    epoch is not None and actual_epoch != epoch
                ):
                    raise FillingError("Live query returned a different cycle or epoch than requested.", 4)
        answer = self._live_evidence(result, expected, event_ids={event["event_id"] for event in matches})
        current = self.live_status(expected["session_id"], stream_id=expected["stream_id"])
        self._same_live_identity(current, expected)
        self._live_events_checked(matches, current)
        if any(event["epoch"] > current["epoch"] for event in matches):
            raise FillingError("Live answer claims a future connection epoch.", 4)
        validated = self._live_units({**answer, "matches": matches})
        return self._live_operator_view(validated) if operator_view else validated
