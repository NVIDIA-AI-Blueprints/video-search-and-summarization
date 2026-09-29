# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""FastAPI service for source-backed filling measurements and evidence."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from fractions import Fraction
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.gzip import GZipMiddleware

from .segmentation import SegmentationProxy
from .live import LiveProxy, live_router
from .tools import answer_question, create_mcp_server, measurement_rows
from .profiles import (BALANCED_SHA256, BALANCED_VIOS_SHA256, BALANCED_VIOS_20260922_SHA256,
                       profile_for_identity, reviewed_profiles)

BACKEND = Path(__file__).resolve().parent


class MeasurementJSONCompression:
    """Compress large mask/measurement JSON without altering ranged video responses."""
    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=2048, compresslevel=4)

    async def __call__(self, scope, receive, send):
        target = self.compressed if (scope["type"] == "http" and scope.get("path") in {
            "/api/analysis/result", "/api/segmentation/result"}) else self.app
        await target(scope, receive, send)


REVIEWED_SOURCE_SHA256 = "21739924f607755906107affa28842df85a4c468e1a8a3a9f28afcb83f346d61"
REFERENCE_CHAPTERS = [
    ("loading", "Bottle loading", 0, 15.633333, "Human-reviewed loading and transfer sequence."),
    ("filling-first", "Filling · wide view", 15.633333, 42.033333, "Human-reviewed filling shot; bottle identities are local to this view."),
    ("filling-close", "Filling · close view", 42.033333, 61.966667, "Human-reviewed second filling view; tracking starts again after the cut."),
    ("overflow", "Overflow reference", 61.966667, 69.833333, "Human-reviewed reference: visible overflow around 63–69s. This chapter is not an automatic alert."),
    ("capping", "Capping", 69.833333, 88.5, "Human-reviewed cap placement; cap-tool activity is visible around 79–85.5s."),
    ("hose", "Hose spraying", 88.5, 93.5, "Human-reviewed hose spraying on capped bottles. Do not interpret this as a filling spill."),
    ("handling", "Bottle handling", 93.5, 95.6, "Human-reviewed handling sequence."),
    ("labeling", "Labeling", 95.6, 107.6, "Human-reviewed labeling sequence."),
]


@dataclass(frozen=True)
class Settings:
    media_path: Path
    data_dir: Path
    vss_base_url: str | None = None
    vss_public_url: str | None = None
    vss_media_origin: str | None = None
    frontend_dir: Path = BACKEND.parent / "frontend" / "dist"
    calibration_path: Path = BACKEND / "calibration.json"
    vision_path: Path = BACKEND / "vision.py"
    enable_mcp: bool = True
    source_mode: str = "local"
    public_prefix: str = ""
    additional_media_path: Path | None = None
    segmentation_url: str | None = None
    segmentation_contract_path: Path = BACKEND / "segmentation-runtime.json"
    live_url: str | None = None
    live_profiles_path: Path = Path("/config/streams.json")

    def __post_init__(self):
        if self.source_mode not in {"local", "vss"}:
            raise ValueError("APP_SOURCE_MODE must be local or vss")
        if self.public_prefix and not re.fullmatch(r"/[A-Za-z0-9/_-]+", self.public_prefix):
            raise ValueError("APP_PUBLIC_PREFIX must be an absolute URL path")
        for origin in (self.vss_base_url, self.vss_public_url, self.vss_media_origin, self.segmentation_url, self.live_url):
            if not origin:
                continue
            url = urlsplit(origin)
            if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
                raise ValueError("VSS_BASE_URL must be an HTTP(S) origin without embedded credentials")

    @classmethod
    def from_env(cls):
        return cls(
            media_path=Path(os.getenv("APP_MEDIA_PATH", "assets/orangejuice.mp4")),
            data_dir=Path(os.getenv("APP_DATA_DIR", "data")),
            vss_base_url=os.getenv("VSS_BASE_URL", "").strip().rstrip("/") or None,
            vss_public_url=os.getenv("VSS_PUBLIC_URL", "").strip().rstrip("/") or None,
            vss_media_origin=os.getenv("VSS_MEDIA_ORIGIN", "").strip().rstrip("/") or None,
            enable_mcp=os.getenv("APP_ENABLE_MCP", "true").lower() not in {"false", "0", "no"},
            source_mode=os.getenv("APP_SOURCE_MODE", "local"),
            segmentation_url=os.getenv("APP_SEGMENTATION_URL", "").strip().rstrip("/") or None,
            public_prefix=os.getenv("APP_PUBLIC_PREFIX", "").rstrip("/"),
            additional_media_path=Path(os.environ["APP_ADDITIONAL_MEDIA_PATH"]) if os.getenv("APP_ADDITIONAL_MEDIA_PATH") else None,
            live_url=os.getenv("APP_LIVE_URL", "").strip().rstrip("/") or None,
            live_profiles_path=Path(os.getenv("LIVE_STREAMS_CONFIG", "/config/streams.json")),
        )


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("A UTC timestamp is required")
    return parsed


def _probe_media(path: Path) -> dict:
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height,avg_frame_rate:format=duration", "-of", "json", str(path)],
                                capture_output=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        values = {"duration": float(data["format"]["duration"]), "width": int(stream["width"]),
                  "height": int(stream["height"]), "fps": float(Fraction(stream["avg_frame_rate"]))}
        if not all(math.isfinite(x) and x > 0 for x in values.values()):
            raise ValueError("Invalid video metadata")
        return values
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
        raise HTTPException(503, "The recorded source could not be inspected by ffprobe") from exc


