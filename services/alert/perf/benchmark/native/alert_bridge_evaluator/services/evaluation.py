"""
Evaluation service for Alert Bridge VLM verification.

Handles the full evaluation flow:
1. Load ground truth for the video.
2. Submit an on-demand verification request to Alert Bridge
   (`POST /api/v1/verification/ondemand`).
3. Poll Elasticsearch for the VLM result.
4. Compare the verdict with ground truth.

Prompts and VLM parameters are NOT pushed from this module. Alert Bridge
resolves them server-side from its static `alert_type_config.json`
(referenced by `alert_type_config_file` in AB's `config.yaml`). To switch
use-case (warehouse / traffic / safety / …) point AB at a different
`alert_type_config.json` and restart it; the evaluator only needs to send
the right `category` per request.
"""

import json
import logging
import uuid
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

from ..config import Settings, RedisClient
from .alert_bridge_client import (
    AlertBridgeClient,
    AlertBridgeError,
    UnknownCategoryError,
)
from .category_resolver import (
    gt_row_slug,
    slugify_alert_type,
)
from .elasticsearch_client import ElasticsearchClient, ElasticsearchError
from .kafka_synthetic_producer import KafkaSyntheticIncidentProducer
from .latency import LatencyService
from .prometheus_client import PrometheusClient, PrometheusError

logger = logging.getLogger(__name__)

_SYNTHETIC_GT_UNSET = object()