def _validate_result(result: dict, source: dict) -> None:
    """Reject corrupt/stale/non-JSON measurement payloads before publishing them."""
    required = {"version", "source_sha256", "algorithm", "sample_fps", "runtime_seconds", "samples", "events", "bottles", "quality"}
    if not isinstance(result, dict) or not required.issubset(result) or result["source_sha256"] != source["sha256"]:
        raise ValueError("Analysis result identity or required fields are invalid")
    if source.get("analysis_kind") == "bottle-cycles" and result.get("analysis_kind") != "bottle-cycles":
        raise ValueError("The selected cycle profile requires a cycle-analysis result")
    expected = source.get("expected_measurement")
    if expected:
        actual = result.get("measurement", {})
        if result.get("algorithm") != expected["algorithm"] or any(actual.get(k) != v for k, v in expected.items()):
            raise ValueError("Analysis is stale or does not use the expected neural measurement engine")
    json.dumps(result, allow_nan=False)
    if not isinstance(result["sample_fps"], (int, float)) or result["sample_fps"] <= 0 or not isinstance(result["runtime_seconds"], (int, float)) or result["runtime_seconds"] < 0:
        raise ValueError("Analysis sampling rate and measured runtime are invalid")
    if not all(isinstance(result[key], list) for key in ("samples", "events", "bottles")) or not isinstance(result["quality"], dict):
        raise ValueError("Analysis collections do not match the result contract")
    previous = -1.0
    for sample in result["samples"]:
        t = sample["t"]
        if not isinstance(t, (float, int)) or not math.isfinite(t) or not 0 <= t <= source["duration"] or t < previous:
            raise ValueError("Analysis samples must have ordered, valid source timestamps")
        previous = t
        for bottle in sample["bottles"]:
            if not isinstance(bottle.get("id"), str):
                raise ValueError("A measured bottle needs a within-shot identity")
            level = bottle.get("level")
            if level is not None and (not isinstance(level, (float, int)) or isinstance(level, bool) or not 0 <= level <= 1):
                raise ValueError("Visible height must be null or normalized to 0..1")
            confidence = bottle.get("confidence")
            if not isinstance(confidence, (float, int)) or not 0 <= confidence <= 1:
                raise ValueError("Measurement confidence must be normalized to 0..1")
            for field in ("box", "measurement_box"):
                if field not in bottle:
                    if field == "box":
                        raise ValueError("Missing bottle box")
                    continue
                box = bottle[field]
                if len(box) != 4 or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in box) or box[0] + box[2] > 1.00001 or box[1] + box[3] > 1.00001:
                    raise ValueError("Overlay boxes must be normalized to the original source frame")
            for field in ("surface", "mask"):
                for point in bottle.get(field, []):
                    if len(point) != 2 or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in point):
                        raise ValueError("Overlay points must be normalized to the original source frame")
    for event in result["events"]:
        if not 0 <= event["t"] <= source["duration"]:
            raise ValueError("Event timestamp is outside the source")
    if result.get("analysis_kind") == "bottle-cycles":
        cycles, summary = result.get("cycles"), result.get("summary")
        if not isinstance(cycles, list) or not isinstance(summary, dict):
            raise ValueError("Cycle analysis requires measured cycles and a summary")
        identifiers, counts = set(), {status: 0 for status in ("normal", "underfill", "overflow", "uncertain")}
        inventory = {bottle["id"] for bottle in result["bottles"]}
        for cycle in cycles:
            identifier, status = cycle.get("id"), cycle.get("status")
            if not isinstance(identifier, str) or identifier in identifiers or identifier not in inventory or status not in counts:
                raise ValueError("Cycle identity or result status is invalid")
            identifiers.add(identifier)
            counts[status] += 1
            for field in ("start_time", "end_time", "fill_start_time", "fill_end_time", "measurement_time", "evidence_start", "evidence_end"):
                value = cycle.get(field)
                if value is None and field in {"fill_start_time", "fill_end_time", "measurement_time"} and status in {"uncertain", "overflow"}:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= source["duration"]:
                    raise ValueError("Cycle timestamps must be within the analyzed recording")
            measured_times = [cycle[field] for field in ("fill_start_time", "fill_end_time", "measurement_time") if cycle.get(field) is not None]
            if not (cycle["start_time"] < cycle["end_time"] and cycle["evidence_start"] < cycle["evidence_end"]
                    and all(cycle["start_time"] <= t <= cycle["end_time"] for t in measured_times)):
                raise ValueError("Cycle timing is unordered")
            if cycle.get("fill_start_time") is not None and cycle.get("fill_end_time") is not None and cycle["fill_start_time"] > cycle["fill_end_time"]:
                raise ValueError("Filling end precedes its observed start")
            overflow_time = cycle.get("overflow_time")
            if overflow_time is not None and (isinstance(overflow_time, bool) or not isinstance(overflow_time, (int, float))
                    or not cycle["start_time"] <= overflow_time <= cycle["end_time"]):
                raise ValueError("Overflow evidence timestamp is outside the cycle")
            for field in ("final_level", "reference_level", "tolerance", "confidence"):
                value = cycle.get(field)
                if field == "final_level" and value is None:
                    if status in {"normal", "underfill"}:
                        raise ValueError("A final-height verdict requires a readable final height")
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    raise ValueError("Cycle levels and confidence must be normalized to 0..1")
        if summary.get("total") != len(cycles) or any(summary.get(status) != count for status, count in counts.items()):
            raise ValueError("Cycle summary does not match the measured cycle records")


class AnalysisStore:
    def __init__(self, settings: Settings, analyzer: Callable | None = None):
        self.settings, self.analyzer = settings, analyzer
        self.lock = threading.RLock()
        self._metadata = None
        self._source_stat = None
        self._result = None
        self._result_key = None
        self._state = {"status": "idle", "progress": 0.0, "cached": False}
        self.selecting = False
        self.selected = None
        if settings.source_mode == "vss":
            try:
                selected = json.loads((settings.data_dir / "vss" / "selected.json").read_text())
                self._validate_selection(selected)
                self.selected = selected
            except (OSError, ValueError, KeyError, TypeError, HTTPException):
                pass

    def _validate_selection(self, selected: dict) -> None:
        proof = selected["identity_proof"]
        verifier_file = BACKEND / "media_identity.py"
        if selected.get("identity_code_sha256") != (_sha(verifier_file) if verifier_file.is_file() else None):
            raise ValueError("Media identity verifier changed; select the registered stream again")
        path = (self.settings.data_dir / "vss" / selected["file"]).resolve()
        if not path.is_relative_to((self.settings.data_dir / "vss").resolve()) or not path.is_file():
            raise ValueError("Selected VIOS download is unavailable")
        UUID(selected["stream_id"])
        _utc(selected["actual_start_time"])
        profile = reviewed_profiles(REVIEWED_SOURCE_SHA256).get(proof.get("reference_sha256"))
        if proof.get("verified") is not True or proof.get("offset_seconds") != 0 or profile is None or proof.get("candidate_sha256") != _sha(path):
            raise ValueError("Selected VIOS media no longer matches its verified calibration identity")
        if selected.get("profile_id", profile.id) != profile.id:
            raise ValueError("Selected profile does not match its verified source identity")

    def media_path(self) -> Path:
        if self.settings.source_mode == "local":
            return self.settings.media_path
        if self.selecting:
            raise HTTPException(409, "VIOS source selection is in progress")
        if self.selected is None:
            raise HTTPException(409, "Select a registered VIOS source before analyzing or playing footage")
        return self.settings.data_dir / "vss" / self.selected["file"]

    def select_begin(self):
        with self.lock:
            if self.selecting or self._state["status"] == "running":
                raise HTTPException(409, "Wait for the current source selection or analysis to finish")
            self.selecting = True

    def select_commit(self, selected: dict):
        self._validate_selection(selected)
        path = self.settings.data_dir / "vss" / "selected.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(selected, allow_nan=False))
        os.replace(temporary, path)
        with self.lock:
            self.selected = selected
            self._metadata = self._source_stat = self._result = self._result_key = None
            self._state = {"status": "idle", "progress": 0.0, "cached": False}

    def source(self) -> dict:
        path = self.media_path()
        if not path.is_file():
            raise HTTPException(503, "Recorded source is unavailable; configure APP_MEDIA_PATH")
        stat = path.stat()
        signature = (str(path), stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with self.lock:
            if signature != self._source_stat:
                metadata = _probe_media(path)
                digest = _sha(path)
                binding = self.selected if self.settings.source_mode == "vss" else None
                if binding and digest != binding["identity_proof"]["candidate_sha256"]:
                    raise HTTPException(409, "Selected VIOS download changed; select and verify it again")
                chapters = []
                profile = profile_for_identity(digest, binding["identity_proof"] if binding else None, REVIEWED_SOURCE_SHA256)
                if profile and profile.id == "original-filling-shots":
                    chapters = [{"id": ident, "label": label, "start": start, "end": min(end, metadata["duration"]),
                                 "kind": "reference", "description": description}
                                for ident, label, start, end, description in REFERENCE_CHAPTERS if start < metadata["duration"]]
                self._metadata = {"id": f"sha256:{digest}", "name": path.name, **metadata, "sha256": digest,
                                  "media_url": self.settings.public_prefix + "/api/media", "mode": "recorded", "chapters": chapters,
                                  "origin": "local-file"}
                if profile:
                    self._metadata.update(profile_id=profile.id, analysis_kind=profile.analysis_kind,
                                          calibration_reference_sha256=profile.source_sha256)
                if binding:
                    self._metadata.update({k: binding[k] for k in ("sensor_id", "stream_id", "recording_start", "recording_end", "actual_start_time", "identity_proof", "materialized_at")})
                    self._metadata.update(file_id=binding.get("file_id"), retrieval=binding.get("retrieval"))
                    self._metadata.update(id=f"vss:{binding['stream_id']}:{digest}", name=binding["name"], origin="vss-vios")
                    self._metadata["media_url"] += "?source_id=" + quote(self._metadata["id"], safe="")
                self._source_stat = signature
            result = dict(self._metadata)
            if self.settings.segmentation_url and result.get("analysis_kind") == "bottle-cycles":
                try:
                    contract = json.loads(self.settings.segmentation_contract_path.read_text())
                    if (contract["engine"] != "rfdetr" or contract["algorithm"] != "rfdetr-mask-cycle-v2"
                            or set(contract["model_hashes"]) != {"bottle", "liquid"}
                            or any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in contract["model_hashes"].values())
                            or not re.fullmatch(r"[0-9a-f]{64}", contract["segmentation_pipeline_sha256"])):
                        raise ValueError("Invalid neural measurement contract")
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    raise HTTPException(503, "Pinned neural measurement contract is unavailable") from exc
                result["expected_measurement"] = contract
            return result

    def context(self) -> tuple[str, dict, dict]:
        source = self.source()
        profile = profile_for_identity(source["sha256"], source.get("identity_proof"), REVIEWED_SOURCE_SHA256)
        calibration_path = BACKEND / profile.calibration_file if profile and profile.analysis_kind == "bottle-cycles" else self.settings.calibration_path
        fingerprint = {"source_sha256": source["sha256"], "vision_sha256": _sha(self.settings.vision_path) if self.settings.vision_path.is_file() else None,
                       "calibration_sha256": _sha(calibration_path) if calibration_path.is_file() else None,
                       "profiles_sha256": _sha(BACKEND / "profiles.py"),
                       "profile_id": profile.id if profile else None,
                       "cycle_vision_sha256": _sha(BACKEND / "cycle_vision.py") if profile and profile.analysis_kind == "bottle-cycles" and (BACKEND / "cycle_vision.py").is_file() else None,
                       "cache_schema": 1}
        if self.settings.source_mode == "vss":
            fingerprint.update(cache_schema=3, vss_binding={k: source[k] for k in ("sensor_id", "stream_id", "file_id", "recording_start", "recording_end", "actual_start_time", "retrieval")},
                               identity_sha256=_sha(BACKEND / "media_identity.py") if (BACKEND / "media_identity.py").is_file() else None)
        if source.get("expected_measurement"):
            fingerprint.update(cache_schema=4, expected_measurement=source["expected_measurement"],
                               neural_vision_sha256=_sha(BACKEND / "neural_vision.py"),
                               neural_calibration_sha256=_sha(BACKEND / "neural_measurement_calibration.json"),
                               segmentation_proxy_sha256=_sha(BACKEND / "segmentation.py"))
        key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        return key, fingerprint, source

    def _cache_path(self, key: str) -> Path:
        return self.settings.data_dir / "analyses" / f"{key}.json"

    def _public_state(self) -> dict:
        result = dict(self._state)
        if self.settings.source_mode == "vss" and self.selected:
            result.update(stream_id=self.selected["stream_id"], sensor_id=self.selected["sensor_id"],
                          source_clock_origin=self.selected["actual_start_time"],
                          source_id=f"vss:{self.selected['stream_id']}:{self.selected['identity_proof']['candidate_sha256']}")
        if self.settings.segmentation_url and (self.settings.source_mode != "vss" or self.selected):
            expected = self.source().get("expected_measurement")
            if expected:
                result["expected_measurement"] = expected
                if self._result and self._state["status"] == "complete":
                    result["measurement"] = self._result.get("measurement")
        return result

    def _load_cache(self, key: str, fingerprint: dict, source: dict) -> bool:
        try:
            cache = json.loads(self._cache_path(key).read_text())
            if not isinstance(cache, dict) or cache.get("cache_key") != key or cache.get("fingerprint") != fingerprint:
                return False
            _validate_result(cache["result"], source)
            if "vss_binding" in fingerprint and any(cache["result"].get("provenance", {}).get(k) != v for k, v in fingerprint["vss_binding"].items()):
                return False
            self._result, self._result_key = cache["result"], key
            self._state = {"status": "complete", "progress": 1.0, "cached": True, "cache_key": key}
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def state(self) -> dict:
        with self.lock:
            if self.selecting:
                return {**self._public_state(), "status": "idle", "progress": 0.0, "cached": False, "selecting": True}
            if self.settings.source_mode == "vss" and self.selected is None:
                return {"status": "idle", "progress": 0.0, "cached": False, "requires_selection": True}
            if self._state["status"] != "running":
                try:
                    key, fingerprint, source = self.context()
                    if self._state["status"] == "error" and self._state.get("cache_key") == key:
                        return self._public_state()
                    if self._result_key != key:
                        self._result, self._result_key = None, None
                        if not self._load_cache(key, fingerprint, source) and self._state["status"] != "error":
                            self._state = {"status": "idle", "progress": 0.0, "cached": False}
                except HTTPException as exc:
                    self._result, self._result_key = None, None
                    self._state = {"status": "error", "progress": 0.0, "error": str(exc.detail), "cached": False}
            return self._public_state()

    def result(self) -> dict:
        if self.selecting:
            raise HTTPException(409, "VIOS source selection is in progress")
        self.state()
        with self.lock:
            if self._state["status"] != "complete" or self._result is None:
                raise HTTPException(409, "A matching completed analysis is not available")
            return self._result

    def start(self, force: bool) -> dict:
        with self.lock:
            if self._state["status"] == "running":
                if force:
                    raise HTTPException(409, "Analysis is already running; wait before forcing another computation")
                return self._public_state()
            key, fingerprint, source = self.context()
            if not force and self._load_cache(key, fingerprint, source):
                return self._public_state()
            self._result, self._result_key = None, None
            self._state = {"status": "running", "progress": 0.0, "cached": False, "cache_key": key,
                           "started_at": datetime.now(timezone.utc).isoformat()}
            threading.Thread(target=self._run, args=(key, fingerprint, source, force), daemon=True).start()
            return self._public_state()

    def _run(self, key: str, fingerprint: dict, source: dict, force: bool = False) -> None:
        def progress(value, *unused):
            if isinstance(value, dict):
                value = value.get("progress", 0)
            try:
                value = float(value)
                if math.isfinite(value):
                    with self.lock:
                        self._state["progress"] = min(0.99, max(0.0, value))
            except (ValueError, TypeError):
                pass
        try:
            if source.get("expected_measurement") and self.analyzer is None:
                proxy = SegmentationProxy(self, self.settings.segmentation_url)
                segmentation = asyncio.run(proxy.analyze(source["id"], source["expected_measurement"], progress,
                                                        force=force))
                module = importlib.import_module("backend.neural_vision")
                result = module.analyze_segmentation(segmentation, str(self.media_path()),
                    progress_callback=lambda value: progress(.92 + .07 * value),
                    approved_reference=source.get("identity_proof"))
                result.setdefault("measurement", {})["segmentation_pipeline_sha256"] = segmentation["provenance"]["pipeline_sha256"]
            else:
                analyzer = self.analyzer
                if analyzer is None:
                    module = importlib.import_module("backend.vision")
                    module = importlib.reload(module)
                    analyzer = module.analyze_video
                kwargs = {"output_path": None, "progress_callback": progress}
                if self.settings.source_mode == "vss":
                    kwargs["approved_reference"] = source["identity_proof"]
                result = analyzer(str(self.media_path()), **kwargs)
            _validate_result(result, source)
            if self.context()[0] != key:
                raise ValueError("Source, calibration or algorithm changed during analysis; recompute")
            result.setdefault("provenance", {}).update({"mode": "analyzed-replay", "cache_key": key, **fingerprint})
            if self.settings.source_mode == "vss":
                result["provenance"].update(origin="vss-vios", **fingerprint["vss_binding"])
            path = self._cache_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps({"cache_key": key, "fingerprint": fingerprint, "result": result}, allow_nan=False)
            with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
                stream.write(encoded)
                temporary = Path(stream.name)
            os.replace(temporary, path)
            with self.lock:
                self._result, self._result_key = result, key
                self._state.update(status="complete", progress=1.0, finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            with self.lock:
                self._state.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:300]}")


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force: bool = False
    source_id: str | None = Field(default=None, min_length=1, max_length=500)


class SegmentationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=1, max_length=500)
    force: bool = False


class QueryRequest(BaseModel):
    source_id: str | None = Field(default=None, min_length=1, max_length=500)
    question: str = Field(min_length=1, max_length=2000)
    reference_level: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    scene_id: str | None = Field(default=None, max_length=200)


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sensor_id: str | None = Field(default=None, min_length=1, max_length=300)
    stream_id: str | None = None
    refresh: bool = False


def create_app(settings: Settings | None = None, analyzer: Callable | None = None,
               vss_transport: httpx.AsyncBaseTransport | None = None,
               identity_verifier: Callable | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = AnalysisStore(settings, analyzer)
    segmentation = SegmentationProxy(store, settings.segmentation_url)
    mcp = None

    @asynccontextmanager
    async def lifespan(_app):
        if mcp:
            async with mcp.session_manager.run():
                yield
        else:
            yield

    app = FastAPI(title="Filling Operations Console", version="1.0.0", lifespan=lifespan)
    app.add_middleware(MeasurementJSONCompression)
    app.state.analysis = store
    app.state.settings = settings
    vss_cache: dict = {}
    vss_lock = asyncio.Lock()

    async def vss_sources() -> dict:
        if not settings.vss_base_url:
            return {"configured": False, "available": False, "base_url": None, "sources": [], "reason": "VSS integration is not configured"}
        async with vss_lock:
            if time.monotonic() - vss_cache.get("at", 0) < 3:
                return dict(vss_cache["value"])
            status = {"configured": True, "available": False, "base_url": settings.vss_public_url or settings.vss_base_url, "sources": [],
                      "identity_mapping": "unlinked", "origin": "vss-vios"}
            try:
                async with httpx.AsyncClient(timeout=10.0, transport=vss_transport, follow_redirects=False) as client:
                    response = await client.get(settings.vss_base_url.rstrip("/") + "/vst/api/v1/sensor/list")
                    response.raise_for_status()
                    payload = response.json()
                    if isinstance(payload, dict):
                        payload = payload.get("sensors", payload.get("data", payload))
                    if not isinstance(payload, list):
                        raise ValueError("Unexpected VIOS source response")
                    rows = []
                    for sensor in payload:
                        if not isinstance(sensor, dict):
                            continue
                        sensor_id = sensor.get("sensorId", sensor.get("sensor_id", sensor.get("id")))
                        if not isinstance(sensor_id, str) or not sensor_id:
                            continue
                        base = {"sensor_id": sensor_id, "sensorId": sensor_id, "name": sensor.get("name", sensor_id),
                                "state": sensor.get("state"), "status": sensor.get("status", sensor.get("state")),
                                "type": sensor.get("type"), "has_timeline": sensor.get("isTimelinePresent")}
                        try:
                            response = await client.get(settings.vss_base_url + f"/vst/api/v1/sensor/{quote(sensor_id, safe='')}/streams")
                            response.raise_for_status()
                            streams = response.json()
                            if isinstance(streams, dict):
                                streams = streams.get("streams", [streams])
                            if not isinstance(streams, list):
                                raise ValueError("Invalid streams")
                            for stream in streams:
                                stream_id = str(UUID(stream["streamId"]))
                                rows.append({**base, "id": stream_id, "stream_id": stream_id, "is_main": stream.get("isMain", False)})
                            if not streams:
                                rows.append({**base, "id": sensor_id, "stream_id": None, "error": "No registered stream"})
                        except (httpx.HTTPError, ValueError, KeyError, TypeError):
                            rows.append({**base, "id": sensor_id, "stream_id": None, "error": "Registered stream metadata unavailable"})
                status.update(available=True, sources=rows, identity_mapping="registered-streams")
            except (httpx.HTTPError, ValueError):
                status["reason"] = "VSS source discovery is unavailable; the baseline may be stopped"
            vss_cache.update(at=time.monotonic(), value=status)
            return dict(status)

    app.include_router(live_router(LiveProxy(
        settings.live_url, settings.live_profiles_path, vss_sources,
        public_origin=settings.vss_public_url or settings.vss_base_url,
        public_prefix=settings.public_prefix,
    )))

    async def select_vss_source(sensor_id: str | None = None, stream_id: str | None = None, refresh: bool = False) -> dict:
        if settings.source_mode != "vss":
            raise HTTPException(409, "VIOS selection requires APP_SOURCE_MODE=vss")
        if not sensor_id and not stream_id:
            raise HTTPException(422, "Select an actual registered sensor_id or stream_id")
        try:
            if stream_id:
                stream_id = str(UUID(stream_id))
        except ValueError as exc:
            raise HTTPException(422, "stream_id must be an actual UUID") from exc
        store.select_begin()
        temporary = None
        try:
            vss_cache.clear()
            registry = await vss_sources()
            if not registry["available"]:
                raise HTTPException(503, registry.get("reason", "VIOS is unavailable"))
            matches = [r for r in registry["sources"] if r.get("stream_id") and
                       (not sensor_id or r["sensor_id"] == sensor_id) and (not stream_id or r["stream_id"] == stream_id)]
            if len(matches) > 1:
                matches = [r for r in matches if r["is_main"]]
            if len(matches) != 1:
                raise HTTPException(422, "The requested registered stream is absent or ambiguous; select its actual stream_id")
            row = matches[0]
            async with httpx.AsyncClient(timeout=180.0, transport=vss_transport, follow_redirects=False) as client:
                response = await client.get(settings.vss_base_url + "/vst/api/v1/storage/timelines")
                response.raise_for_status()
                segments = response.json().get(row["stream_id"], [])
                if not isinstance(segments, list) or not segments:
                    raise HTTPException(422, "Selected stream has no recorded VIOS timeline")
                segments = sorted(segments, key=lambda item: _utc(item["startTime"]))
                for previous, current in zip(segments, segments[1:]):
                    if (_utc(current["startTime"]) - _utc(previous["endTime"])).total_seconds() > 0.1:
                        raise HTTPException(422, "This calibrated replay requires one continuous recording; the selected timeline has gaps")
                recording_start, recording_end = segments[0]["startTime"], segments[-1]["endTime"]
                if _utc(recording_end) <= _utc(recording_start):
                    raise HTTPException(422, "Recorded VIOS interval is empty")
                previous = store.selected
                if not refresh and previous and all(previous.get(k) == v for k, v in {
                    "sensor_id": row["sensor_id"], "stream_id": row["stream_id"], "recording_start": recording_start, "recording_end": recording_end}.items()):
                    store._validate_selection(previous)
                    store.selecting = False
                    return store.source()
                # Uploaded-file sensors have a documented full-file endpoint. Prefer it
                # over clip export, whose muxer can alter SPS/PPS and final-frame timing.
                file_id, retrieval, download_url = None, None, None
                response = await client.get(settings.vss_base_url + f"/vst/api/v1/storage/file/{quote(row['sensor_id'], safe='')}/list")
                if response.status_code == 200:
                    entries = response.json().get(row["sensor_id"], [])
                    if isinstance(entries, list) and len(entries) == 1:
                        file_metadata = entries[0].get("metadata", {})
                        if isinstance(file_metadata, dict) and file_metadata.get("id"):
                            if file_metadata.get("sensorId") != row["sensor_id"]:
                                raise HTTPException(502, "VIOS file metadata belongs to a different registered sensor")
                            file_id = str(UUID(file_metadata["id"]))
                            timestamp = file_metadata.get("timestamp")
                            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp):
                                raise HTTPException(422, "VIOS full-file metadata lacks a valid UTC recording timestamp")
                            file_start = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
                            if abs((file_start - _utc(recording_start)).total_seconds()) > 0.001:
                                raise HTTPException(422, "VIOS file timestamp and recorded timeline disagree; explicit alignment is required")
                            actual_start = recording_start
                            download_url = settings.vss_base_url + "/vst/api/v1/storage/file?id=" + quote(file_id, safe="")
                            retrieval = {"method": "vios-original-file-by-id", "file_id": file_id, "metadata_timestamp_ms": timestamp}
                elif response.status_code not in {404, 405, 501}:
                    response.raise_for_status()
                if download_url is None:
                    response = await client.get(settings.vss_base_url + f"/vst/api/v1/storage/file/{row['stream_id']}/url",
                                                params={"startTime": recording_start, "endTime": recording_end, "container": "mp4",
                                                        "disableAudio": "true", "transcode": "none", "fullLength": "true"})
                    response.raise_for_status()
                    clip = response.json()
                    actual_start = clip["startTime"]
                    _utc(actual_start)
                    if clip.get("streamId", row["stream_id"]) != row["stream_id"]:
                        raise HTTPException(502, "VIOS returned evidence for a different stream")
                    remote_url = urlsplit(clip["videoUrl"])
                    origins = [urlsplit(x) for x in (settings.vss_base_url, settings.vss_public_url, settings.vss_media_origin) if x]
                    if remote_url.scheme not in {"http", "https"} or remote_url.username or remote_url.password or not any(
                            (remote_url.scheme, remote_url.netloc) == (origin.scheme, origin.netloc) for origin in origins):
                        raise HTTPException(502, "VIOS returned an unconfigured media origin; set VSS_MEDIA_ORIGIN to its verified internal origin")
                    # Preserve the returned path/query and use the configured gateway.
                    download_url = settings.vss_base_url + remote_url.path + ("?" + remote_url.query if remote_url.query else "")
                    retrieval = {"method": "vios-recorded-clip-url", "actual_start_time": actual_start}
                directory = settings.data_dir / "vss"
                directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(suffix=".mp4", dir=directory, delete=False) as output:
                    temporary = Path(output.name)
                    async with client.stream("GET", download_url) as response:
                        response.raise_for_status()
                        total = 0
                        async for block in response.aiter_bytes():
                            total += len(block)
                            if total > 2 * 1024**3:
                                raise HTTPException(422, "Recorded source exceeds the 2 GiB calibrated replay limit")
                            output.write(block)
            verifier = identity_verifier
            if verifier is None:
                from .media_identity import verify_media_identity
                verifier = verify_media_identity
            digest = await asyncio.to_thread(_sha, temporary)
            references = [(REVIEWED_SOURCE_SHA256, settings.media_path)]
            if settings.additional_media_path is not None:
                additional_digest = await asyncio.to_thread(_sha, settings.additional_media_path)
                if additional_digest not in {BALANCED_SHA256, BALANCED_VIOS_SHA256, BALANCED_VIOS_20260922_SHA256}:
                    raise HTTPException(503, "Additional calibration reference is not a reviewed source profile")
                references.append((additional_digest, settings.additional_media_path))
            if digest in {BALANCED_SHA256, BALANCED_VIOS_SHA256, BALANCED_VIOS_20260922_SHA256}:
                # Known exact bytes need no container-remux equivalence. The
                # verifier still checks the explicit reviewed expected digest.
                references.insert(0, (digest, temporary))
            # Exact byte matches select first; remuxes still require the existing
            # full-packet or full-decoded-frame identity verifier with zero shift.
            references.sort(key=lambda item: item[0] != digest)
            proof = {"verified": False, "reason": "No reviewed source profile matched the full recording"}
            for expected, reference_path in references:
                try:
                    candidate_proof = await asyncio.to_thread(verifier, temporary, reference_path, expected)
                except ValueError:
                    continue
                if (candidate_proof.get("verified") is True and candidate_proof.get("offset_seconds") == 0
                        and candidate_proof.get("candidate_sha256") == digest and candidate_proof.get("reference_sha256") == expected):
                    proof = candidate_proof
                    break
                proof = {"verified": False, "reason": str(candidate_proof.get("reason", "Identity proof does not match the source"))[:180]}
            if proof.get("verified") is not True or proof.get("offset_seconds") != 0:
                raise HTTPException(422, "VIOS footage does not match the calibrated full recording: " + str(proof.get("reason", "identity verification failed"))[:180])
            metadata = await asyncio.to_thread(_probe_media, temporary)
            profile = reviewed_profiles(REVIEWED_SOURCE_SHA256).get(proof.get("reference_sha256"))
            if digest != proof.get("candidate_sha256") or profile is None:
                raise HTTPException(422, "VIOS identity proof does not match downloaded media")
            if abs((_utc(actual_start) - _utc(recording_start)).total_seconds()) > 0.1:
                raise HTTPException(422, "VIOS returned a shifted recording origin; explicit alignment is required")
            if abs((_utc(recording_end) - _utc(recording_start)).total_seconds() - metadata["duration"]) > 0.15:
                raise HTTPException(422, "VIOS timeline duration does not match the verified full recording")
            destination = directory / (digest + ".mp4")
            os.replace(temporary, destination)
            temporary = None
            selected = {"file": destination.name, "name": row["name"], "sensor_id": row["sensor_id"], "stream_id": row["stream_id"],
                        "file_id": file_id, "retrieval": retrieval,
                        "recording_start": recording_start, "recording_end": recording_end, "actual_start_time": actual_start,
                        "identity_proof": proof, "materialized_at": datetime.now(timezone.utc).isoformat(),
                        "profile_id": profile.id,
                        "identity_code_sha256": _sha(BACKEND / "media_identity.py") if (BACKEND / "media_identity.py").is_file() else None,
                        "duration": metadata["duration"], "origin": "vss-vios"}
            store.select_commit(selected)
        except HTTPException:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(503, "VIOS source materialization failed; inspect registered source/timeline and media availability") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            store.selecting = False
        return store.source()

    async def vss_evidence(stream_id: str, start_time: str, end_time: str) -> dict:
        try:
            parsed_id = UUID(stream_id)
            start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
            if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0) or end <= start:
                raise ValueError("Invalid UTC range")
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(422, "Provide a real stream UUID and ordered ISO 8601 UTC start_time/end_time") from exc
        requested = {"streamId": str(parsed_id), "startTime": start_time, "endTime": end_time}
        output = {"configured": bool(settings.vss_base_url), "available": False, "origin": "vss-vios",
                  "requested": requested, "actual": None, "identity_mapping": "caller-supplied-stream"}
        if not settings.vss_base_url:
            output["reason"] = "VSS integration is not configured"
            return output
        try:
            async with httpx.AsyncClient(timeout=20.0, transport=vss_transport, follow_redirects=False) as client:
                response = await client.get(settings.vss_base_url.rstrip("/") + f"/vst/api/v1/storage/file/{parsed_id}/url",
                                            params={"startTime": start_time, "endTime": end_time, "container": "mp4", "disableAudio": "true"})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("videoUrl"), str) or not isinstance(payload.get("startTime"), str):
                raise ValueError("Unexpected VSS evidence response")
            url = urlsplit(payload["videoUrl"])
            if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
                raise ValueError("VSS returned an unsupported evidence URL")
            fields = {"videoUrl", "startTime", "startTimeEpochMs", "expiryISO", "expiryMinutes", "streamId", "type"}
            output.update(available=True, actual={k: v for k, v in payload.items() if k in fields},
                          note="The upstream URL and actual startTime are unchanged. The actual segment boundary can differ from the requested start.")
            allowed = [urlsplit(value) for value in (settings.vss_base_url, settings.vss_public_url, settings.vss_media_origin) if value]
            if settings.vss_public_url and any((url.scheme, url.netloc) == (item.scheme, item.netloc) for item in allowed):
                output["public_url"] = settings.vss_public_url + url.path + ("?" + url.query if url.query else "")
                output["origin_translation"] = {"from": f"{url.scheme}://{url.netloc}", "to": settings.vss_public_url, "path_and_query_preserved": True}
        except (httpx.HTTPError, ValueError):
            output["reason"] = "VSS evidence is unavailable; check that VSS is running and the supplied stream interval is recorded"
        return output

    async def measurement_evidence(start: float, end: float, source_id: str | None = None) -> dict:
        with store.lock:
            source = store.source()
            if source_id is not None and source["id"] != source_id:
                raise HTTPException(409, "Selected source differs from the requested source_id")
        if source["origin"] != "vss-vios":
            raise HTTPException(409, "Select a registered VIOS source before requesting native measurement evidence")
        if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end <= source["duration"]:
            raise HTTPException(422, "Evidence offsets must satisfy 0 <= start < end <= source duration")
        origin = _utc(source["actual_start_time"])
        output = await vss_evidence(source["stream_id"], (origin + timedelta(seconds=start)).isoformat(),
                                    (origin + timedelta(seconds=end)).isoformat())
        with store.lock:
            if store.source()["id"] != source["id"]:
                raise HTTPException(409, "Selected source changed during evidence retrieval")
        output.update(source_id=source["id"], stream_id=source["stream_id"], source_clock_origin=source["actual_start_time"],
                      source_offsets={"start": start, "end": end}, identity_mapping="verified-selected-vios-stream",
                      local_clip_url=settings.public_prefix + f"/api/evidence?start={start}&end={end}&source_id=" + quote(source["id"], safe=""))
        return output

    @app.get("/api/health")
    async def health():
        vss = await vss_sources()
        return {"status": "ok", "source_mode": settings.source_mode, "vss": vss, "analysis": store.state(), "mcp": {"available": mcp is not None, "path": settings.public_prefix + "/mcp/" if mcp else None}}

    @app.get("/api/source")
    def source():
        return store.source()

    @app.get("/api/analysis")
    def analysis_state():
        return store.state()

    @app.post("/api/analysis")
    def analyze(body: AnalysisRequest = AnalysisRequest()):
        # Hold the same RLock as source selection until the analysis owns the source.
        with store.lock:
            if body.source_id is not None and store.source()["id"] != body.source_id:
                raise HTTPException(409, "Selected source differs from the requested source_id")
            return store.start(body.force)

    @app.get("/api/analysis/result")
    def analysis_result():
        # Results are validated JSON-native data. Avoid FastAPI recursively
        # traversing every per-frame mask coordinate before encoding them.
        return JSONResponse(content=store.result())

    @app.get("/api/segmentation")
    async def segmentation_state(source_id: str | None = None):
        return await segmentation.status(source_id)

    @app.post("/api/segmentation")
    async def segmentation_start(body: SegmentationRequest):
        return await segmentation.start(body.source_id, body.force)

    @app.get("/api/segmentation/result")
    async def segmentation_result(source_id: str | None = None):
        return JSONResponse(content=await segmentation.result(source_id))

    @app.api_route("/api/media", methods=["GET", "HEAD"])
    def media(request: Request, source_id: str | None = None):
        with store.lock:
            metadata, path = store.source(), store.media_path()
            if source_id is not None and source_id != metadata["id"]:
                raise HTTPException(409, "Selected source changed; refresh the source metadata before playback")
            stat = path.stat()
        size, start, end, status = stat.st_size, 0, stat.st_size - 1, 200
        etag = f'"{metadata["sha256"]}"'
        headers = {"Accept-Ranges": "bytes", "ETag": etag, "Cache-Control": "private, max-age=0",
                   "Last-Modified": formatdate(stat.st_mtime, usegmt=True)}
        requested = request.headers.get("range")
        if request.headers.get("if-range") not in {None, etag}:
            requested = None
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
            if not match or not any(match.groups()):
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            a, b = match.groups()
            if a:
                start = int(a)
                end = min(int(b), size - 1) if b else size - 1
            elif int(b) > 0:
                start = max(0, size - int(b))
            else:
                start = size
            if start >= size or start > end:
                return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers, media_type="video/mp4")

        def chunks():
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    block = stream.read(min(1024 * 256, remaining))
                    if not block:
                        break
                    remaining -= len(block)
                    yield block
        return StreamingResponse(chunks(), status_code=status, headers=headers, media_type="video/mp4")

    evidence_lock = threading.Lock()

    def evidence_file(start: float, end: float | None, source_id: str | None = None) -> tuple[Path, dict]:
        with store.lock:
            metadata, input_path = store.source(), store.media_path()
            if source_id is not None and source_id != metadata["id"]:
                raise HTTPException(409, "Selected source changed; this evidence link belongs to a different source")
        if not math.isfinite(start) or not 0 <= start < metadata["duration"]:
            raise HTTPException(422, "Timestamp must be within the original source duration")
        if end is not None and (not math.isfinite(end) or not start < end <= metadata["duration"]):
            raise HTTPException(422, "Evidence range must satisfy 0 <= start < end <= source duration")
        suffix = ".jpg" if end is None else ".mp4"
        identity = hashlib.sha256(f"{metadata['id']}:{metadata.get('actual_start_time')}:{start:.9f}:{end}:ffmpeg-evidence-v1".encode()).hexdigest()
        destination = settings.data_dir / "evidence" / (identity + suffix)
        with evidence_lock:
            if destination.is_file() and destination.stat().st_size:
                return destination, metadata
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(suffix=suffix, dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
            args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "4", "-filter_threads", "1", "-ss", f"{start:.9f}", "-i", str(input_path)]
            if end is None:
                args += ["-frames:v", "1", "-q:v", "2", "-threads", "4"]
            else:
                args += ["-t", f"{end - start:.9f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "4", "-movflags", "+faststart"]
            try:
                subprocess.run([*args, str(temporary)], capture_output=True, timeout=120, check=True)
                if not temporary.stat().st_size:
                    raise ValueError("Empty evidence output")
                os.replace(temporary, destination)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                temporary.unlink(missing_ok=True)
                raise HTTPException(503, "Evidence extraction failed; ffmpeg must be available") from exc
        return destination, metadata

    @app.get("/api/evidence")
    def evidence(start: float = Query(...), end: float = Query(...), source_id: str | None = None):
        path, source = evidence_file(start, end, source_id)
        return FileResponse(path, media_type="video/mp4", headers={"X-Source-Start": str(start), "X-Source-End": str(end),
                            "X-VSS-Stream-ID": source.get("stream_id", ""), "X-Source-Clock-Origin": source.get("actual_start_time", "")})

    @app.get("/api/snapshot")
    def snapshot(t: float = Query(...), source_id: str | None = None):
        path, source = evidence_file(t, None, source_id)
        return FileResponse(path, media_type="image/jpeg", headers={"X-Source-Time": str(t), "X-VSS-Stream-ID": source.get("stream_id", ""),
                            "X-Source-Clock-Origin": source.get("actual_start_time", "")})

    @app.get("/api/export.csv")
    def export_csv():
        result = store.result()
        text = io.StringIO(newline="")
        fields = ["source_sha256", "algorithm", "stream_id", "source_clock_origin", "recorded_at", "t", "scene_id", "bottle_id", "label", "phase", "level", "confidence", "level_kind"]
        writer = csv.DictWriter(text, fieldnames=fields)
        writer.writeheader()
        for row in measurement_rows(result):
            # Spreadsheet cells must stay data, even if future detector labels are user-controlled.
            values = {"source_sha256": result["source_sha256"], "algorithm": result["algorithm"], **row}
            writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for k, v in values.items()})
        return Response(text.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="visible-height-measurements.csv"'})

    @app.post("/api/query")
    def query(body: QueryRequest):
        with store.lock:
            if body.source_id is not None and store.source()["id"] != body.source_id:
                raise HTTPException(409, "Selected source differs from the requested source_id")
            return answer_question(store.result(), body.question, body.reference_level, body.scene_id)

    @app.get("/api/vss/sources")
    async def sources():
        return await vss_sources()

    @app.post("/api/vss/select")
    async def select_source(body: SelectionRequest):
        return await select_vss_source(body.sensor_id, body.stream_id, body.refresh)

    @app.get("/api/measurement-evidence")
    async def selected_evidence(start: float, end: float, source_id: str | None = None):
        return await measurement_evidence(start, end, source_id)

    @app.get("/api/vss/evidence")
    async def upstream_evidence(stream_id: str, start_time: str, end_time: str):
        return await vss_evidence(stream_id, start_time, end_time)

    if settings.enable_mcp:
        mcp = create_mcp_server(store.source, store.result, vss_evidence, vss_sources, select_vss_source,
                                store.start, store.state, measurement_evidence)
    if mcp:
        app.mount("/mcp", mcp.streamable_http_app())

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/") or path == "mcp" or path.startswith("mcp/"):
            raise HTTPException(404, "Endpoint not found")
        base = settings.frontend_dir.resolve()
        asset = (base / path).resolve()
        if not asset.is_relative_to(base):
            raise HTTPException(404, "Asset not found")
        if asset.is_file():
            return FileResponse(asset)
        if path and Path(path).suffix:
            raise HTTPException(404, "Asset not found")
        if (base / "index.html").is_file():
            return FileResponse(base / "index.html")
        return JSONResponse({"service": "Filling Operations Console", "frontend": "not built", "api": "/api/health"}, status_code=503)

    return app


app = create_app()