class EvaluationService:
    """
    Service for evaluating VLM verification against ground truth.

    Flow:
    1. Load GT for video to get expected answer (and any explicit media URL).
    2. Submit on-demand verification request to Alert Bridge
       (`POST /api/v1/verification/ondemand`). AB resolves prompts itself
       from `alert_type_config.json` keyed by `category`.
    3. Poll Elasticsearch for VLM result.
    4. Compare verdict with GT expected answer.
    """

    PROMETHEUS_LATENCY_STAGES = [
        "upstream",
        "kafka_lag",
        "worker_queue_wait",
        "vst",
        "video_length",
        "vlm",
        "worker_processing",
        "e2e",
    ]

    PROMETHEUS_RELIABILITY_KEYS = [
        "total_events",
        "total_verified_events",
        "confirmed_events",
        "rejected_events",
        "verification_failed_events",
        "unknown_events",
        "events_after_dedup",
        "dropped_events",
        "skipped_confirmed_events",
        "verification_failures",
    ]

    SYNTHETIC_DEFAULT_OUTPUT_CATEGORY = "Ladder PPE Violation"

    def __init__(
        self,
        settings: Settings,
        alert_bridge_client: AlertBridgeClient,
        elasticsearch_client: ElasticsearchClient,
        redis_client: RedisClient = None,
        prometheus_client: Optional[PrometheusClient] = None,
    ):
        self._settings = settings
        self._ab_client = alert_bridge_client
        self._es_client = elasticsearch_client
        # Reserved: kept on the instance so the router can pass a Redis
        # client without breaking the constructor signature, and to make it
        # easy to add runtime prompt overrides via REST in the future.
        # NOT used on the hot path anymore — prompts come from AB's static
        # alert_type_config.json (see module docstring).
        self._redis_client = redis_client
        self._prom_client = prometheus_client
        self._kafka_synthetic_producer = KafkaSyntheticIncidentProducer(
            bootstrap_servers=getattr(settings, "kafka_bootstrap_servers", ""),
            incident_topic=getattr(settings, "kafka_incident_topic", "mdx-incidents"),
            flush_timeout=float(getattr(settings, "kafka_produce_flush_timeout", 10.0) or 10.0),
        )
        self._ground_truth: Optional[Dict[str, Any]] = None
        self._gt_entries: Optional[List[Dict[str, Any]]] = None
        # Events-based GT for eval v2: { "version", "events": [...] } or { "sensors": { "id": { "events": [...] } } }
        self._gt_events_data: Optional[Dict[str, Any]] = None

        self._latency_service: Optional[LatencyService] = None
        if settings.eval_mode == "latency" and settings.data_source == "elasticsearch":
            self._latency_service = LatencyService(
                elasticsearch_client,
                default_duration=settings.latency_duration,
            )
            logger.info(
                "Eval mode: latency (duration=%s), data_source=%s - v2 endpoints will produce latency reports",
                settings.latency_duration,
                settings.data_source,
            )
        else:
            logger.info(
                "Eval mode: %s, data_source=%s",
                settings.eval_mode,
                settings.data_source,
            )

    def load_ground_truth_events(self, force_reload: bool = False) -> Dict[str, Any]:
        """
        Load ground truth in events format for eval v2.
        Supports:
        - { "version": 1.0, "events": [ { "start_time": 92, "end_time": 115, ... } ] }
        - { "version": 1.0, "sensors": { "sensor_id": { "events": [ ... ] } } }  (per-sensor)
        Path from settings.gt_file_path (env: GT_FILE_PATH).
        """
        if self._gt_events_data is not None and not force_reload:
            return self._gt_events_data
        try:
            with open(self._settings.gt_file_path, 'r') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.error("Events GT must be a JSON object")
                self._gt_events_data = {}
                return self._gt_events_data
            if 'events' in data:
                self._gt_events_data = data
                logger.info(f"✓ Loaded events GT v{data.get('version', '?')} with {len(data.get('events', []))} events")
            elif 'sensors' in data:
                self._gt_events_data = data
                total = sum(len(s.get('events', [])) for s in data.get('sensors', {}).values())
                logger.info(f"✓ Loaded events GT v{data.get('version', '?')} with {len(data['sensors'])} sensors, {total} total events")
            else:
                logger.error("Events GT must have 'events' or 'sensors' key")
                self._gt_events_data = {}
            return self._gt_events_data
        except OSError as e:
            logger.warning(
                "Ground truth is unavailable at %s (%s); continuing without ground truth",
                self._settings.gt_file_path,
                e,
            )
            self._gt_events_data = {}
            return self._gt_events_data
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing ground truth JSON: {e}")
            self._gt_events_data = {}
            return self._gt_events_data

    def get_gt_events_for_sensor(self, sensor_id: str) -> List[Dict[str, Any]]:
        """Return list of events for the given sensor from loaded events GT."""
        data = self.load_ground_truth_events()
        if not data:
            return []
        if 'sensors' in data:
            sensor_data = data.get('sensors', {}).get(sensor_id)
            if sensor_data is None:
                return []
            return sensor_data.get('events', [])
        return data.get('events', [])

    def filter_evaluable_v2_incidents(
        self,
        incidents: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Return the subset of v2 batch incidents that should appear in accuracy results.

        For event-count accuracy, sensors with no matching GT events are intentionally
        skipped so they do not appear as failed rows in API results or CSV reports.
        Latency and synthetic/BCQ flows keep their existing behavior because they do
        not use events-per-sensor GT as the source of truth.
        """
        if self._settings.eval_mode == "latency":
            return incidents

        gt_raw = self.get_ground_truth_raw()
        if 'items' in gt_raw or 'bcq' in gt_raw:
            return incidents

        evaluable: List[Dict[str, Any]] = []
        skipped: List[str] = []
        for incident in incidents:
            sensor_id = str(incident.get("video") or "").strip()
            if sensor_id and self.get_gt_events_for_sensor(sensor_id):
                evaluable.append(incident)
            else:
                skipped.append(sensor_id or "<missing video>")

        if skipped:
            logger.info(
                "Skipping %d v2 accuracy incident(s) without GT events: %s",
                len(skipped),
                ", ".join(skipped),
            )

        return evaluable

    @staticmethod
    def _stamp_row_indices(entries: Optional[List[Dict[str, Any]]]) -> None:
        """Mutate GT entries in place to attach a stable ``_eval_row_idx``.

        :func:`category_resolver.gt_row_slug` reads this index when an entry
        has no explicit ``id`` / ``video_id`` so the seed pass and the submit
        pass produce the *same* slug for the *same* row. Without this stamp,
        duplicate-``video`` rows in the legacy ``gt.json`` collapse to the
        same slug and AB ends up serving the last-seeded prompt for every
        row that shares the video — silently breaking accuracy comparisons.

        Idempotent: ``setdefault`` keeps any existing ``_eval_row_idx`` so a
        force-reload after re-ordering doesn't shift slugs underneath an
        in-flight eval batch.
        """
        if not entries:
            return
        for idx, entry in enumerate(entries):
            if isinstance(entry, dict):
                entry.setdefault("_eval_row_idx", idx)

    def load_ground_truth(self, force_reload: bool = False) -> List[Dict[str, Any]]:
        """
        Load ground truth data from file.

        Supports three formats:
        1. DataFrame (eval v1): { "version": 1.0, "dataframe": [...] }
        2. Events (eval v2): { "version": 1.0, "events": [...] } or { "sensors": { "id": { "events": [...] } } }
           e.g. warehouse_dataset/events.json with start_time, end_time in seconds.
        3. Legacy: [ {...}, {...} ]
        """
        if force_reload:
            self._gt_events_data = None
        if self._gt_entries is not None and not force_reload:
            return self._gt_entries

        try:
            with open(self._settings.gt_file_path, 'r') as f:
                data = json.load(f)

            if isinstance(data, dict) and 'dataframe' in data:
                self._ground_truth = data
                self._gt_entries = data.get('dataframe', [])
                self._stamp_row_indices(self._gt_entries)
                version = data.get('version', 'unknown')
                logger.info(f"✓ Loaded GT v{version} with {len(self._gt_entries)} entries")
            elif isinstance(data, dict) and ('events' in data or 'sensors' in data):
                # Events format (eval v2): e.g. events.json with events[].start_time, end_time
                self._ground_truth = data
                self._gt_events_data = data
                self._gt_entries = []
                if 'events' in data:
                    n = len(data.get('events', []))
                    logger.info(f"✓ Loaded events GT v{data.get('version', '?')} with {n} events (eval v2)")
                else:
                    total = sum(len(s.get('events', [])) for s in data.get('sensors', {}).values())
                    logger.info(f"✓ Loaded events GT v{data.get('version', '?')} with {len(data['sensors'])} sensors, {total} events (eval v2)")
            elif isinstance(data, dict) and ('items' in data or 'bcq' in data):
                # BCQ / annotation format for synthetic eval
                self._ground_truth = data
                self._gt_entries = []
                # Stamp synthetic-flow entries so `get_synthetic_gt_entry` returns
                # rows that already carry the row index.
                synthetic_items = data.get('items') if 'items' in data else data.get('bcq')
                self._stamp_row_indices(synthetic_items)
                fmt = 'BCQ' if 'items' in data else 'annotation'
                n = len(data.get('items', data.get('bcq', [])))
                logger.info(f"✓ Loaded {fmt} GT with {n} entries (synthetic eval)")
            elif isinstance(data, list):
                self._ground_truth = {'version': 'legacy', 'dataframe': data}
                self._gt_entries = data
                self._stamp_row_indices(self._gt_entries)
                logger.info(f"✓ Loaded {len(self._gt_entries)} GT entries (legacy format)")
            else:
                logger.error("Invalid GT format: expected 'dataframe', 'events', 'sensors', 'items', 'bcq', or a list")
                self._gt_entries = []

            return self._gt_entries

        except OSError as e:
            logger.warning(
                "Ground truth is unavailable at %s (%s); continuing without ground truth",
                self._settings.gt_file_path,
                e,
            )
            self._ground_truth = {'version': None, 'dataframe': []}
            self._gt_entries = []
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing ground truth JSON: {e}")
            return []

    def get_ground_truth_raw(self) -> Dict[str, Any]:
        """Get raw ground truth data including version info."""
        self.load_ground_truth()
        return self._ground_truth or {'version': None, 'dataframe': []}

    def save_ground_truth(self, gt_data: Dict[str, Any]) -> bool:
        """Save new ground truth data to file."""
        try:
            if 'dataframe' not in gt_data:
                if isinstance(gt_data, list):
                    gt_data = {'version': 1.0, 'dataframe': gt_data}
                else:
                    raise ValueError("Invalid GT format")

            if os.path.exists(self._settings.gt_file_path):
                backup_path = f"{self._settings.gt_file_path}.backup"
                os.rename(self._settings.gt_file_path, backup_path)
                logger.info(f"Created backup: {backup_path}")

            with open(self._settings.gt_file_path, 'w') as f:
                json.dump(gt_data, f, indent=2)

            self._gt_entries = None
            self.load_ground_truth(force_reload=True)

            logger.info(f"✓ Saved new GT with {len(self._gt_entries)} entries")
            return True

        except Exception as e:
            logger.error(f"Failed to save GT: {e}")
            return False

    def get_gt_entry(
        self,
        video: str,
        category: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get ground truth entry for a video.

        Resolution order:

        1. If ``category`` is provided, prefer the row whose
           :func:`category_resolver.gt_row_slug` matches it. This keeps the
           submit pass and the seed pass talking about the *same* row even
           when several GT entries share a ``video`` (the GT file legitimately
           has duplicate-video rows with different questions/answers, and the
           first-by-video lookup used to silently misroute them).
        2. Fall back to the first GT entry matching ``video`` — the legacy
           single-video-per-row behaviour.

        Args:
            video: Video identifier (matches 'video' field in GT).
            category: AB ``alert_type`` slug the caller already chose
                (typically the same slug AB was seeded with). Optional.

        Returns:
            GT entry if found, None otherwise.
        """
        gt = self.load_ground_truth()

        if category and isinstance(category, str) and category.strip():
            target = category.strip()
            for item in gt:
                if not isinstance(item, dict):
                    continue
                row_idx = item.get("_eval_row_idx", 0)
                if gt_row_slug(item, row_idx) == target:
                    return item

        entry = next(
            (item for item in gt if isinstance(item, dict) and item.get('video') == video),
            None
        )

        return entry

    def get_gt_v2_entry(self, video: str) -> Optional[Dict[str, Any]]:
        """
        Get ground truth v2 entry for a video (must have duration and max_delay).
        Uses same GT file; expects dataframe entries to include duration, max_delay for v2.
        """
        entry = self.get_gt_entry(video)
        if not entry:
            return None
        if 'duration' not in entry or 'max_delay' not in entry:
            logger.warning(f"GT entry for video={video} missing duration/max_delay (not GT v2)")
            return None
        return entry

    def get_gt_for_video(
        self,
        video: str,
        category: Optional[str] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Get ground truth entry for a video.

        Args:
            video: Video identifier.
            category: AB ``alert_type`` slug. When set, lookup prefers the GT
                row whose per-row slug matches, which is the only way to pick
                the correct row out of duplicate-video entries.

        Returns:
            Tuple of (found, gt_entry).
        """
        gt_entry = self.get_gt_entry(video, category=category)

        if not gt_entry:
            logger.warning(
                f"No ground truth found for video={video} category={category}"
            )
            return False, None

        logger.info(
            f"✓ Found GT for video={video} category={category}, answer={gt_entry.get('answer')}"
        )
        return True, gt_entry

    @staticmethod
    def _parse_iso_timestamp(value: str) -> datetime:
        cleaned = str(value).strip()
        if cleaned.endswith("Z"):
            cleaned = f"{cleaned[:-1]}+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_iso_timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _submission_uses_vst_stream_id(submission_mode: str) -> bool:
        return submission_mode in {"message_bus", "incident_api"}

    @staticmethod
    def _runtime_stream_identity(
        requested_sensor_id: str,
        *,
        vst_stream_id: Optional[str] = None,
        vst_stream_name: Optional[str] = None,
        use_vst_stream_id: bool = False,
    ) -> Dict[str, str]:
        requested = str(requested_sensor_id or "").strip()
        stream_id = str(vst_stream_id or "").strip()
        stream_name = str(vst_stream_name or "").strip()
        submitted = stream_id if use_vst_stream_id and stream_id else requested
        return {
            "requested_sensor_id": requested,
            "submitted_sensor_id": submitted,
            "vst_stream_id": stream_id,
            "vst_stream_name": stream_name,
            "stream_identity_mode": "vst_stream_id" if submitted and submitted == stream_id else "provided_sensor_id",
        }

    @classmethod
    def _normalize_runtime_stream_identities(
        cls,
        runtime_streams: Optional[List[Dict[str, Any]]],
        *,
        use_vst_stream_id: bool,
    ) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        for stream in runtime_streams or []:
            if not isinstance(stream, dict):
                continue
            requested = (
                stream.get("requested_sensor_id")
                or stream.get("sensor_id")
                or stream.get("name")
                or stream.get("vst_stream_name")
                or stream.get("submitted_sensor_id")
                or stream.get("stream_id")
                or stream.get("vst_stream_id")
            )
            identity = cls._runtime_stream_identity(
                str(requested or ""),
                vst_stream_id=(
                    stream.get("vst_stream_id")
                    or stream.get("stream_id")
                    or stream.get("streamId")
                ),
                vst_stream_name=(
                    stream.get("vst_stream_name")
                    or stream.get("name")
                    or stream.get("stream_name")
                ),
                use_vst_stream_id=use_vst_stream_id,
            )
            if identity["requested_sensor_id"]:
                normalized.append(identity)
        return normalized

    @classmethod
    def _runtime_streams_from_readiness(
        cls,
        sensor_ids: List[str],
        vst_readiness: Optional[Dict[str, Any]],
        *,
        use_vst_stream_id: bool,
        fallback_streams: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, str]]:
        fallback_by_requested = {
            str(stream.get("requested_sensor_id") or "").strip(): stream
            for stream in fallback_streams or []
            if str(stream.get("requested_sensor_id") or "").strip()
        }
        resolved_by_requested = {
            str(stream.get("requested_sensor_id") or "").strip(): stream
            for stream in (vst_readiness or {}).get("resolved_streams") or []
            if str(stream.get("requested_sensor_id") or "").strip()
        }

        runtime_streams: List[Dict[str, str]] = []
        for sensor_id in sensor_ids:
            requested = str(sensor_id or "").strip()
            if not requested:
                continue
            source = resolved_by_requested.get(requested) or fallback_by_requested.get(requested) or {}
            runtime_streams.append(
                cls._runtime_stream_identity(
                    requested,
                    vst_stream_id=source.get("vst_stream_id"),
                    vst_stream_name=source.get("vst_stream_name"),
                    use_vst_stream_id=use_vst_stream_id,
                )
            )
        return runtime_streams

    @staticmethod
    def _stream_identity_mode(runtime_streams: List[Dict[str, str]]) -> str:
        modes = {
            str(stream.get("stream_identity_mode") or "provided_sensor_id")
            for stream in runtime_streams
        }
        if not modes:
            return "provided_sensor_id"
        return modes.pop() if len(modes) == 1 else "mixed"

    def _resolve_runtime_streams_for_submission(
        self,
        sensor_ids: List[str],
        submission_mode: str,
        *,
        runtime_streams: Optional[List[Dict[str, Any]]] = None,
        vst_readiness: Optional[Dict[str, Any]] = None,
        wait_for_vst_readiness: bool = False,
    ) -> Tuple[List[Dict[str, str]], Optional[Dict[str, Any]]]:
        use_vst_stream_id = self._submission_uses_vst_stream_id(submission_mode)
        provided_streams = self._normalize_runtime_stream_identities(
            runtime_streams,
            use_vst_stream_id=use_vst_stream_id,
        )
        requested_ids = [
            stream["requested_sensor_id"] for stream in provided_streams
        ] or [str(sensor).strip() for sensor in sensor_ids or [] if str(sensor).strip()]

        if not requested_ids:
            return [], vst_readiness

        should_query_vst = (
            bool(str(getattr(self._settings, "vst_base_url", "") or "").strip())
            and (use_vst_stream_id or wait_for_vst_readiness)
        )
        if vst_readiness is None and should_query_vst:
            vst_readiness = self.wait_for_vst_sensor_readiness(requested_ids)

        if vst_readiness is not None:
            runtime_streams_resolved = self._runtime_streams_from_readiness(
                requested_ids,
                vst_readiness,
                use_vst_stream_id=use_vst_stream_id,
                fallback_streams=provided_streams,
            )
        elif provided_streams:
            runtime_streams_resolved = provided_streams
        else:
            runtime_streams_resolved = [
                self._runtime_stream_identity(sensor, use_vst_stream_id=False)
                for sensor in requested_ids
            ]

        return runtime_streams_resolved, vst_readiness

    def _resolve_vst_storage_media_url(
        self,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: Optional[str] = None,
    ) -> Optional[str]:
        """Resolve a playable MP4 URL from VST storage for a runtime stream.

        Helm synthetic latency uses runtime sensor IDs, not source dataset file
        names. VST storage can turn a stream ID plus time range into a
        temporary ``/vst/storage/temp_files/...mp4`` URL that Alert Bridge/VLM
        can fetch. If VST URL generation is not configured or fails, callers
        fall back to the legacy MEDIA_BASE_URL path.
        """
        vst_base_url = str(getattr(self._settings, "vst_base_url", "") or "").rstrip("/")
        if not vst_base_url:
            return None

        try:
            start_dt = self._parse_iso_timestamp(start_timestamp)
            if end_timestamp:
                end_dt = self._parse_iso_timestamp(end_timestamp)
            else:
                window_seconds = int(getattr(self._settings, "vst_media_window_seconds", 30) or 30)
                end_dt = start_dt + timedelta(seconds=max(1, window_seconds))
        except Exception as exc:  # noqa: BLE001 - preserve legacy fallback path
            logger.warning("[vst-media] Invalid timestamp for sensor_id=%s: %s", sensor_id, exc)
            return None

        timeout = int(getattr(self._settings, "vst_media_timeout", 30) or 30)
        stream_id = self._resolve_vst_stream_id(vst_base_url, sensor_id, timeout)
        if not stream_id:
            logger.warning("[vst-media] Could not resolve VST streamId for sensor_id=%s", sensor_id)
            return None

        params = {
            "startTime": self._format_iso_timestamp(start_dt),
            "endTime": self._format_iso_timestamp(end_dt),
            "expiryMinutes": str(int(getattr(self._settings, "vst_media_expiry_minutes", 60) or 60)),
            "container": "mp4",
            "disableAudio": str(bool(getattr(self._settings, "vst_media_disable_audio", False))).lower(),
        }
        encoded_stream_id = urllib.parse.quote(str(stream_id), safe="")
        query = urllib.parse.urlencode(params)
        url = f"{vst_base_url}/api/v1/storage/file/{encoded_stream_id}/url?{query}"

        try:
            payload = self._fetch_vst_url_json(url, timeout)
        except RuntimeError as exc:  # fall back to MEDIA_BASE_URL
            logger.warning(
                "[vst-media] Failed to resolve VST media URL for sensor_id=%s: %s",
                sensor_id,
                exc,
            )
            return None

        video_url = str(payload.get("videoUrl") or "").strip()
        if not video_url:
            logger.warning("[vst-media] VST response did not include videoUrl for sensor_id=%s", sensor_id)
            return None
        resolved_url = self._rewrite_vst_media_url_to_base(video_url, vst_base_url)
        logger.info("[vst-media] Resolved VST media URL for sensor_id=%s", sensor_id)
        return resolved_url

    def _resolve_vst_stream_id(
        self,
        vst_base_url: str,
        sensor_id: str,
        timeout: int,
    ) -> Optional[str]:
        """Resolve a VST runtime sensor name to the UUID streamId used by storage."""
        sensor_id = str(sensor_id).strip()
        if not sensor_id:
            return None

        try:
            payload = self._fetch_vst_json(vst_base_url, "/api/v1/sensor/streams", timeout)
        except RuntimeError as exc:  # caller handles VST fallback policy
            logger.warning(
                "[vst-media] Failed to fetch VST streams for sensor_id=%s: %s",
                sensor_id,
                exc,
            )
            return None

        stream_entries = list(self._iter_vst_stream_entries(payload))
        if not stream_entries:
            logger.warning("[vst-media] Unexpected or empty VST streams response type: %s", type(payload).__name__)
            return None

        for stream_key, entry in stream_entries:
            stream_id = str(entry.get("streamId") or entry.get("id") or stream_key or "").strip()
            name = str(entry.get("name") or "").strip()
            if sensor_id in {name, stream_id}:
                return stream_id or sensor_id

        logger.warning("[vst-media] No VST stream matched sensor_id=%s", sensor_id)
        return None

    def _iter_vst_stream_entries(self, payload: Any):
        """Yield ``(stream_key, stream_entry)`` pairs from known VST stream shapes."""
        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_vst_stream_entries(item)
            return

        if not isinstance(payload, dict):
            return

        if "streamId" in payload or "name" in payload:
            yield None, payload
            return

        for key, value in payload.items():
            if isinstance(value, dict):
                if "streamId" in value or "name" in value:
                    yield key, value
                else:
                    yield from self._iter_vst_stream_entries(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        if "streamId" in item or "name" in item:
                            yield key, item
                        else:
                            yield from self._iter_vst_stream_entries(item)

    @staticmethod
    def _build_vst_url(vst_base_url: str, path: str) -> str:
        return f"{str(vst_base_url or '').rstrip('/')}/{str(path or '').lstrip('/')}"

    @staticmethod
    def _positive_int_setting(value: Any, *, default: int, setting_name: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            logger.warning("[vst-ready] Invalid %s=%r; using %s", setting_name, value, default)
            return default
        if parsed <= 0:
            logger.warning("[vst-ready] Invalid %s=%r; using %s", setting_name, value, default)
            return default
        return parsed

    @staticmethod
    def _is_transient_vst_error(exc: Exception) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code in {408, 425, 429, 500, 502, 503, 504}
        return isinstance(exc, (TimeoutError, urllib.error.URLError))

    def _vst_request_attempts(self) -> int:
        return self._positive_int_setting(
            getattr(self._settings, "vst_request_attempts", 3),
            default=3,
            setting_name="vst_request_attempts",
        )

    def _vst_request_backoff_seconds(self) -> float:
        raw_value = getattr(self._settings, "vst_request_backoff_seconds", 1.0)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            logger.warning("[vst-request] Invalid vst_request_backoff_seconds=%r; using 1.0", raw_value)
            return 1.0
        if value < 0:
            logger.warning("[vst-request] Invalid vst_request_backoff_seconds=%r; using 1.0", raw_value)
            return 1.0
        return value

    def _fetch_vst_url_text(self, url: str, timeout: int) -> str:
        attempts = self._vst_request_attempts()
        backoff_seconds = self._vst_request_backoff_seconds()
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                with urllib.request.urlopen(url, timeout=timeout) as response:
                    return response.read().decode("utf-8")
            except Exception as exc:  # noqa: BLE001 - converted to readiness diagnostics
                last_error = exc
                if attempt >= attempts or not self._is_transient_vst_error(exc):
                    break
                sleep_seconds = backoff_seconds * attempt
                logger.warning(
                    "[vst-request] Transient VST request failure for %s "
                    "attempt=%d/%d retry_in=%.1fs error=%s",
                    url, attempt, attempts, sleep_seconds, exc,
                )
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)

        raise RuntimeError(f"VST request failed for {url}: {last_error}") from last_error

    def _fetch_vst_url_json(self, url: str, timeout: int) -> Any:
        raw = self._fetch_vst_url_text(url, timeout)
        if not raw.strip():
            return {}

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"VST returned invalid JSON for {url}: {raw[:300]}") from exc

    def _fetch_vst_json(self, vst_base_url: str, path: str, timeout: int) -> Any:
        return self._fetch_vst_url_json(
            self._build_vst_url(vst_base_url, path),
            timeout,
        )

    def _iter_vst_status_entries(self, payload: Any):
        """Yield ``(stream_key, status_entry)`` pairs from known VST status shapes."""
        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_vst_status_entries(item)
            return

        if not isinstance(payload, dict):
            return

        if any(key in payload for key in ("name", "state", "errorCode", "error_code")):
            stream_key = payload.get("streamId") or payload.get("id") or payload.get("sensorId")
            yield stream_key, payload
            return

        for key, value in payload.items():
            if isinstance(value, dict):
                if any(field in value for field in ("name", "state", "errorCode", "error_code")):
                    yield key, value
                else:
                    yield from self._iter_vst_status_entries(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        if any(field in item for field in ("name", "state", "errorCode", "error_code")):
                            yield key, item
                        else:
                            yield from self._iter_vst_status_entries(item)

    @staticmethod
    def _vst_timeline_value_has_entries(value: Any) -> bool:
        if isinstance(value, list):
            return any(EvaluationService._vst_timeline_value_has_entries(item) for item in value)
        if not isinstance(value, dict):
            return False
        timestamp_keys = {"startTime", "start_time", "start", "endTime", "end_time", "end"}
        if any(value.get(key) for key in timestamp_keys):
            return True
        for nested_key in ("timelines", "timeline", "segments", "records", "recordings"):
            nested = value.get(nested_key)
            if nested and EvaluationService._vst_timeline_value_has_entries(nested):
                return True
        return False

    def _iter_vst_timeline_ids(self, payload: Any):
        """Yield stream IDs that have at least one known VST storage timeline."""
        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_vst_timeline_ids(item)
            return

        if not isinstance(payload, dict):
            return

        stream_id = payload.get("streamId") or payload.get("id") or payload.get("sensorId")
        if stream_id and self._vst_timeline_value_has_entries(payload):
            yield str(stream_id)

        for key, value in payload.items():
            if isinstance(value, (dict, list)) and self._vst_timeline_value_has_entries(value):
                yield str(key)
            elif isinstance(value, (dict, list)):
                yield from self._iter_vst_timeline_ids(value)

    def _collect_vst_sensor_readiness(
        self,
        vst_base_url: str,
        sensor_ids: List[str],
        *,
        timeout: int,
        require_timelines: bool,
    ) -> Dict[str, Any]:
        expected = [str(sensor).strip() for sensor in sensor_ids or [] if str(sensor).strip()]
        status_payload = self._fetch_vst_json(vst_base_url, "/api/v1/sensor/status", timeout)
        registered: Dict[str, str] = {}
        offline: Dict[str, Dict[str, str]] = {}
        resolved_by_sensor: Dict[str, Dict[str, str]] = {}

        for stream_key, entry in self._iter_vst_status_entries(status_payload):
            stream_id = str(entry.get("streamId") or entry.get("id") or stream_key or "").strip()
            name = str(entry.get("name") or "").strip()
            matched = [sensor for sensor in expected if sensor in {name, stream_id}]
            if not matched:
                continue

            state = str(entry.get("state") or "").strip().lower()
            error_code = str(entry.get("errorCode") or entry.get("error_code") or "").strip()
            error_message = str(entry.get("errorMessage") or entry.get("error_message") or "").strip()
            is_ready = state in {"online", "ready"} and error_code.lower() in {"", "noerror", "no_error"}
            for sensor in matched:
                resolved_by_sensor[sensor] = self._runtime_stream_identity(
                    sensor,
                    vst_stream_id=stream_id or sensor,
                    vst_stream_name=name,
                    use_vst_stream_id=True,
                )
                if is_ready:
                    registered[sensor] = stream_id or sensor
                    offline.pop(sensor, None)
                else:
                    offline[sensor] = {
                        "state": state or "unknown",
                        "error_code": error_code,
                        "error_message": error_message,
                    }

        missing = [sensor for sensor in expected if sensor not in registered and sensor not in offline]
        no_timeline: List[str] = []
        timeline_ids: set[str] = set()
        if require_timelines and registered:
            timeline_payload = self._fetch_vst_json(vst_base_url, "/api/v1/storage/timelines", timeout)
            timeline_ids = {stream_id for stream_id in self._iter_vst_timeline_ids(timeline_payload) if stream_id}
            no_timeline = [
                sensor for sensor, stream_id in registered.items()
                if sensor not in timeline_ids and stream_id not in timeline_ids
            ]

        for sensor, identity in resolved_by_sensor.items():
            identity["ready"] = str(sensor in registered and sensor not in no_timeline).lower()
            identity["has_timeline"] = str(not require_timelines or sensor not in no_timeline).lower()

        resolved_streams = [
            resolved_by_sensor[sensor]
            for sensor in expected
            if sensor in resolved_by_sensor
        ]

        return {
            "ready": not missing and not offline and not no_timeline,
            "expected": expected,
            "registered": registered,
            "resolved_streams": resolved_streams,
            "sensor_ids_requested": expected,
            "sensor_ids_submitted": [
                stream["submitted_sensor_id"] for stream in resolved_streams
            ],
            "stream_identity_mode": self._stream_identity_mode(resolved_streams),
            "missing": missing,
            "offline": offline,
            "no_timeline": no_timeline,
            "timeline_count": len(timeline_ids),
            "require_timelines": require_timelines,
        }

    def wait_for_vst_sensor_readiness(
        self,
        sensor_ids: List[str],
        *,
        sleep_fn=None,
        monotonic_fn=None,
    ) -> Dict[str, Any]:
        """Wait until VST has registered all synthetic latency runtime streams."""
        vst_base_url = str(getattr(self._settings, "vst_base_url", "") or "").rstrip("/")
        expected = [str(sensor).strip() for sensor in sensor_ids or [] if str(sensor).strip()]
        if not vst_base_url:
            logger.info("[vst-ready] VST_BASE_URL is not configured; skipping sensor readiness gate")
            resolved_streams = [
                self._runtime_stream_identity(sensor, use_vst_stream_id=False)
                for sensor in expected
            ]
            return {
                "enabled": False,
                "ready": True,
                "expected": expected,
                "resolved_streams": resolved_streams,
                "sensor_ids_requested": expected,
                "sensor_ids_submitted": [stream["submitted_sensor_id"] for stream in resolved_streams],
                "stream_identity_mode": self._stream_identity_mode(resolved_streams),
            }
        if not expected:
            return {
                "enabled": True,
                "ready": True,
                "expected": expected,
                "resolved_streams": [],
                "sensor_ids_requested": [],
                "sensor_ids_submitted": [],
                "stream_identity_mode": "provided_sensor_id",
            }

        timeout_seconds = self._positive_int_setting(
            getattr(self._settings, "vst_sensor_ready_timeout_seconds", 900),
            default=900,
            setting_name="vst_sensor_ready_timeout_seconds",
        )
        poll_seconds = self._positive_int_setting(
            getattr(self._settings, "vst_sensor_ready_poll_seconds", 5),
            default=5,
            setting_name="vst_sensor_ready_poll_seconds",
        )
        request_timeout = self._positive_int_setting(
            getattr(self._settings, "vst_media_timeout", 30),
            default=30,
            setting_name="vst_media_timeout",
        )
        request_timeout = min(request_timeout, max(1, timeout_seconds))
        require_timelines = bool(getattr(self._settings, "vst_require_timelines", True))
        sleep_fn = sleep_fn or time.sleep
        monotonic_fn = monotonic_fn or time.monotonic
        deadline = monotonic_fn() + timeout_seconds
        last_details: Dict[str, Any] = {}
        last_error = ""

        logger.info(
            "[vst-ready] Waiting for %d VST sensor(s) base=%s timeout=%ss poll=%ss require_timelines=%s",
            len(expected),
            vst_base_url,
            timeout_seconds,
            poll_seconds,
            require_timelines,
        )

        while True:
            try:
                last_details = self._collect_vst_sensor_readiness(
                    vst_base_url,
                    expected,
                    timeout=request_timeout,
                    require_timelines=require_timelines,
                )
                last_error = ""
            except RuntimeError as exc:
                last_details = {
                    "ready": False,
                    "expected": expected,
                    "missing": expected,
                    "offline": {},
                    "no_timeline": [],
                    "error": str(exc),
                }
                last_error = str(exc)

            if last_details.get("ready"):
                logger.info(
                    "[vst-ready] VST sensors ready registered=%d timeline_count=%d",
                    len(last_details.get("registered") or {}),
                    int(last_details.get("timeline_count") or 0),
                )
                return {"enabled": True, **last_details}

            remaining = deadline - monotonic_fn()
            if remaining <= 0:
                break

            missing = last_details.get("missing") or []
            offline = last_details.get("offline") or {}
            no_timeline = last_details.get("no_timeline") or []
            logger.info(
                "[vst-ready] Waiting for VST sensors: missing=%d offline=%d no_timeline=%d%s",
                len(missing),
                len(offline),
                len(no_timeline),
                f" error={last_error}" if last_error else "",
            )
            sleep_fn(min(float(poll_seconds), max(0.1, remaining)))

        missing = last_details.get("missing") or []
        offline = last_details.get("offline") or {}
        no_timeline = last_details.get("no_timeline") or []
        raise TimeoutError(
            "Timed out waiting for VST sensor readiness "
            f"after {timeout_seconds}s: missing={missing[:10]} "
            f"offline={list(offline.keys())[:10]} no_timeline={no_timeline[:10]}"
            f"{' error=' + last_error if last_error else ''}"
        )

    def _rewrite_vst_media_url_to_base(self, video_url: str, vst_base_url: str) -> str:
        """Keep VST-generated media on the configured VST base when possible."""
        try:
            parsed_video = urllib.parse.urlparse(video_url)
            parsed_base = urllib.parse.urlparse(vst_base_url)
        except Exception:  # noqa: BLE001 - keep original URL if parsing is odd
            return video_url

        if not parsed_video.path or not parsed_base.scheme or not parsed_base.netloc:
            return video_url

        base_path = parsed_base.path.rstrip("/")
        media_path = parsed_video.path
        if base_path and media_path.startswith(f"{base_path}/"):
            rewritten_path = media_path
        elif base_path and media_path.startswith("/vst/") and base_path != "/vst":
            rewritten_path = f"{base_path}{media_path[4:]}"
        elif base_path:
            rewritten_path = f"{base_path}/{media_path.lstrip('/')}"
        elif media_path.startswith("/vst/"):
            rewritten_path = media_path[4:]
        else:
            rewritten_path = media_path

        return urllib.parse.urlunparse((
            parsed_base.scheme,
            parsed_base.netloc,
            rewritten_path,
            "",
            parsed_video.query,
            parsed_video.fragment,
        ))

    def _gt_entry_with_media_url(
        self,
        gt_entry: Dict[str, Any],
        media_url: Optional[str],
    ) -> Dict[str, Any]:
        if not media_url:
            return gt_entry
        updated = dict(gt_entry)
        updated["media_urls"] = [media_url]
        updated.pop("media_url", None)
        return updated

    def _resolve_media_urls(
        self,
        video: str,
        gt_entry: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Resolve `info.media_urls` for the on-demand verification request.

        Resolution order (first hit wins):

        1. ``gt_entry["media_urls"]`` — list of absolute URLs supplied by GT.
        2. ``gt_entry["media_url"]`` — single absolute URL supplied by GT.
        3. ``f"{settings.media_base_url}/{video}{settings.media_file_ext}"``
           when GT does not specify a URL (legacy GT format that only carries
           a bare ``video`` name).

        AB's schema only requires the list to be non-empty and contain
        non-empty strings; it is the caller's responsibility to pick URLs
        that AB can actually fetch.
        """
        if gt_entry:
            urls = gt_entry.get("media_urls")
            if isinstance(urls, list):
                # Match AB's own validator (verification_schemas.py):
                # drop falsy entries (None / "") *before* str() so that a
                # stray ``None`` doesn't sneak through as the literal string
                # "None".
                cleaned = [str(u).strip() for u in urls if u and str(u).strip()]
                if cleaned:
                    return cleaned
            single = gt_entry.get("media_url")
            if isinstance(single, str) and single.strip():
                return [single.strip()]

        base = self._settings.media_base_url.rstrip('/')
        ext = self._settings.media_file_ext or ''
        media_path = video if ext and str(video).endswith(ext) else f"{video}{ext}"
        return [f"{base}/{media_path}"]

    def _resolve_category(
        self,
        video: str,
        category: Optional[str],
        gt_entry: Optional[Dict[str, Any]],
    ) -> str:
        """Pick the AB-compatible ``category`` to use for an incident.

        Resolution order (first hit wins):

        1. Explicit ``category`` argument from the caller (router) —
           respected verbatim. The caller is presumed to have chosen
           something AB already knows about.
        2. ``settings.default_incident_category`` — operator-wide override
           that lets a deployment funnel every incident into a single
           pre-seeded ``alert_type`` (handy for staged tenants that don't
           want per-row slugs). Setting this *opts out* of the per-row
           routing in step 3, so duplicate-video rows will share the
           default's prompt.
        3. Per-row slug from the GT entry via
           :func:`category_resolver.gt_row_slug`. Reads ``_eval_row_idx``
           that ``load_ground_truth`` stamped earlier so duplicate-video
           rows produce *distinct* slugs — matching the seed pass exactly.
        4. Slug derived from the bare ``video`` argument as a final
           fallback for callers without GT context.

        We never just return raw ``video`` anymore: it can contain ``/`` /
        ``.`` which AB's config CRUD validator rejects.  The /ondemand
        endpoint itself is permissive but mismatch with the seeded slug
        would defeat the seed. Slugifying everywhere keeps both paths in
        lock-step.
        """
        if isinstance(category, str) and category.strip():
            return category.strip()
        default = getattr(self._settings, "default_incident_category", None)
        if isinstance(default, str) and default.strip():
            return default.strip()
        if gt_entry is not None:
            if isinstance(gt_entry, dict) and gt_entry.get("_gt_format") == "events":
                event_category = str(gt_entry.get("category") or "").strip()
                if event_category:
                    return event_category
            row_idx = gt_entry.get("_eval_row_idx", 0) if isinstance(gt_entry, dict) else 0
            return gt_row_slug(gt_entry, row_idx)
        # Last-resort: slugify the bare video arg. ``slugify_alert_type``
        # raises on empty strings; callers ensure ``video`` is non-empty
        # before reaching this branch (it's the request payload's primary
        # identifier).
        return slugify_alert_type(video)

    def _seed_from_gt_and_retry(
        self,
        category: str,
        gt_entry: Optional[Dict[str, Any]],
        incident: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Best-effort: seed AB with the GT entry's prompt then re-submit.

        Triggered when AB returned 400 ``unknown_category`` — most often
        because AB restarted mid-batch and the auto-seed at evaluator
        startup is no longer reflected in Redis. Returns the 202 body on
        success, ``None`` on giving up. Failures here are intentionally
        swallowed; the caller logs the original ``UnknownCategoryError``
        and surfaces it to the report.
        """
        if not gt_entry:
            return None
        prompt = gt_entry.get("question") or gt_entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        system_prompt = gt_entry.get("system_prompt")
        try:
            self._ab_client.upsert_alert_config(
                alert_type=category,
                prompt=prompt.strip(),
                system_prompt=system_prompt.strip() if isinstance(system_prompt, str) and system_prompt.strip() else None,
            )
            logger.info(
                "[seed-on-demand] Re-seeded category=%s after UnknownCategoryError", category,
            )
        except AlertBridgeError as exc:
            logger.warning(
                "[seed-on-demand] Failed to re-seed category=%s: %s", category, exc,
            )
            return None
        try:
            return self._ab_client.submit_ondemand_verification(incident)
        except AlertBridgeError as exc:
            logger.warning(
                "[seed-on-demand] Retry submit failed for category=%s: %s", category, exc,
            )
            return None

    @staticmethod
    def _normalize_synthetic_alert_submission_mode(value: Optional[str]) -> str:
        normalized = str(value or "message_bus").strip().lower().replace("-", "_")
        aliases = {
            "message_bus": "message_bus",
            "messagebus": "message_bus",
            "bus": "message_bus",
            "kafka": "message_bus",
            "incident_api": "incident_api",
            "http_incident_api": "incident_api",
            "http_incident": "incident_api",
            "ondemand_api": "ondemand_api",
            "ondemand": "ondemand_api",
            "on_demand": "ondemand_api",
            "api": "ondemand_api",
        }
        if normalized in aliases:
            return aliases[normalized]
        raise ValueError(
            f"Invalid synthetic alert submission mode {value!r}; "
            "supported values are 'message_bus', 'incident_api', and 'ondemand_api'"
        )

    def _submit_synthetic_latency_incident(
        self,
        incident: Dict[str, Any],
        submission_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode = self._normalize_synthetic_alert_submission_mode(submission_mode)
        if mode == "ondemand_api":
            return self._ab_client.submit_ondemand_verification(incident)
        if mode == "incident_api":
            if not hasattr(self._ab_client, "submit_incident"):
                raise AlertBridgeError(
                    "Alert Bridge client does not support incident_api synthetic "
                    "submission via /api/v1/incidents"
                )
            return self._ab_client.submit_incident(incident)
        return self._kafka_synthetic_producer.submit_incident(incident)

    def build_incident(
        self,
        video: str,
        timestamp: str,
        end: Optional[str] = None,
        category: Optional[str] = None,
        media_video: Optional[str] = None,
        gt_entry: Optional[Dict[str, Any]] = None,
        include_media_urls: bool = True,
        alert_sensor_id: Optional[str] = None,
        runtime_stream_name: Optional[str] = None,
        vst_stream_id: Optional[str] = None,
        source_video_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build an Alert Bridge incident payload.

        ``include_media_urls=True`` builds the legacy on-demand verification
        payload for ``POST /api/v1/verification/ondemand``.
        ``include_media_urls=False`` builds a normal NvSchema incident for the
        direct Kafka ``message_bus`` path; Alert Agent then resolves
        media from ``sensorId`` plus timestamps in the worker path.

        ``info.userPrompt`` / ``info.systemPrompt`` are intentionally NOT
        included — AB resolves prompts server-side from
        ``alert_type_config.json`` / Redis ``alert_config:*`` keyed by
        ``category``. Anything we put in ``info`` for prompts is silently
        ignored upstream and only adds noise.

        Args:
            video: Video / sensor identifier (used for ``sensorId`` and as a
                fallback for media-URL resolution).
            timestamp: Start timestamp (ISO 8601).
            end: End timestamp (ISO 8601); defaults to ``timestamp``.
            category: Incident category; falls back to ``video`` to preserve
                legacy behaviour for callers that don't pass one.
            media_video: Optional media identifier used only for media URL resolution;
                defaults to ``video`` so normal incident behavior is unchanged.
            gt_entry: Optional GT row used to look up an explicit
                ``media_url`` / ``media_urls`` override.
            include_media_urls: Include direct media fields for on-demand API
                submission. Disable for message-bus latency injection.
            alert_sensor_id: Optional ID to publish in the NvSchema ``sensorId``
                field. Synthetic message-bus latency uses the VST ``streamId``
                here so Alert Agent can resolve media deterministically.
            runtime_stream_name: Optional human/runtime stream name retained in
                ``info`` for diagnostics when ``sensorId`` is a VST UUID.
            vst_stream_id: Optional VST stream UUID retained in ``info``.
            source_video_id: Optional source dataset/GT video id retained in
                ``info``.
        """
        incident_sensor_id = str(alert_sensor_id or video).strip()
        incident_id = f"{incident_sensor_id}_{timestamp}_{uuid.uuid4().hex[:8]}"

        info: Dict[str, Any] = {}
        if include_media_urls:
            info = {
                "media_urls": self._resolve_media_urls(media_video or video, gt_entry),
                "media_type": self._settings.media_type,
            }

        for key, value in (
            ("sourceVideoId", source_video_id),
            ("runtimeStreamName", runtime_stream_name),
            ("vstStreamId", vst_stream_id),
        ):
            if value is not None and str(value).strip():
                info[key] = str(value).strip()

        return {
            "id": incident_id,
            "sensorId": incident_sensor_id,
            "timestamp": timestamp,
            "end": end or timestamp,
            "category": category or video,
            "isAnomaly": True,
            "objectIds": [],
            "place": {"name": f"video={video}"},
            "analyticsModule": {
                "id": "Evaluation Module",
                "description": f"Evaluation incident for {video}",
            },
            "info": info,
        }

        return incident

    def _resolve_incident_category(self, requested_category: Optional[str], video: str) -> str:
        """Resolve incident category with explicit > configured-default > video fallback.

        Kept available as an operator hook for callers that want to honour
        ``settings.default_incident_category``. The per-incident submit path
        currently routes through :meth:`_resolve_category` instead so the
        slug stays in lock-step with what the seed lifecycle wrote to AB
        (per-row slugs from the GT file). Wire ``_resolve_incident_category``
        into ``evaluate_incident`` once the default category itself is
        seeded as an AB ``alert_type``.
        """
        explicit = (requested_category or '').strip()
        if explicit:
            return explicit
        configured = str(getattr(self._settings, 'default_incident_category', '') or '').strip()
        if configured:
            return configured
        return video


    def evaluate_incident(
        self,
        video: str,
        timestamp: str,
        end: Optional[str] = None,
        category: Optional[str] = None,
        wait_for_result: bool = True
    ) -> Dict[str, Any]:
        """
        Evaluate a single incident through Alert Bridge.

        Input: Only video, timestamp, end, category required.
        GT data (question, system_prompt, answer) loaded automatically from GT file.

        Flow:
        1. Load GT for video → get question, system_prompt, expected answer
        2. Configure prompt in Alert Bridge
        3. Build and submit incident
        4. Poll for VLM result
        5. Compare with GT
        """
        result = {
            "video": video,
            "timestamp": timestamp,
            "status": "processing"
        }

        # Step 1: Load GT for this video. Pass ``category`` so duplicate-video
        # rows are routed to the entry whose seed slug matches — without this
        # the first row by ``video`` always wins and accuracy comparison
        # silently picks the wrong answer for the second/third/... duplicate.
        gt_found, gt_entry = self.get_gt_for_video(video, category=category)

        if gt_entry:
            result["gt_question"] = gt_entry.get('question')
            result["gt_answer"] = gt_entry.get('answer')
            result["gt_system_prompt"] = gt_entry.get('system_prompt')

        # GT is optional — we can still evaluate without it (no comparison).
        # Prompts come from AB's Redis (`alert_config:*`), which the evaluator
        # seeds at startup from this same GT file via ``seed_alert_types_from_gt``.
        # ``category`` is the slug AB looks up; we keep it in lock-step with
        # the seeded slugs by going through ``_resolve_category``.

        use_category = self._resolve_category(video, category, gt_entry)

        incident = self.build_incident(
            video=video,
            timestamp=timestamp,
            end=end,
            category=use_category,
            gt_entry=gt_entry,
        )

        logger.info(
            "Submitting on-demand verification: video=%s category=%s media_urls=%s",
            video, use_category, incident["info"]["media_urls"],
        )

        try:
            submit_response = self._ab_client.submit_ondemand_verification(incident)
            correlation_id = submit_response.get("correlationId") or incident["id"]
            logger.info(f"✓ Submitted ondemand verification: correlationId={correlation_id}")
            result["incident_id"] = incident["id"]
            result["correlation_id"] = correlation_id
        except UnknownCategoryError as e:
            logger.warning(
                "AB unknown_category for %s — attempting seed-on-demand retry: %s",
                use_category, e,
            )
            retry_response = self._seed_from_gt_and_retry(use_category, gt_entry, incident)
            if retry_response is not None:
                correlation_id = retry_response.get("correlationId") or incident["id"]
                logger.info(f"✓ Submitted (after seed) ondemand verification: correlationId={correlation_id}")
                result["incident_id"] = incident["id"]
                result["correlation_id"] = correlation_id
            else:
                result["status"] = "failed"
                result["error"] = (
                    f"Alert Bridge has no prompt config for category='{use_category}' "
                    f"and seed-on-demand retry failed. Check that the GT entry has a "
                    f"valid `question`/`prompt` field and that AB is reachable. "
                    f"Original error: {e}"
                )
                return result
        except AlertBridgeError as e:
            result["status"] = "failed"
            result["error"] = f"Failed to submit incident: {e}"
            return result

        # Step 3: Poll for result if requested
        if not wait_for_result:
            result["status"] = "submitted"
            return result

        try:
            es_result = self._es_client.poll_for_result(video, timestamp)

            if not es_result:
                result["status"] = "timeout"
                result["error"] = "Timeout waiting for VLM result"
                return result

            # Extract VLM result
            info_data = es_result.get("info", {})
            result["verdict"] = info_data.get("verdict")
            result["reasoning"] = info_data.get("reasoning")
            result["verification_code"] = info_data.get("verificationResponseCode") or info_data.get("verification_response_code")
            result["verification_status"] = info_data.get("verificationResponseStatus") or info_data.get("verification_response_status")
            result["raw_response"] = es_result
            result["status"] = "success"

            logger.info(f"✓ Got VLM result for {video}: verdict={result['verdict']}")

        except ElasticsearchError as e:
            result["status"] = "error"
            result["error"] = f"Elasticsearch query failed: {e}"
            return result

        # Step 4: Compare with GT
        result = self._compare_with_gt(result, gt_entry)

        return result

    def _compare_with_gt(
        self,
        result: Dict[str, Any],
        gt_entry: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Compare VLM verdict with ground truth."""
        if not gt_entry:
            result["comparison_status"] = "no_ground_truth"
            result["match"] = None
            return result

        expected = (gt_entry.get('answer', '')).lower().strip()
        verdict = (result.get('verdict') or '').lower().strip()

        # Normalize for comparison
        # GT: "Yes"/"No"
        # VLM: "confirmed"/"rejected"/"unverified"

        expected_normalized = expected
        if expected in ['yes', 'true', '1']:
            expected_normalized = 'confirmed'
        elif expected in ['no', 'false', '0']:
            expected_normalized = 'rejected'

        verdict_normalized = verdict
        if verdict in ['yes', 'true']:
            verdict_normalized = 'confirmed'
        elif verdict in ['no', 'false']:
            verdict_normalized = 'rejected'

        is_match = expected_normalized == verdict_normalized

        result["match"] = is_match
        result["comparison_status"] = "exact" if is_match else "false"

        return result

    def evaluate_batch(
        self,
        incidents: List[Dict[str, Any]],
        wait_for_results: bool = True
    ) -> List[Dict[str, Any]]:
        """Evaluate a batch of incidents."""
        results = []

        for incident_params in incidents:
            result = self.evaluate_incident(
                video=incident_params['video'],
                timestamp=incident_params['timestamp'],
                end=incident_params.get('end'),
                category=incident_params.get('category'),
                wait_for_result=wait_for_results
            )
            results.append(result)

        return results

    def calculate_summary(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate summary statistics from evaluation results."""
        total = len(results)
        confirmed = sum(1 for r in results if r.get('verdict') == 'confirmed')
        rejected = sum(1 for r in results if r.get('verdict') == 'rejected')
        unverified = sum(1 for r in results if r.get('verdict') == 'unverified' or r.get('status') in ['timeout', 'error', 'failed'])

        gt_matches = sum(1 for r in results if r.get('match') is True)
        gt_mismatches = sum(1 for r in results if r.get('match') is False)
        no_gt = sum(1 for r in results if r.get('comparison_status') == 'no_ground_truth')

        comparable = gt_matches + gt_mismatches
        accuracy = f"{(gt_matches / comparable * 100):.2f}%" if comparable > 0 else "N/A"
        match_rate = gt_matches / comparable if comparable > 0 else 0.0

        return {
            "total": total,
            "confirmed": confirmed,
            "rejected": rejected,
            "unverified": unverified,
            "gt_matches": gt_matches,
            "gt_mismatches": gt_mismatches,
            "no_ground_truth": no_gt,
            "accuracy": accuracy,
            "match_rate": match_rate
        }

    # -------------------------------------------------------------------------
    # Eval V2: events-based GT; compare event counts (GT vs mdx-vlm-incidents*)
    # Input: GT path from env (GT_FILE_PATH), timestamp from /eval/v2/batch payload.
    # Flow: query mdx-vlm-incidents* by sensor name only; PASS if count(ES) == count(GT) for that sensor.
    #       start_ts/end_ts are still computed from GT for reference in reports (no time filter on query).
    # -------------------------------------------------------------------------

    @staticmethod
    def _parse_iso_and_add_seconds(iso_timestamp: str, offset_seconds: int) -> Optional[str]:
        """Parse ISO timestamp and add offset_seconds; return ISO string."""
        try:
            ts = iso_timestamp.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ts)
            end_dt = dt + timedelta(seconds=offset_seconds)
            return end_dt.isoformat().replace('+00:00', 'Z') if iso_timestamp.endswith('Z') else end_dt.isoformat()
        except Exception as e:
            logger.warning(f"Failed to parse timestamp {iso_timestamp}: {e}")
            return None

    def _resolve_prometheus_metric_sensor_id(
        self,
        sensor_id: str,
        runtime_stream: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, Dict[str, str]]:
        requested = str(sensor_id or "").strip()
        if runtime_stream:
            identities = self._normalize_runtime_stream_identities(
                [runtime_stream],
                use_vst_stream_id=True,
            )
            if identities:
                identity = identities[0]
                identifiers = {
                    str(identity.get(key) or "").strip()
                    for key in (
                        "requested_sensor_id",
                        "submitted_sensor_id",
                        "vst_stream_id",
                        "vst_stream_name",
                    )
                }
                identifiers.discard("")
                if not requested or requested in identifiers:
                    submitted = str(identity.get("submitted_sensor_id") or requested).strip()
                    if submitted:
                        return submitted, identity
                logger.warning(
                    "[prometheus] Ignoring runtime stream mapping that does not match sensor_id=%s: %s",
                    requested, identity,
                )

        mode = self._normalize_synthetic_alert_submission_mode(
            getattr(self._settings, "synthetic_alert_submission_mode", "message_bus")
        )
        if not (
            requested
            and bool(getattr(self._settings, "synthetic_alerts_injection", False))
            and self._submission_uses_vst_stream_id(mode)
            and bool(str(getattr(self._settings, "vst_base_url", "") or "").strip())
        ):
            return requested, {}

        timeout = self._positive_int_setting(
            getattr(self._settings, "vst_media_timeout", 30),
            default=30,
            setting_name="vst_media_timeout",
        )
        stream_id = self._resolve_vst_stream_id(
            str(getattr(self._settings, "vst_base_url", "") or "").rstrip("/"),
            requested,
            timeout,
        )
        if not stream_id:
            return requested, {}
        identity = self._runtime_stream_identity(
            requested,
            vst_stream_id=stream_id,
            use_vst_stream_id=True,
        )
        return identity["submitted_sensor_id"], identity

    def _evaluate_latency_v2_prometheus(
        self,
        video: str,
        timestamp: Optional[str],
        start_timestamp: Optional[str],
        end_timestamp: Optional[str],
        runtime_stream: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        metric_sensor_id, stream_identity = self._resolve_prometheus_metric_sensor_id(
            video,
            runtime_stream,
        )
        result = {
            "video": video,
            "sensor_id": video,
            "metric_sensor_id": metric_sensor_id,
            "requested_sensor_id": video,
            "submitted_sensor_id": metric_sensor_id,
            "timestamp": end_timestamp or start_timestamp or timestamp or "",
            "start_ts": start_timestamp or "",
            "end_ts": end_timestamp or "",
            "eval_version": "v2",
            "eval_mode": "latency",
            "data_source": "prometheus",
            "metric_profile": self._prometheus_metric_profile(),
        }
        if stream_identity:
            result.update(stream_identity)

        if self._prom_client is None:
            result.update(
                {
                    "status": "error",
                    "error": "Prometheus client is not configured",
                }
            )
            return result

        metric_profile = result["metric_profile"]
        if metric_profile == "ondemand":
            result.update(
                {
                    "status": "not_applicable",
                    "error": "On-demand Prometheus metrics are aggregate-only; see latency_run_summary.csv and latency_stage_breakdown.csv",
                    "stats": {},
                    "reliability": {},
                }
            )
            return result

        try:
            if start_timestamp and end_timestamp:
                stats = self._prom_client.query_latency_metrics(
                    sensor_id=metric_sensor_id,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                )
                reliability = self._prom_client.query_reliability_metrics(
                    sensor_id=metric_sensor_id,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                )
            else:
                stats = self._prom_client.query_latency_metrics(
                    sensor_id=metric_sensor_id,
                    duration=self._settings.latency_duration,
                )
                reliability = self._prom_client.query_reliability_metrics(
                    sensor_id=metric_sensor_id,
                    duration=self._settings.latency_duration,
                )
            has_latency_data = any(float(stage.get("count", 0) or 0) > 0 for stage in stats.values())
            has_reliability_data = any(int(v or 0) > 0 for v in reliability.values())
            has_data = has_latency_data or has_reliability_data
            result["status"] = "success" if has_data else "no_data"
            result["stats"] = stats
            result["reliability"] = reliability
            return result
        except PrometheusError as exc:
            result.update(
                {
                    "status": "error",
                    "error": str(exc),
                    "stats": {},
                    "reliability": {},
                }
            )
            return result

    def collect_prometheus_latency_batch(
        self,
        incidents: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Collect Prometheus latency metrics for a list of incident windows."""
        results: List[Dict[str, Any]] = []
        for inc in incidents:
            video = str(inc.get("video") or inc.get("video_id") or "").strip()
            timestamp = inc.get("timestamp")
            start_timestamp = inc.get("start_timestamp")
            end_timestamp = inc.get("end_timestamp") or inc.get("end")
            if not video:
                results.append(
                    {
                        "video": "",
                        "sensor_id": "",
                        "timestamp": end_timestamp or start_timestamp or timestamp or "",
                        "start_ts": start_timestamp or "",
                        "end_ts": end_timestamp or "",
                        "eval_version": "v2",
                        "eval_mode": "latency",
                        "data_source": "prometheus",
                        "status": "error",
                        "error": "Missing video/sensor id in incident payload",
                        "stats": {},
                        "reliability": {},
                    }
                )
                continue
            results.append(
                self._evaluate_latency_v2_prometheus(
                    video=video,
                    timestamp=timestamp,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                    runtime_stream=inc.get("runtime_stream"),
                )
            )
        return results

    @staticmethod
    def _parse_iso_timestamp(iso_timestamp: str) -> Optional[datetime]:
        try:
            ts = str(iso_timestamp).strip().replace("Z", "+00:00")
            return datetime.fromisoformat(ts)
        except Exception:
            return None

    @staticmethod
    def _to_utc_z(dt: datetime) -> str:
        if dt.tzinfo is None:
            return dt.isoformat() + "Z"
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _parse_duration_seconds(duration: str) -> Optional[int]:
        if not duration:
            return None
        value = str(duration).strip().lower()
        unit = value[-1:]
        number = value[:-1] if unit in {"s", "m", "h", "d"} else value
        multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit, 1)
        try:
            seconds = int(float(number) * multiplier)
        except (TypeError, ValueError):
            return None
        return seconds if seconds > 0 else None

    @classmethod
    def _incident_window_metadata(cls, incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
        min_start_dt: Optional[datetime] = None
        max_end_dt: Optional[datetime] = None

        for inc in incidents:
            start_raw = (
                inc.get("timestamp")
                or inc.get("start_timestamp")
                or inc.get("start")
                or inc.get("end")
                or inc.get("end_timestamp")
            )
            end_raw = inc.get("end") or inc.get("end_timestamp") or start_raw
            if not start_raw or not end_raw:
                continue

            start_dt = cls._parse_iso_timestamp(start_raw)
            end_dt = cls._parse_iso_timestamp(end_raw)
            if not start_dt or not end_dt:
                continue

            if min_start_dt is None or start_dt < min_start_dt:
                min_start_dt = start_dt
            if max_end_dt is None or end_dt > max_end_dt:
                max_end_dt = end_dt

        if min_start_dt is None or max_end_dt is None:
            return {}

        return {
            "observed_incident_start_ts": cls._to_utc_z(min_start_dt),
            "observed_incident_end_ts": cls._to_utc_z(max_end_dt),
            "observed_incident_window_seconds": int(round(max_end_dt.timestamp() - min_start_dt.timestamp())),
        }

    def _sensor_ids_from_incidents(self, incidents: List[Dict[str, Any]]) -> List[str]:
        sensor_ids: List[str] = []
        for inc in incidents:
            sensor_id = str(inc.get("video") or inc.get("sensor_id") or "").strip()
            if sensor_id:
                metric_sensor_id, _identity = self._resolve_prometheus_metric_sensor_id(
                    sensor_id,
                    inc.get("runtime_stream"),
                )
                sensor_ids.append(metric_sensor_id or sensor_id)
        return sensor_ids

    def _observed_incident_window_from_es(
        self,
        start_ts: str,
        end_ts: str,
        incidents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if self._es_client is None or not hasattr(self._es_client, "query_alert_incident_window"):
            return {}
        try:
            docs = self._es_client.query_alert_incident_window(
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                sensor_ids=self._sensor_ids_from_incidents(incidents),
            )
        except ElasticsearchError as exc:
            logger.warning("Failed to query observed incident window from Elasticsearch: %s", exc)
            return {}
        except Exception as exc:  # noqa: BLE001 - reporting metadata must not fail eval
            logger.warning("Failed to derive observed incident window from Elasticsearch: %s", exc)
            return {}
        return self._incident_window_metadata(docs)

    def collect_prometheus_aggregate_summary(
        self,
        incidents: List[Dict[str, Any]],
        *,
        window_start_ts: Optional[str] = None,
        window_end_ts: Optional[str] = None,
        sensor_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Collect aggregate Prometheus metrics for summary reporting.

        Synthetic realtime runs can provide runtime sensor IDs to scope exact
        completeness counts to the streams owned by the evaluation. Prefer an
        explicit measurement window. If none is provided, derive a
        fixed window ending now from latency_duration. Observed incident window
        metadata is derived from real ES alert/incident documents inside that
        measurement window, not from eval input rows.
        """
        if self._prom_client is None:
            return {
                "status": "error",
                "error": "Prometheus client is not configured",
            }

        start_ts = window_start_ts
        end_ts = window_end_ts
        configured_seconds = self._parse_duration_seconds(
            getattr(self._settings, "latency_duration", "")
        )

        if not start_ts and end_ts and configured_seconds:
            end_dt = self._parse_iso_timestamp(end_ts)
            if end_dt:
                start_ts = self._to_utc_z(end_dt - timedelta(seconds=configured_seconds))

        if not (start_ts and end_ts) and configured_seconds:
            end_dt = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(seconds=configured_seconds)
            start_ts = self._to_utc_z(start_dt)
            end_ts = self._to_utc_z(end_dt)

        if not (start_ts and end_ts):
            error_summary = {
                "status": "error",
                "error": "Could not resolve aggregate summary window from explicit latency window or valid latency_duration",
            }
            if configured_seconds:
                error_summary["configured_latency_duration_seconds"] = configured_seconds
            return error_summary

        try:
            summary = self._prom_client.query_aggregate_summary_metrics(
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                buffer_seconds=int(getattr(self._settings, "prometheus_summary_buffer_seconds", 0)),
                metric_profile=self._prometheus_metric_profile(),
                sensor_ids=sensor_ids,
            )
        except PrometheusError as exc:
            summary = {
                "status": "error",
                "error": str(exc),
                "start_ts": start_ts,
                "end_ts": end_ts,
            }

        observed_window = self._observed_incident_window_from_es(start_ts, end_ts, incidents)
        summary.update(observed_window)
        if configured_seconds:
            summary["configured_latency_duration_seconds"] = configured_seconds
        observed_seconds = observed_window.get("observed_incident_window_seconds")
        if configured_seconds and observed_seconds is not None:
            summary["incident_coverage_ratio"] = round(observed_seconds / configured_seconds, 4)
        return summary

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _safe_int(cls, value: Any) -> int:
        numeric = cls._safe_float(value)
        if numeric is None:
            return 0
        return int(round(numeric))

    @classmethod
    def _prometheus_per_sensor_summary_status(cls, results: List[Dict[str, Any]]) -> str:
        has_error = False
        for result in results:
            if result.get("status") == "error":
                has_error = True
            stats = result.get("stats") or {}
            reliability = result.get("reliability") or {}
            if any(
                cls._safe_int((stage_stats or {}).get("count")) > 0
                for stage_stats in stats.values()
            ):
                return "has_data"
            if any(cls._safe_int(value) > 0 for value in reliability.values()):
                return "has_data"
        return "error" if has_error else "no_data"

    def build_prometheus_latency_summary(
        self,
        results: List[Dict[str, Any]],
        prometheus_aggregate_summary: Optional[Dict[str, Any]],
        synthetic_injection_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build the top-level Prometheus latency summary from aggregate metrics."""
        aggregate = prometheus_aggregate_summary if isinstance(prometheus_aggregate_summary, dict) else {}
        raw_status = str(aggregate.get("status") or "error").strip().lower()
        summary_status = raw_status if raw_status in {"success", "no_data", "error"} else "error"
        per_sensor_status = self._prometheus_per_sensor_summary_status(results)
        aggregate_stats_raw = (aggregate.get("stats") or {}) if isinstance(aggregate.get("stats"), dict) else {}
        aggregate_reliability_raw = (
            (aggregate.get("reliability") or {})
            if isinstance(aggregate.get("reliability"), dict)
            else {}
        )
        aggregate_has_metric_data = any(
            self._safe_int((stage_stats or {}).get("count")) > 0
            for stage_stats in aggregate_stats_raw.values()
            if isinstance(stage_stats, dict)
        ) or any(
            self._safe_int(value) > 0
            for value in aggregate_reliability_raw.values()
        )
        if (
            summary_status == "success"
            and results
            and per_sensor_status == "no_data"
            and aggregate_has_metric_data
        ):
            summary_status = "degraded"
        aggregate_ready = summary_status in {"success", "no_data", "degraded"}

        summary: Dict[str, Any] = {
            "total_sensors": len(results),
            "total_hits": 0,
            "total_usable": 0,
            "summary_source": "prometheus_aggregate",
            "summary_status": summary_status,
            "per_sensor_summary_status": per_sensor_status,
        }

        window = {
            key: aggregate.get(key)
            for key in (
                "start_ts",
                "end_ts",
                "window_seconds",
                "buffer_seconds",
                "configured_latency_duration_seconds",
                "observed_incident_start_ts",
                "observed_incident_end_ts",
                "observed_incident_window_seconds",
                "incident_coverage_ratio",
            )
            if aggregate.get(key) is not None
        }
        if window:
            summary["prometheus_window"] = window
        if summary_status == "error" and aggregate.get("error"):
            summary["summary_error"] = str(aggregate.get("error"))
        elif summary_status == "degraded":
            summary["summary_error"] = (
                "Aggregate Prometheus metrics exist, but no requested sensor had "
                "per-sensor latency or reliability data. Check stream identifier mapping."
            )

        aggregate_stats = aggregate_stats_raw if aggregate_ready else {}
        for stage in self.PROMETHEUS_LATENCY_STAGES:
            stage_stats = aggregate_stats.get(stage) or {}
            summary[stage] = {
                "avg": self._safe_float(stage_stats.get("avg")),
                "p50": self._safe_float(stage_stats.get("p50")),
                "p90": self._safe_float(stage_stats.get("p90")),
                "p99": self._safe_float(stage_stats.get("p99")),
                "max": None,
                "n": self._safe_int(stage_stats.get("count")),
            }

        summary["metric_profile"] = str(aggregate.get("metric_profile") or self._prometheus_metric_profile())

        aggregate_reliability = aggregate_reliability_raw if aggregate_ready else {}
        reliability = {
            key: self._safe_int(aggregate_reliability.get(key))
            for key in self.PROMETHEUS_RELIABILITY_KEYS
        }
        verified_total = reliability["total_verified_events"]
        if verified_total <= 0:
            verified_total = (
                reliability["confirmed_events"]
                + reliability["rejected_events"]
                + reliability["verification_failed_events"]
                + reliability["unknown_events"]
            )
        reliability["total_verified_events"] = verified_total
        reliability["total_events"] = verified_total
        reliability["terminal_outcomes"] = verified_total
        reliability["successful_verifications"] = (
            reliability["confirmed_events"] + reliability["rejected_events"]
        )

        event_count = summary["e2e"]["n"] if summary["e2e"]["n"] > 0 else verified_total
        summary["total_hits"] = event_count
        summary["total_usable"] = event_count
        summary["reliability"] = reliability

        if (
            bool(getattr(self._settings, "synthetic_alerts_injection", False))
            and aggregate_ready
        ):
            injection = (
                synthetic_injection_summary
                if isinstance(synthetic_injection_summary, dict)
                else {}
            )
            target_submissions = self._safe_int(injection.get("target_submissions"))
            if target_submissions <= 0:
                rate = self._safe_float(
                    getattr(self._settings, "synthetic_alerts_per_minute", None)
                )
                duration_seconds = self._parse_duration_seconds(
                    str(getattr(self._settings, "latency_duration", "") or "")
                )
                if rate and duration_seconds:
                    target_submissions = max(1, int(rate * duration_seconds / 60.0))
            attempted = self._safe_int(injection.get("attempted"))
            submitted = self._safe_int(injection.get("submitted"))
            failed = self._safe_int(injection.get("failed"))
            missed = self._safe_int(injection.get("missed"))
            if not injection and target_submissions > 0:
                # Backward-compatible estimate for callers that do not yet
                # forward the injection job summary. New wrappers pass exact counts.
                submitted = target_submissions
            terminal_outcomes = verified_total
            successful_verifications = reliability["successful_verifications"]
            e2e_observed = summary["e2e"]["n"]
            submission_coverage = (
                submitted / target_submissions if target_submissions > 0 else None
            )
            terminal_coverage = (
                terminal_outcomes / submitted if submitted > 0 else None
            )
            e2e_coverage = (
                e2e_observed / terminal_outcomes if terminal_outcomes > 0 else None
            )
            min_coverage = float(
                getattr(self._settings, "synthetic_e2e_min_coverage_ratio", 0.99)
                or 0.99
            )
            completeness = {
                "target_submissions": target_submissions,
                "attempted": attempted,
                "submitted": submitted,
                "failed": failed,
                "missed": missed,
                "terminal_outcomes": terminal_outcomes,
                "successful_verifications": successful_verifications,
                "e2e_observed": e2e_observed,
                "e2e_missing": max(0, terminal_outcomes - e2e_observed),
                "submission_coverage_ratio": submission_coverage,
                "terminal_coverage_ratio": terminal_coverage,
                "e2e_coverage_ratio": e2e_coverage,
                "e2e_min_coverage_ratio": min_coverage,
                "counts_source": "injection_summary" if injection else "configured_target_estimate",
                "metrics_counts_source": str(
                    aggregate.get("counts_source") or "prometheus_increase"
                ),
            }
            summary["synthetic_completeness"] = completeness

            invalid_reasons: List[str] = []
            degraded_reasons: List[str] = []
            aggregate_errors = aggregate.get("errors") or {}
            failure_rows = (
                list(aggregate_errors.get("verification_failure_reasons") or [])
                + list(aggregate_errors.get("ondemand_verification_failure_reasons") or [])
            )
            no_prompt_count = sum(
                self._safe_int(row.get("count"))
                for row in failure_rows
                if isinstance(row, dict) and str(row.get("reason") or "") == "no_prompt"
            )

            if terminal_outcomes <= 0:
                invalid_reasons.append("no terminal outcomes were emitted")
            if submitted > 0 and terminal_outcomes > submitted:
                invalid_reasons.append(
                    f"terminal outcomes ({terminal_outcomes}) exceed submitted alerts "
                    f"({submitted}); metric populations are not aligned"
                )
            if e2e_observed <= 0:
                invalid_reasons.append("no E2E latency observations were emitted")
            elif terminal_outcomes > 0 and e2e_observed > terminal_outcomes:
                invalid_reasons.append(
                    f"E2E observations ({e2e_observed}) exceed terminal outcomes "
                    f"({terminal_outcomes}); metric populations are not aligned"
                )
            elif e2e_coverage is not None and e2e_coverage < min_coverage:
                invalid_reasons.append(
                    f"E2E coverage {e2e_coverage:.4f} is below required "
                    f"threshold {min_coverage:.4f} "
                    f"({e2e_observed}/{terminal_outcomes})"
                )
            elif e2e_coverage is not None and e2e_coverage < 1.0:
                degraded_reasons.append(
                    f"E2E coverage is incomplete: {e2e_observed}/{terminal_outcomes} "
                    f"({e2e_coverage:.4f})"
                )
            if submission_coverage is not None and submission_coverage < 1.0:
                degraded_reasons.append(
                    f"submission coverage is incomplete: "
                    f"{submitted}/{target_submissions} ({submission_coverage:.4f})"
                )
            if terminal_coverage is not None and terminal_coverage < 1.0:
                degraded_reasons.append(
                    f"terminal outcome coverage is incomplete: "
                    f"{terminal_outcomes}/{submitted} ({terminal_coverage:.4f})"
                )
            mode = self._normalize_synthetic_alert_submission_mode(
                getattr(self._settings, "synthetic_alert_submission_mode", "message_bus")
            )
            required_stages = ("vlm",) if mode == "ondemand_api" else ("vst", "vlm")
            for stage in required_stages:
                if summary[stage]["n"] <= 0:
                    invalid_reasons.append(f"no {stage.upper()} latency observations were emitted")
            if no_prompt_count > 0:
                invalid_reasons.append(f"{no_prompt_count} event(s) failed with no_prompt")

            if invalid_reasons:
                previous_error = str(summary.get("summary_error") or "").strip()
                validation_error = "Synthetic latency validation failed: " + "; ".join(invalid_reasons)
                if previous_error:
                    validation_error = f"{previous_error} {validation_error}"
                summary["summary_status"] = "invalid"
                summary["summary_error"] = validation_error
                summary["validation_failures"] = invalid_reasons
                summary["no_prompt_failures"] = no_prompt_count
            elif degraded_reasons:
                previous_error = str(summary.get("summary_error") or "").strip()
                degraded_error = "Synthetic latency completeness degraded: " + "; ".join(
                    degraded_reasons
                )
                if previous_error:
                    degraded_error = f"{previous_error} {degraded_error}"
                summary["summary_status"] = "degraded"
                summary["summary_error"] = degraded_error
                summary["validation_warnings"] = degraded_reasons

        return summary

    def _prometheus_metric_profile(self) -> str:
        return str(getattr(self._settings, "prometheus_metric_profile", "realtime") or "realtime").strip().lower()

    def evaluate_incident_v2(
        self,
        video: str,
        timestamp: Optional[str] = None,
        start_timestamp: Optional[str] = None,
        end_timestamp: Optional[str] = None,
        category: Optional[str] = None,
        wait_for_result: bool = True,
        runtime_stream: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Eval v2 (events-based).

        When eval_mode == 'latency': extract info.latency.* from ES, return latency metrics.
        When eval_mode == 'accuracy' (default): compare GT event count vs ES event count.
        """
        # BCQ GT format (items/bcq array with question/answer) uses synthetic accuracy flow.
        # In latency mode we must stay on latency-only paths.
        gt_raw = self.get_ground_truth_raw()
        if self._settings.eval_mode != "latency" and ('items' in gt_raw or 'bcq' in gt_raw):
            logger.info(f"[v2/incident] BCQ GT detected — routing to synthetic eval: video={video}")
            return self.evaluate_synthetic_incident(
                video_id=video,
                timestamp=start_timestamp or timestamp,
                end=end_timestamp,
                category=category,
                wait_for_result=wait_for_result,
            )

        logger.debug(f"[v2/incident] eval_mode={self._settings.eval_mode} video={video} start={start_timestamp or timestamp} end={end_timestamp}")
        if self._settings.eval_mode == "latency":
            if self._settings.data_source == "elasticsearch" and self._latency_service:
                logger.info(f"[v2/incident] Latency mode (elasticsearch) — delegating to LatencyService: video={video}")
                return self._latency_service.evaluate_latency_for_sensor(
                    sensor_id=video,
                    start_ts=start_timestamp or timestamp,
                    end_ts=end_timestamp,
                )
            if self._settings.data_source == "prometheus":
                logger.info(
                    "[v2/incident] Latency mode (prometheus/%s) — querying Prometheus metrics: video=%s",
                    self._prometheus_metric_profile(),
                    video,
                )
                return self._evaluate_latency_v2_prometheus(
                    video=video,
                    timestamp=timestamp,
                    start_timestamp=start_timestamp,
                    end_timestamp=end_timestamp,
                    runtime_stream=runtime_stream,
                )
        # Resolve time window: either (start_timestamp, end_timestamp) or legacy (timestamp + GT)
        if start_timestamp and end_timestamp:
            start_ts = start_timestamp
            end_ts = end_timestamp
            result_timestamp = end_timestamp  # for report
        elif timestamp:
            start_ts = None
            end_ts = None
            result_timestamp = timestamp
        else:
            return {
                "video": video,
                "timestamp": "",
                "status": "failed",
                "error": "Either (start_timestamp and end_timestamp) or timestamp must be provided",
                "eval_version": "v2",
                "sensor_id": video,
                "gt_events_count": 0,
                "actual_events_count": None,
                "pass": False,
            }

        result = {
            "video": video,
            "timestamp": result_timestamp,
            "status": "processing",
            "eval_version": "v2",
            "sensor_id": video,
            "data_source": self._settings.data_source,
        }
        events = self.get_gt_events_for_sensor(video)
        logger.info(f"GT Events: {events}")
        if not events:
            result["status"] = "failed"
            result["error"] = "No ground truth events for this sensor (missing or empty events in GT file)"
            result["gt_events_count"] = 0
            result["actual_events_count"] = None
            result["pass"] = False
            return result

        # Legacy path: compute start_ts/end_ts from timestamp + GT offsets
        if start_ts is None and end_ts is None:
            start_offsets = [int(e.get("start_time", 0)) for e in events if e.get("start_time") is not None]
            end_offsets = [int(e.get("end_time", 0)) for e in events if e.get("end_time") is not None]
            if not start_offsets or not end_offsets:
                result["status"] = "failed"
                result["error"] = "GT events must have start_time and end_time (seconds)"
                result["gt_events_count"] = len(events)
                result["actual_events_count"] = None
                result["pass"] = False
                return result
            min_start = min(start_offsets)
            max_end = max(end_offsets)
            start_ts = self._parse_iso_and_add_seconds(timestamp, min_start)
            end_ts = self._parse_iso_and_add_seconds(timestamp, max_end)
            if not start_ts or not end_ts:
                result["status"] = "failed"
                result["error"] = "Invalid input timestamp (ISO 8601 required)"
                result["gt_events_count"] = len(events)
                result["actual_events_count"] = None
                result["pass"] = False
                return result

        result["start_ts"] = start_ts
        result["end_ts"] = end_ts
        result["gt_events_count"] = len(events)

        if self._settings.data_source == "prometheus":
            if self._prom_client is None:
                result["status"] = "error"
                result["error"] = "Prometheus client is not configured"
                result["actual_events_count"] = None
                result["pass"] = False
                return result
            try:
                prom_count = self._prom_client.query_confirmed_events_count(
                    sensor_id=video,
                    start_timestamp=start_ts,
                    end_timestamp=end_ts,
                )
                result["actual_events_count"] = prom_count
                result["status"] = "success"
                result["pass"] = prom_count == result["gt_events_count"]
                return result
            except PrometheusError as exc:
                result["status"] = "error"
                result["error"] = str(exc)
                result["actual_events_count"] = None
                result["pass"] = False
                return result

        try:
            if start_timestamp and end_timestamp:
                # Query ES between start and end (alerts first, then incidents)
                docs = self._es_client.query_alerts_then_incidents_by_time_range(
                    sensor_id=video,
                    start_timestamp=start_ts,
                    end_timestamp=end_ts,
                    size=500
                )
            else:
                docs = self._es_client.query_incidents_by_sensor(
                    sensor_id=video,
                    size=500
                )
            logger.info(f"Docs: {docs}")
        except ElasticsearchError as e:
            result["status"] = "error"
            result["error"] = str(e)
            result["actual_events_count"] = None
            result["pass"] = False
            return result

        # Count only incidents/alerts with info.verdict == "confirmed" as actual
        es_count = sum(
            1 for d in docs
            if (d.get("info") or {}).get("verdict") == "confirmed"
        )
        logger.info(f"Confirmed events count retrieved from ES: {es_count} between {start_ts} and {end_ts}")
        result["actual_events_count"] = es_count
        result["status"] = "success"
        result["pass"] = es_count == result["gt_events_count"]
        if docs:
            result["raw_response"] = docs
        return result

    def evaluate_batch_v2(
        self,
        incidents: List[Dict[str, Any]],
        wait_for_results: bool = True
    ) -> List[Dict[str, Any]]:
        """Evaluate a batch with eval v2.

        eval_mode='latency'  -> delegates to LatencyService for each sensor.
        eval_mode='accuracy' -> accuracy comparison (GT count vs ES count).
        """
        # BCQ GT format delegates to synthetic flow only for non-latency runs.
        gt_raw = self.get_ground_truth_raw()
        if self._settings.eval_mode != "latency" and ('items' in gt_raw or 'bcq' in gt_raw):
            logger.info(f"[v2/batch] BCQ GT detected — routing {len(incidents)} incidents to synthetic eval")
            return self.evaluate_synthetic_batch(
                [
                    {
                        'video_id': inc['video'],
                        'timestamp': inc.get('start_timestamp') or inc.get('timestamp'),
                        'end': inc.get('end_timestamp'),
                        'category': inc.get('category'),
                    }
                    for inc in incidents
                ],
                wait_for_results=wait_for_results,
            )

        logger.debug(f"[v2/batch] eval_mode={self._settings.eval_mode} incidents={len(incidents)}")
        if (
            self._settings.eval_mode == "latency"
            and self._settings.data_source == "elasticsearch"
            and self._latency_service
        ):
            logger.info(f"[v2/batch] Latency mode — delegating {len(incidents)} incidents to LatencyService")
            return self._latency_service.evaluate_latency_batch(incidents)

        logger.info(
            "[v2/batch] mode=%s source=%s — evaluating %d incidents",
            self._settings.eval_mode,
            self._settings.data_source,
            len(incidents),
        )
        incidents = self.filter_evaluable_v2_incidents(incidents)
        return [
            self.evaluate_incident_v2(
                video=inc["video"],
                timestamp=inc.get("timestamp"),
                start_timestamp=inc.get("start_timestamp"),
                end_timestamp=inc.get("end_timestamp"),
                category=inc.get("category"),
                wait_for_result=wait_for_results,
                runtime_stream=inc.get("runtime_stream"),
            )
            for inc in incidents
        ]

    # -------------------------------------------------------------------------
    # Synthetic eval: BCQ ground truth; same V1 accuracy comparison flow.
    # GT format: { "version", "metadata": { "description" }, "items": [{ "video_id", "question", "answer" }] }
    # Triggered automatically by /eval/v2/* when GT file has 'items' key.
    # -------------------------------------------------------------------------

    def load_synthetic_gt(self) -> Dict[str, Any]:
        """Return the BCQ GT data — same file as gt_file_path, already cached."""
        return self.get_ground_truth_raw()

    @staticmethod
    def _synthetic_lookup_candidates(value: Any) -> set[str]:
        """Return stable lookup keys for GT ids, paths, filenames, and stems."""
        if value is None:
            return set()
        raw = str(value).strip()
        if not raw:
            return set()

        normalized = raw.replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1]
        candidates = {raw, normalized, basename}
        for candidate in list(candidates):
            stem, _ext = os.path.splitext(candidate)
            if stem:
                candidates.add(stem)
                candidates.add(stem.rsplit("/", 1)[-1])
        return {candidate for candidate in candidates if candidate}

    def _synthetic_item_candidates(self, item: Dict[str, Any], id_key: str) -> set[str]:
        candidates: set[str] = set()
        for key in (id_key, "id", "video_id", "video", "sensorId", "sensor_id", "_gt_sensor_id"):
            candidates.update(self._synthetic_lookup_candidates(item.get(key)))
        return candidates

    @staticmethod
    def _synthetic_gt_entries(data: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str, str]:
        """Normalize supported GT shapes without mutating the loaded document."""
        if isinstance(data.get("items"), list):
            items = [dict(item) for item in data["items"] if isinstance(item, dict)]
            return items, "BCQ", "video_id"
        if isinstance(data.get("bcq"), list):
            items = [dict(item) for item in data["bcq"] if isinstance(item, dict)]
            return items, "annotation", "id"

        entries: List[Dict[str, Any]] = []
        sensors = data.get("sensors")
        if isinstance(sensors, dict):
            for sensor_id, sensor_data in sensors.items():
                events = sensor_data.get("events", []) if isinstance(sensor_data, dict) else []
                for event_idx, event in enumerate(events):
                    if not isinstance(event, dict):
                        continue
                    item = dict(event)
                    item.setdefault("video_id", sensor_id)
                    item.setdefault("video", sensor_id)
                    item["_gt_format"] = "events"
                    item["_gt_sensor_id"] = sensor_id
                    item["_gt_event_idx"] = event_idx
                    entries.append(item)

        top_level_events = data.get("events")
        if isinstance(top_level_events, list):
            for event_idx, event in enumerate(top_level_events):
                if not isinstance(event, dict):
                    continue
                identity = next(
                    (
                        event.get(key)
                        for key in ("video_id", "video", "sensorId", "sensor_id")
                        if event.get(key)
                    ),
                    None,
                )
                if identity is None:
                    logger.warning(
                        "[gt_lookup] Ignoring top-level event without video/sensor "
                        "identity at index=%d",
                        event_idx,
                    )
                    continue
                item = dict(event)
                item.setdefault("video_id", identity)
                item["_gt_format"] = "events"
                item["_gt_event_idx"] = event_idx
                entries.append(item)
        return entries, "events", "video_id"

    def get_synthetic_gt_entry(
        self,
        video_id: str,
        category: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return a matching entry from BCQ annotation or events-style GT."""
        items, fmt, id_key = self._synthetic_gt_entries(self.get_ground_truth_raw())
        logger.debug(
            f"[gt_lookup] format={fmt} key={id_key} total_entries={len(items)} "
            f"video_id={video_id} category={category}"
        )

        target_candidates = self._synthetic_lookup_candidates(video_id)
        requested_category = category.strip() if isinstance(category, str) and category.strip() else ""
        requested_category_folded = requested_category.casefold()

        def category_matches(item: Dict[str, Any]) -> bool:
            if not requested_category:
                return True
            if fmt == "events":
                return str(item.get("category") or "").strip().casefold() == requested_category_folded
            return gt_row_slug(item, item.get("_eval_row_idx", 0)) == requested_category

        source_matches = [
            item for item in items
            if target_candidates & self._synthetic_item_candidates(item, id_key)
        ]
        for item in source_matches:
            if category_matches(item):
                logger.debug("[gt_lookup] Source/category match found for video_id=%s", video_id)
                return item
        if source_matches:
            logger.debug("[gt_lookup] Source match found for video_id=%s", video_id)
            return source_matches[0]

        category_matches_only = (
            [item for item in items if category_matches(item)]
            if requested_category
            else []
        )
        if fmt != "events" and category_matches_only:
            logger.debug(
                "[gt_lookup] Category-only match found for video_id=%s category=%s",
                video_id, category,
            )
            return category_matches_only[0]
        if fmt == "events" and len(category_matches_only) == 1:
            logger.debug(
                "[gt_lookup] Unique event category match found for video_id=%s category=%s",
                video_id, category,
            )
            return category_matches_only[0]
        if fmt == "events" and len(category_matches_only) > 1:
            logger.warning(
                "[gt_lookup] Ambiguous event category-only match for video_id=%s category=%s matches=%d",
                video_id, category, len(category_matches_only),
            )

        logger.warning(f"[gt_lookup] No GT entry found for video_id={video_id} (format={fmt})")
        return None

    def _validate_synthetic_alert_config(self, category: str) -> Dict[str, str]:
        """Fail fast unless Alert Bridge can verify the injected category."""

        normalized_category = str(category or "").strip()
        if not normalized_category:
            raise ValueError(
                "Synthetic latency injection requires a non-empty alert category"
            )

        getter = getattr(self._ab_client, "get_alert_config", None)
        if not callable(getter):
            raise RuntimeError(
                "Alert Bridge client does not support alert-config preflight"
            )

        config = getter(normalized_category)
        if not isinstance(config, dict):
            hint = (
                '; for the default Sample Warehouse Ladder dataset use '
                '--alert-category "FOV Count Violation"'
                if normalized_category == self.SYNTHETIC_DEFAULT_OUTPUT_CATEGORY
                else ""
            )
            raise ValueError(
                f"No Alert Bridge prompt config found for alert_type={normalized_category!r}{hint}"
            )

        prompt = config.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(
                f"Alert Bridge config for alert_type={normalized_category!r} has no user prompt"
            )

        output_category = str(config.get("output_category") or "").strip()
        if output_category != self.SYNTHETIC_DEFAULT_OUTPUT_CATEGORY:
            raise ValueError(
                f"Alert Bridge config for alert_type={normalized_category!r} must use "
                f"output_category={self.SYNTHETIC_DEFAULT_OUTPUT_CATEGORY!r}; got {output_category!r}"
            )

        return {
            "alert_type": normalized_category,
            "output_category": output_category,
        }

    def _effective_synthetic_event_duration_ms(self, requested: Optional[int] = None) -> int:
        value = requested
        if value is None:
            value = getattr(self._settings, "synthetic_alert_event_duration_ms", None)
        if value is None:
            value = int(getattr(self._settings, "vst_media_window_seconds", 30) or 30) * 1000
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("event_duration_ms must be a positive integer") from exc
        if isinstance(value, bool) or parsed <= 0 or str(value).strip() != str(parsed):
            raise ValueError("event_duration_ms must be a positive integer")
        return parsed

    def _completed_synthetic_event_window(
        self,
        timestamp: str,
        end: Optional[str],
        event_duration_ms: int,
        *,
        publication_time: Optional[datetime] = None,
    ) -> tuple[str, str]:
        """Return a completed event window that is valid at publication time.

        When callers omit ``end``, ``timestamp`` represents the scheduled
        publication/completion time. The event duration is subtracted from
        that time so Alert Bridge never receives an event whose declared end
        is in the future. Explicit start/end windows are preserved after
        validation.
        """
        event_start_dt = self._parse_iso_timestamp(timestamp)
        if not event_start_dt:
            raise ValueError(f"Invalid synthetic incident timestamp: {timestamp!r}")
        if event_start_dt.tzinfo is None:
            event_start_dt = event_start_dt.replace(tzinfo=timezone.utc)
        event_start_dt = event_start_dt.astimezone(timezone.utc)

        if end is None:
            event_end_dt = event_start_dt
            event_start_dt = event_end_dt - timedelta(milliseconds=event_duration_ms)
        else:
            event_end_dt = self._parse_iso_timestamp(end)
            if not event_end_dt:
                raise ValueError(f"Invalid synthetic incident end timestamp: {end!r}")
            if event_end_dt.tzinfo is None:
                event_end_dt = event_end_dt.replace(tzinfo=timezone.utc)
            event_end_dt = event_end_dt.astimezone(timezone.utc)

        if event_end_dt <= event_start_dt:
            raise ValueError("Synthetic incident end must be later than its start")

        published_at = publication_time or datetime.now(timezone.utc)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        published_at = published_at.astimezone(timezone.utc)
        # Allow a small amount of clock/serialization skew, but never an event
        # duration-sized future end (the measurement-correctness defect).
        if event_end_dt > published_at + timedelta(seconds=1):
            raise ValueError(
                "Synthetic incident end must not be later than publication time"
            )

        return self._to_utc_z(event_start_dt), self._to_utc_z(event_end_dt)

    def inject_synthetic_latency_incidents(
        self,
        source_video_id: str,
        sensor_ids: List[str],
        timestamp: str,
        end: Optional[str] = None,
        category: Optional[str] = None,
        submission_mode: Optional[str] = None,
        runtime_streams: Optional[List[Dict[str, Any]]] = None,
        vst_readiness: Optional[Dict[str, Any]] = None,
        event_duration_ms: Optional[int] = None,
        gt_entry: Any = _SYNTHETIC_GT_UNSET,
    ) -> Dict[str, Any]:
        """Submit synthetic events for latency collection.

        ``source_video_id`` selects the BCQ/annotation GT row that provides the
        prompt and expected answer. ``sensor_ids`` are the requested runtime
        stream identifiers. For message-bus/incident-api synthetic latency, the
        evaluator resolves those identifiers to VST ``streamId`` values when VST
        readiness data is available, and publishes that UUID as NvSchema
        ``sensorId`` so Alert Agent can resolve media through VST storage.

        Submission modes:
        * ``message_bus`` (default): publish NvSchema incidents directly to
          Kafka so Alert Agent emits normal realtime latency metrics.
        * ``incident_api``: use the legacy ``/api/v1/incidents`` endpoint.
        * ``ondemand_api``: use ``/api/v1/verification/ondemand`` with media
          URL resolution for direct-media verification.
        """
        source = (source_video_id or "").strip()
        cleaned_sensor_ids = [
            str(sensor).strip() for sensor in sensor_ids or []
            if str(sensor).strip()
        ]
        if not source:
            raise ValueError("source_video_id is required for synthetic latency injection")
        if not cleaned_sensor_ids and not runtime_streams:
            raise ValueError("At least one sensor_id is required for synthetic latency injection")
        if not timestamp:
            raise ValueError("timestamp is required for synthetic latency injection")

        effective_event_duration_ms = self._effective_synthetic_event_duration_ms(event_duration_ms)
        timestamp, end = self._completed_synthetic_event_window(
            timestamp,
            end,
            effective_event_duration_ms,
        )

        if gt_entry is _SYNTHETIC_GT_UNSET:
            gt_entry = self.get_synthetic_gt_entry(source, category=category)
            if not gt_entry:
                logger.warning(
                    "[synthetic/latency] No ground-truth entry found for "
                    "source_video_id=%r; continuing with configured category",
                    source,
                )

        mode = self._normalize_synthetic_alert_submission_mode(
            submission_mode or getattr(self._settings, "synthetic_alert_submission_mode", "message_bus")
        )
        runtime_streams_resolved, vst_readiness = self._resolve_runtime_streams_for_submission(
            cleaned_sensor_ids,
            mode,
            runtime_streams=runtime_streams,
            vst_readiness=vst_readiness,
        )
        if not runtime_streams_resolved:
            raise ValueError("At least one sensor_id is required for synthetic latency injection")

        use_category = self._resolve_category(source, category, gt_entry)
        results: List[Dict[str, Any]] = []
        submitted = 0
        failed = 0

        logger.info(
            "[synthetic/latency] Submitting %d runtime sensor incident(s) "
            "from source_video_id=%s category=%s submission_mode=%s stream_identity_mode=%s",
            len(runtime_streams_resolved), source, use_category, mode,
            self._stream_identity_mode(runtime_streams_resolved),
        )
        require_vst_media = (
            mode == "ondemand_api"
            and bool(str(getattr(self._settings, "vst_base_url", "") or "").strip())
        )

        for stream in runtime_streams_resolved:
            requested_sensor_id = stream["requested_sensor_id"]
            submitted_sensor_id = stream["submitted_sensor_id"]
            vst_stream_id = stream.get("vst_stream_id") or ""
            vst_stream_name = stream.get("vst_stream_name") or ""
            result = {
                "sensor_id": requested_sensor_id,
                "requested_sensor_id": requested_sensor_id,
                "submitted_sensor_id": submitted_sensor_id,
                "vst_stream_id": vst_stream_id,
                "vst_stream_name": vst_stream_name,
                "stream_identity_mode": stream.get("stream_identity_mode", "provided_sensor_id"),
                "source_video_id": source,
                "timestamp": timestamp,
                "end": end,
                "status": "processing",
                "category": use_category,
                "submission_mode": mode,
                "gt_question": gt_entry.get("question") if gt_entry else None,
                "gt_answer": gt_entry.get("answer") if gt_entry else None,
            }

            incident_gt_entry = gt_entry
            include_media_urls = mode == "ondemand_api"
            if mode == "ondemand_api":
                vst_media_url = self._resolve_vst_storage_media_url(requested_sensor_id, timestamp, end)
                if require_vst_media and not vst_media_url:
                    error = (
                        "Failed to resolve VST media URL; refusing MEDIA_BASE_URL "
                        "fallback for synthetic latency"
                    )
                    logger.error(
                        "[synthetic/latency] %s sensor_id=%s source_video_id=%s",
                        error,
                        requested_sensor_id,
                        source,
                    )
                    result["status"] = "failed"
                    result["error"] = error
                    failed += 1
                    results.append(result)
                    continue

                media_source = "vst_storage" if vst_media_url else "legacy_media_base"
                logger.info(
                    "[synthetic/latency] media_source=%s sensor_id=%s source_video_id=%s",
                    media_source,
                    requested_sensor_id,
                    source,
                )
                incident_gt_entry = self._gt_entry_with_media_url(gt_entry, vst_media_url)

            incident = self.build_incident(
                video=requested_sensor_id,
                timestamp=timestamp,
                end=end,
                category=use_category,
                gt_entry=incident_gt_entry,
                media_video=source,
                include_media_urls=include_media_urls,
                alert_sensor_id=submitted_sensor_id,
                runtime_stream_name=vst_stream_name or requested_sensor_id,
                vst_stream_id=vst_stream_id,
                source_video_id=source,
            )
            try:
                submit_response = self._submit_synthetic_latency_incident(incident, mode)
                result["status"] = "submitted"
                result["incident_id"] = incident["id"]
                result["correlation_id"] = (
                    submit_response.get("correlationId")
                    or submit_response.get("id")
                    or incident["id"]
                )
                submitted += 1
            except UnknownCategoryError as exc:
                logger.warning(
                    "[synthetic/latency] Unknown category=%s for sensor_id=%s; retrying seed. %s",
                    use_category, requested_sensor_id, exc,
                )
                retry_response = self._seed_from_gt_and_retry(use_category, gt_entry, incident)
                if retry_response is not None:
                    result["status"] = "submitted"
                    result["incident_id"] = incident["id"]
                    result["correlation_id"] = retry_response.get("correlationId") or incident["id"]
                    submitted += 1
                else:
                    result["status"] = "failed"
                    result["error"] = str(exc)
                    failed += 1
            except AlertBridgeError as exc:
                logger.error(
                    "[synthetic/latency] Submit failed for sensor_id=%s submitted_sensor_id=%s "
                    "source_video_id=%s mode=%s: %s",
                    requested_sensor_id, submitted_sensor_id, source, mode, exc,
                )
                result["status"] = "failed"
                result["error"] = str(exc)
                failed += 1
            results.append(result)

        if failed == 0:
            status = "submitted"
        elif submitted > 0:
            status = "partial_failure"
        else:
            status = "failed"

        return {
            "status": status,
            "source_video_id": source,
            "category": use_category,
            "submission_mode": mode,
            "synthetic_alert_event_duration_ms": effective_event_duration_ms,
            "event_window_mode": "completed_at_submission",
            "stream_identity_mode": self._stream_identity_mode(runtime_streams_resolved),
            "sensor_ids_requested": [stream["requested_sensor_id"] for stream in runtime_streams_resolved],
            "sensor_ids_submitted": [stream["submitted_sensor_id"] for stream in runtime_streams_resolved],
            "runtime_streams": runtime_streams_resolved,
            "vst_readiness": vst_readiness,
            "requested": len(runtime_streams_resolved),
            "submitted": submitted,
            "failed": failed,
            "results": results,
        }

    def inject_synthetic_latency_incidents_over_window(
        self,
        *,
        source_video_id: str,
        sensor_ids: List[str],
        start_timestamp: str,
        latency_duration: Optional[str] = None,
        duration_seconds: Optional[int] = None,
        alerts_per_minute: Optional[float] = None,
        event_duration_ms: Optional[int] = None,
        category: Optional[str] = None,
        submission_mode: Optional[str] = None,
        runtime_streams: Optional[List[Dict[str, Any]]] = None,
        sleep_fn=None,
        now_fn=None,
    ) -> Dict[str, Any]:
        """Submit synthetic events at a steady global rate.

        This is used by latency benchmarks that need synthetic Alert Bridge
        traffic throughout the whole measurement window. It reuses the existing
        one-shot injection method for each scheduled event so submission-mode
        routing, category seeding, and request shape stay in one place.
        """
        cleaned_sensor_ids = [
            str(sensor).strip() for sensor in sensor_ids or []
            if str(sensor).strip()
        ]
        if not cleaned_sensor_ids and not runtime_streams:
            raise ValueError("At least one sensor_id is required for synthetic latency injection")

        start_dt = self._parse_iso_timestamp(start_timestamp)
        if not start_dt:
            raise ValueError(f"Invalid synthetic latency start timestamp: {start_timestamp!r}")
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        start_dt = start_dt.astimezone(timezone.utc)

        resolved_duration = duration_seconds
        if resolved_duration is None:
            resolved_duration = self._parse_duration_seconds(
                latency_duration or getattr(self._settings, "latency_duration", "")
            )
        if not resolved_duration or resolved_duration <= 0:
            raise ValueError(
                "A positive duration_seconds or latency_duration is required "
                "for continuous synthetic latency injection"
            )

        if alerts_per_minute is None:
            alerts_per_minute = getattr(self._settings, "synthetic_alerts_per_minute", None)
        if alerts_per_minute is None:
            alerts_per_minute = float(len(cleaned_sensor_ids))
        try:
            rate_per_minute = float(alerts_per_minute)
        except (TypeError, ValueError) as exc:
            raise ValueError("alerts_per_minute must be a positive number") from exc
        if rate_per_minute <= 0:
            raise ValueError("alerts_per_minute must be a positive number")

        total_target = max(1, int(rate_per_minute * resolved_duration / 60.0))
        end_dt = start_dt + timedelta(seconds=resolved_duration)
        sleep_fn = sleep_fn or time.sleep
        now_fn = now_fn or (lambda: datetime.now(timezone.utc))

        mode = self._normalize_synthetic_alert_submission_mode(
            submission_mode or getattr(self._settings, "synthetic_alert_submission_mode", "message_bus")
        )
        gt_entry = self.get_synthetic_gt_entry(source_video_id, category=category)
        if not gt_entry:
            logger.warning(
                "[synthetic/latency] No ground-truth entry found for "
                "source_video_id=%r; continuing with configured category",
                source_video_id,
            )
        category = self._resolve_category(source_video_id, category, gt_entry)
        alert_config_preflight = self._validate_synthetic_alert_config(category)

        runtime_streams_resolved, vst_readiness = self._resolve_runtime_streams_for_submission(
            cleaned_sensor_ids,
            mode,
            runtime_streams=runtime_streams,
            wait_for_vst_readiness=True,
        )
        if not runtime_streams_resolved:
            raise ValueError("At least one sensor_id is required for synthetic latency injection")
        vst_readiness = vst_readiness or {
            "enabled": False,
            "ready": True,
            "expected": [stream["requested_sensor_id"] for stream in runtime_streams_resolved],
            "resolved_streams": runtime_streams_resolved,
            "sensor_ids_requested": [stream["requested_sensor_id"] for stream in runtime_streams_resolved],
            "sensor_ids_submitted": [stream["submitted_sensor_id"] for stream in runtime_streams_resolved],
            "stream_identity_mode": self._stream_identity_mode(runtime_streams_resolved),
        }
        cleaned_sensor_ids = [stream["requested_sensor_id"] for stream in runtime_streams_resolved]

        submitted = 0
        failed = 0
        attempted = 0
        attempted_by_sensor: Dict[str, int] = {sensor_id: 0 for sensor_id in cleaned_sensor_ids}
        submitted_by_sensor: Dict[str, int] = {sensor_id: 0 for sensor_id in cleaned_sensor_ids}
        failed_by_sensor: Dict[str, int] = {sensor_id: 0 for sensor_id in cleaned_sensor_ids}
        sample_results: List[Dict[str, Any]] = []
        max_samples = 100
        effective_event_duration_ms = self._effective_synthetic_event_duration_ms(event_duration_ms)
        last_category = category

        if vst_readiness.get("enabled"):
            ready_now = now_fn()
            if isinstance(ready_now, datetime):
                ready_dt = ready_now
            else:
                ready_dt = self._parse_iso_timestamp(str(ready_now)) or datetime.now(timezone.utc)
            if ready_dt.tzinfo is None:
                ready_dt = ready_dt.replace(tzinfo=timezone.utc)
            ready_dt = ready_dt.astimezone(timezone.utc)
            if ready_dt > start_dt:
                logger.info(
                    "[synthetic/latency] VST readiness completed after requested start; "
                    "shifting latency window start from %s to %s",
                    self._to_utc_z(start_dt),
                    self._to_utc_z(ready_dt),
                )
                start_dt = ready_dt
                end_dt = start_dt + timedelta(seconds=resolved_duration)

        logger.info(
            "[synthetic/latency] Continuous injection start source_video_id=%s "
            "sensors=%d rate_per_minute=%s duration_seconds=%d target=%d "
            "distribution=round_robin submission_mode=%s",
            source_video_id,
            len(cleaned_sensor_ids),
            rate_per_minute,
            resolved_duration,
            total_target,
            mode,
        )

        while attempted < total_target:
            now_value = now_fn()
            if isinstance(now_value, datetime):
                now_dt = now_value
            else:
                now_dt = self._parse_iso_timestamp(str(now_value)) or datetime.now(timezone.utc)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            now_dt = now_dt.astimezone(timezone.utc)

            if now_dt >= end_dt and attempted > 0:
                break

            elapsed_seconds = max(0.0, min(float(resolved_duration), (now_dt - start_dt).total_seconds()))
            target_now = min(total_target, max(1, int(rate_per_minute * elapsed_seconds / 60.0) + 1))
            if target_now <= attempted:
                next_due_seconds = ((attempted + 1) * 60.0 / rate_per_minute)
                sleep_seconds = max(0.1, min(1.0, next_due_seconds - elapsed_seconds))
                sleep_fn(sleep_seconds)
                continue

            while attempted < target_now and attempted < total_target:
                stream = runtime_streams_resolved[attempted % len(runtime_streams_resolved)]
                sensor_id = stream["requested_sensor_id"]
                attempted_by_sensor[sensor_id] += 1
                event_end_dt = start_dt + timedelta(seconds=min(float(resolved_duration), elapsed_seconds))
                event_start_dt = event_end_dt - timedelta(milliseconds=effective_event_duration_ms)
                event_ts = self._to_utc_z(event_start_dt)
                event_end_ts = self._to_utc_z(event_end_dt)
                result = self.inject_synthetic_latency_incidents(
                    source_video_id=source_video_id,
                    sensor_ids=[sensor_id],
                    timestamp=event_ts,
                    end=event_end_ts,
                    category=category,
                    submission_mode=mode,
                    runtime_streams=[stream],
                    vst_readiness=vst_readiness,
                    event_duration_ms=effective_event_duration_ms,
                    gt_entry=gt_entry,
                )
                submitted_count = int(result.get("submitted", 0) or 0)
                failed_count = int(result.get("failed", 0) or 0)
                submitted += submitted_count
                failed += failed_count
                submitted_by_sensor[sensor_id] += submitted_count
                failed_by_sensor[sensor_id] += failed_count
                last_category = result.get("category") or last_category
                for item in result.get("results") or []:
                    if len(sample_results) < max_samples:
                        sample_results.append(item)
                attempted += 1

        missed = max(0, total_target - attempted)
        if failed == 0 and missed == 0 and submitted > 0:
            status = "submitted"
        elif submitted > 0:
            status = "partial_failure"
        else:
            status = "failed"

        summary = {
            "status": status,
            "source_video_id": str(source_video_id or "").strip(),
            "category": last_category,
            "submission_mode": mode,
            "distribution_strategy": "round_robin",
            "sensor_count": len(cleaned_sensor_ids),
            "stream_identity_mode": self._stream_identity_mode(runtime_streams_resolved),
            "sensor_ids_requested": [stream["requested_sensor_id"] for stream in runtime_streams_resolved],
            "sensor_ids_submitted": [stream["submitted_sensor_id"] for stream in runtime_streams_resolved],
            "runtime_streams": runtime_streams_resolved,
            "alerts_per_minute": rate_per_minute,
            "synthetic_alert_event_duration_ms": effective_event_duration_ms,
            "duration_seconds": resolved_duration,
            "latency_window_start_ts": self._to_utc_z(start_dt),
            "latency_window_end_ts": self._to_utc_z(end_dt),
            "target_submissions": total_target,
            "attempted": attempted,
            "submitted": submitted,
            "failed": failed,
            "missed": missed,
            "attempted_by_sensor": attempted_by_sensor,
            "submissions_by_sensor": submitted_by_sensor,
            "failed_by_sensor": failed_by_sensor,
            "sample_results": sample_results,
            "vst_readiness": vst_readiness,
            "alert_config_preflight": alert_config_preflight,
            "event_window_mode": "completed_at_submission",
        }
        logger.info("[synthetic/latency] Continuous injection complete: %s", summary)
        return summary

    def evaluate_synthetic_incident(
        self,
        video_id: str,
        timestamp: str,
        end: Optional[str] = None,
        category: Optional[str] = None,
        wait_for_result: bool = True
    ) -> Dict[str, Any]:
        """
        Evaluate a single incident using synthetic (BCQ) ground truth.

        Flow is identical to V1 accuracy:
        1. Load GT item from BCQ file by video_id
        2. Configure prompts in Redis for Alert Bridge
        3. Submit incident to Alert Bridge
        4. Poll Elasticsearch for VLM result
        5. Compare verdict (confirmed/rejected) with GT answer (Yes/No)
        """
        logger.info(f"[synthetic/incident] Start: video_id={video_id} timestamp={timestamp} end={end} wait={wait_for_result}")
        result = {
            "video": video_id,
            "timestamp": timestamp,
            "status": "processing",
        }

        # Pass ``category`` so duplicate-id BCQ rows resolve to the row whose
        # seed slug matches, matching the V1 dataframe fix in
        # ``get_gt_entry``. Falls back to the existing exact/suffix match
        # when no category is supplied or no row's slug agrees with it.
        gt_entry = self.get_synthetic_gt_entry(video_id, category=category)
        system_prompt = gt_entry.get('system_prompt', '') if gt_entry else ''

        if gt_entry:
            result["gt_question"] = gt_entry.get('question')
            result["gt_answer"] = gt_entry.get('answer')
            result["gt_system_prompt"] = system_prompt
            logger.debug(f"[synthetic/incident] GT entry: answer={gt_entry.get('answer')} question_len={len(gt_entry.get('question', ''))}")
        else:
            logger.warning(f"[synthetic/incident] No GT entry for video_id={video_id} — will proceed without ground truth")

        use_category = self._resolve_category(video_id, category, gt_entry)

        incident = self.build_incident(
            video=video_id,
            timestamp=timestamp,
            end=end,
            category=use_category,
            gt_entry=gt_entry,
        )

        logger.debug(
            "[synthetic/incident] Built ondemand request id=%s category=%s media_urls=%s",
            incident.get('id'), use_category, incident["info"]["media_urls"],
        )
        try:
            submit_response = self._ab_client.submit_ondemand_verification(incident)
            correlation_id = submit_response.get("correlationId") or incident["id"]
            logger.info(
                "[synthetic/incident] Submitted: video=%s correlationId=%s",
                video_id, correlation_id,
            )
            result["incident_id"] = incident["id"]
            result["correlation_id"] = correlation_id
        except UnknownCategoryError as e:
            logger.warning(
                "[synthetic/incident] Unknown category=%s for video=%s — attempting seed-on-demand retry. %s",
                use_category, video_id, e,
            )
            retry_response = self._seed_from_gt_and_retry(use_category, gt_entry, incident)
            if retry_response is not None:
                correlation_id = retry_response.get("correlationId") or incident["id"]
                logger.info(
                    "[synthetic/incident] Submitted (after seed): video=%s correlationId=%s",
                    video_id, correlation_id,
                )
                result["incident_id"] = incident["id"]
                result["correlation_id"] = correlation_id
            else:
                result["status"] = "failed"
                result["error"] = (
                    f"Alert Bridge has no prompt config for category='{use_category}' "
                    f"and seed-on-demand retry failed. Check that the GT entry has "
                    f"`question`/`prompt` and that AB is reachable. Original error: {e}"
                )
                return result
        except AlertBridgeError as e:
            logger.error(f"[synthetic/incident] Submit failed: video={video_id} error={e}")
            result["status"] = "failed"
            result["error"] = f"Failed to submit incident: {e}"
            return result

        if not wait_for_result:
            result["status"] = "submitted"
            logger.info(f"[synthetic/incident] Returning without waiting for result: video={video_id}")
            return result

        logger.info(f"[synthetic/incident] Polling ES for VLM result: video={video_id} timestamp={timestamp}")
        try:
            es_result = self._es_client.poll_for_result(video_id, timestamp)
            if not es_result:
                logger.warning(f"[synthetic/incident] ES poll timed out: video={video_id}")
                result["status"] = "timeout"
                result["error"] = "Timeout waiting for VLM result"
                return result

            info_data = es_result.get("info", {})
            result["verdict"] = info_data.get("verdict")
            result["reasoning"] = info_data.get("reasoning")
            result["verification_code"] = info_data.get("verificationResponseCode") or info_data.get("verification_response_code")
            result["verification_status"] = info_data.get("verificationResponseStatus") or info_data.get("verification_response_status")
            result["raw_response"] = es_result
            result["status"] = "success"
            logger.info(f"[synthetic/incident] VLM result: video={video_id} verdict={result['verdict']} code={result['verification_code']}")
            logger.debug(f"[synthetic/incident] Reasoning: {result['reasoning']}")
        except ElasticsearchError as e:
            logger.error(f"[synthetic/incident] ES error: video={video_id} error={e}")
            result["status"] = "error"
            result["error"] = f"Elasticsearch query failed: {e}"
            return result

        result = self._compare_with_gt(result, gt_entry)
        logger.info(f"[synthetic/incident] Comparison: video={video_id} match={result.get('match')} comparison_status={result.get('comparison_status')}")
        return result

    def evaluate_synthetic_batch(
        self,
        incidents: List[Dict[str, Any]],
        wait_for_results: bool = True
    ) -> List[Dict[str, Any]]:
        """Evaluate a batch of incidents using synthetic (BCQ) ground truth."""
        logger.info(f"[synthetic/batch] Starting batch: {len(incidents)} incidents wait={wait_for_results}")
        results = []
        for i, inc in enumerate(incidents):
            logger.debug(f"[synthetic/batch] Processing {i+1}/{len(incidents)}: video_id={inc.get('video_id')}")
            results.append(self.evaluate_synthetic_incident(
                video_id=inc['video_id'],
                timestamp=inc['timestamp'],
                end=inc.get('end'),
                category=inc.get('category'),
                wait_for_result=wait_for_results,
            ))
        logger.info(f"[synthetic/batch] Done: {len(results)} results")
        return results

    def calculate_summary_v2(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summary for eval v2.

        BCQ results (verdict field present) -> V1 accuracy summary.
        eval_mode='latency'  -> aggregate latency stats across sensors.
        eval_mode='accuracy' -> accuracy: passed = count match, failed = count mismatch.
        """
        if results and 'verdict' in results[0]:
            logger.debug(f"[summary_v2] BCQ results detected — using V1 accuracy summary for {len(results)} results")
            return self.calculate_summary(results)

        if (
            self._settings.eval_mode == "latency"
            and self._settings.data_source == "elasticsearch"
            and self._latency_service
        ):
            return self._latency_service.calculate_latency_summary(results)
        if self._settings.eval_mode == "latency" and self._settings.data_source == "prometheus":
            event_count = 0
            reliability_totals = {
                "total_events": 0,
                "confirmed_events": 0,
                "rejected_events": 0,
                "verification_failed_events": 0,
                "unknown_events": 0,
                "events_after_dedup": 0,
                "dropped_events": 0,
                "skipped_confirmed_events": 0,
                "verification_failures": 0,
            }
            summary: Dict[str, Any] = {
                "total_sensors": len(results),
                "total_hits": 0,
                "total_usable": 0,
            }
            for result in results:
                reliability = result.get("reliability") or {}
                for key in reliability_totals:
                    reliability_totals[key] += int(float(reliability.get(key) or 0))
            for stage in self.PROMETHEUS_LATENCY_STAGES:
                weighted_sum = 0.0
                total_count = 0
                for result in results:
                    stage_stats = (result.get("stats") or {}).get(stage) or {}
                    count = int(float(stage_stats.get("count") or 0))
                    avg = stage_stats.get("avg")
                    if stage == "e2e":
                        event_count += count
                    if count > 0 and avg is not None:
                        weighted_sum += float(avg) * count
                        total_count += count
                avg_value = (weighted_sum / total_count) if total_count > 0 else None
                summary[stage] = {
                    "avg": round(avg_value, 4) if avg_value is not None else None,
                    "p50": None,
                    "p90": None,
                    "p99": None,
                    "max": None,
                    "n": total_count,
                }
            if event_count == 0 and reliability_totals["total_events"] > 0:
                event_count = reliability_totals["total_events"]
            summary["total_hits"] = event_count
            summary["total_usable"] = event_count
            summary["reliability"] = reliability_totals
            return summary

        total = len(results)
        passed = sum(1 for r in results if r.get("pass") is True)
        failed = sum(1 for r in results if r.get("pass") is False)
        errors = sum(1 for r in results if r.get("status") in ["failed", "error"])
        comparable = total
        accuracy = f"{(passed / comparable * 100):.2f}%" if comparable > 0 else "N/A"
        logger.info(f"[summary_v2] Accuracy mode: total={total} passed={passed} failed={failed} errors={errors} accuracy={accuracy}")
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "accuracy": accuracy,
        }
