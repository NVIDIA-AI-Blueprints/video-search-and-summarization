# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""In-process alert-state handler (Redis-free).

Historically these dedup / filter primitives lived in Redis so multiple
Alert MS pods could share them. That coupling is unnecessary:
``mdx-incidents`` is partitioned by ``sensorId`` (set upstream in
behavior-analytics) and every dedup cohort key below is prefixed with
``sensorId``. Kafka therefore routes every event for a cohort to the
same partition, and each Alert MS consumer owns a fixed set of
partitions — so a given cohort is only ever seen by one consumer
instance. No two pods need to share this state, which means it can be
kept **in-process** per consumer:

* **TTL dedup** (system-time collisions)
* **End-time delta filter** (record-time change threshold)
* **VLM rate limit** (disabled by default)

The only primitive that must survive a pod restart / partition
reassignment is **confirmed-verdict protection** (do not re-verify an
incident whose verdict was already confirmed). That is backed by
Elasticsearch — a store Alert MS already talks to — so the Redis pod can
be removed entirely without adding a new dependency.

Multi-replica correctness: when a pod restarts or Kafka rebalances,
the pod that takes over a partition starts with empty in-process state
and rebuilds it as new events arrive. Dedup/delta/rate-limit are
best-effort false-positive suppressors — a cold cache after a restart
means at worst a small window of re-processed events, never data loss.
Verdict protection is the one guarantee that must persist, and it does
(ES).

The class is named :class:`DedupStateHandler`; ``clients.redis_handler``
re-exports it as ``RedisHandler`` for backward-compatible imports.
"""

import hashlib
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import yaml

# Observability metrics. The recorder no-ops when PROMETHEUS_METRICS_ENABLED
# is off, so importing it here is cheap; guard the import itself so a minimal
# environment without the metrics package cannot break the dedup hot path.
try:  # pragma: no cover - exercised indirectly
    from metrics import recorder as _metrics
except Exception:  # pragma: no cover
    _metrics = None


def _metric(name: str, *args, **kwargs) -> None:
    """Call recorder helper ``name`` if the metrics module is available."""
    if _metrics is None:
        return
    fn = getattr(_metrics, name, None)
    if fn is None:
        return
    try:
        fn(*args, **kwargs)
    except Exception:  # pragma: no cover - metrics must never break the pipeline
        pass


class _TTLCache:
    """Minimal thread-safe in-process cache with per-key TTL.

    Kept intentionally small (no external dependency): the alert dedup
    hot path only needs ``get`` / ``set`` / ``set_if_absent`` with a
    per-entry expiry. Expired entries are dropped lazily on access and
    swept periodically on write so memory stays bounded under churn.

    ``clock`` is injectable so tests can advance time deterministically.
    """

    def __init__(self, clock=time.monotonic, purge_interval: float = 30.0, name: Optional[str] = None):
        self._data: Dict[str, tuple[Any, Optional[float]]] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._purge_interval = purge_interval
        self._last_purge = 0.0
        # Label for the occupancy gauge / eviction counter. One of
        # the closed enum in ``metrics.recorder.DEDUP_CACHE_STORES``.
        self._name = name

    def _expired(self, expire_at: Optional[float], now: float) -> bool:
        return expire_at is not None and expire_at <= now

    def _publish_occupancy_locked(self) -> None:
        # Report the resident entry count in O(1). This must stay O(1): it is
        # called on every cache write (the per-event hot path), so an O(n)
        # live-count scan here would make dedup O(n^2) under load. The 30s
        # sweep drops expired entries and republishes, so the gauge tracks
        # the live count closely between sweeps (it may transiently include
        # expired-but-unswept entries — i.e. resident, not strictly live).
        if self._name:
            _metric("set_cache_occupancy", self._name, len(self._data))

    def _maybe_purge_locked(self, now: float) -> None:
        if now - self._last_purge < self._purge_interval:
            return
        self._last_purge = now
        stale = [
            key
            for key, (_, expire_at) in self._data.items()
            if self._expired(expire_at, now)
        ]
        for key in stale:
            del self._data[key]
        if stale and self._name:
            _metric("inc_cache_evictions", self._name, "sweep", len(stale))
        self._publish_occupancy_locked()

    def get(self, key: str) -> Any:
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expire_at = item
            if self._expired(expire_at, now):
                del self._data[key]
                if self._name:
                    _metric("inc_cache_evictions", self._name, "lazy", 1)
                    self._publish_occupancy_locked()
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        now = self._clock()
        expire_at = self._expire_at(now, ttl)
        with self._lock:
            self._maybe_purge_locked(now)
            self._data[key] = (value, expire_at)
            self._publish_occupancy_locked()

    def set_if_absent(self, key: str, value: Any, ttl: Optional[float] = None) -> bool:
        """Atomic create. Returns ``True`` when the key was (re)created,
        ``False`` when a live (non-expired) entry already exists — the
        in-process equivalent of Redis ``SET key val EX ttl NX``.
        """
        now = self._clock()
        with self._lock:
            self._maybe_purge_locked(now)
            item = self._data.get(key)
            if item is not None and not self._expired(item[1], now):
                return False
            self._data[key] = (value, self._expire_at(now, ttl))
            self._publish_occupancy_locked()
            return True

    @staticmethod
    def _expire_at(now: float, ttl: Optional[float]) -> Optional[float]:
        """Resolve an absolute expiry from ``now`` + ``ttl``.

        A positive ``ttl`` expires ``ttl`` seconds from now. ``None`` means
        "never expire". A non-positive ``ttl`` (0 / negative) is treated as
        "already expired" rather than "never expire" — the DedupStateHandler
        rejects such TTLs at construction, but this keeps the cache itself
        from silently turning a mistaken ``0`` into a permanent entry.
        """
        if ttl is None:
            return None
        if ttl <= 0:
            return now  # expired immediately
        return now + ttl

    def __len__(self) -> int:
        now = self._clock()
        with self._lock:
            return sum(
                1
                for _, expire_at in self._data.values()
                if not self._expired(expire_at, now)
            )


class DedupStateHandler:
    """In-process dedup/filter state + ES-backed verdict protection.

    Method surface is deliberately identical to the previous Redis-backed
    handler so orchestrator / sink call sites are unchanged.
    """

    # ES index (under the persistence index_prefix) that holds confirmed
    # verdict markers. Chosen so it is co-located with the other Alert MS
    # indices and can be governed by the same ILM policy.
    _VERDICT_INDEX_SUFFIX = "confirmed-verdicts"

    def __init__(self, config_file="config.yaml", rate_limit=300, clock=time.monotonic, es_client=None):
        self.logger = logging.getLogger(self.__class__.__name__)
        # Optional: verbose per-key dedup logs only when explicitly enabled
        self._dedup_verbose = os.getenv("LOG_VERBOSE_DEDUP", "false").lower() in ("1", "true", "yes")

        normalized_path = os.path.normpath(config_file)
        if not normalized_path.lower().endswith((".yaml", ".yml")):
            raise ValueError(f"Config file must be a YAML file: {normalized_path}")
        if not os.path.isfile(normalized_path):
            raise FileNotFoundError(f"Config file not found: {normalized_path}")

        with open(normalized_path, 'r') as file:
            config = yaml.safe_load(file) or {}
        self._app_config = config

        # Dedup / filter tuning lives under ``alert_agent.event_filters``.
        # A deprecated fallback to the historical
        # ``event_bridge.redis_source`` section is kept so pre-existing
        # config files keep working; a warning is logged when it is used.
        alert_agent_cfg = config.get('alert_agent', {}) or {}
        state_config = alert_agent_cfg.get('event_filters')
        if not state_config:
            legacy = config.get('redis') or config.get('event_bridge', {}).get('redis_source')
            if legacy:
                self.logger.warning(
                    "Reading dedup/filter tuning from the deprecated "
                    "'event_bridge.redis_source' section; move these keys "
                    "under 'alert_agent.event_filters'."
                )
                state_config = legacy
            else:
                state_config = {}

        # TTLs must be finite positive seconds. A mistaken ``0``/negative
        # would otherwise translate to a non-expiring cache entry that
        # permanently suppresses a cohort and grows memory unbounded, so
        # reject it loudly at construction instead.
        self._rate_limit_ttl = self._validate_ttl(rate_limit, "rate_limit")
        self._incident_end_categories = self._load_incident_end_categories(state_config)
        self._dedup_ttl_seconds = self._validate_ttl(
            state_config.get('dedup_ttl_seconds', 300), "dedup_ttl_seconds"
        )

        # Confirmed verdict protection config (ES-backed).
        _protect_cfg = state_config.get('protect_confirmed_verdicts', {})
        self._protect_confirmed_enabled = _protect_cfg.get('enabled', False)
        self._protect_confirmed_ttl = self._validate_ttl(
            _protect_cfg.get('ttl_seconds', 600), "protect_confirmed_verdicts.ttl_seconds"
        )
        # Cooldown before a failed verdict-ES client construction is retried
        # (seconds). Replaces the old permanent-disable behaviour. Validated
        # as a finite positive number so a quoted/zero YAML value cannot make
        # the fail-open path raise (``time.time() + backoff``) or disable the
        # bounded cooldown.
        self._es_backoff_seconds = self._validate_ttl(
            _protect_cfg.get('es_retry_backoff_seconds', 30), "es_retry_backoff_seconds"
        )

        # End time delta filter config.
        _delta_cfg = state_config.get('end_time_delta_filter', {})
        self._end_delta_enabled = _delta_cfg.get('enabled', False)
        self._end_delta_threshold = _delta_cfg.get('threshold_seconds', 5)
        self._end_delta_ttl = self._validate_ttl(
            _delta_cfg.get('ttl_seconds', 3600), "end_time_delta_filter.ttl_seconds"
        )

        # In-process state (per consumer). Dedup + rate-limit share one
        # keyspace to preserve the exact single-keyspace semantics of the
        # previous Redis ``SET NX`` path.
        self._dedup_cache = _TTLCache(clock, name="dedup")
        self._enddelta_cache = _TTLCache(clock, name="enddelta")

        # ES client for verdict protection. An externally-managed client
        # (the app's configured persistence client, carrying auth/TLS) may be
        # injected via the constructor or ``set_es_client``; otherwise one is
        # built lazily from config. ``_es_retry_after`` implements bounded
        # backoff so a transient ES failure no longer disables verdict
        # protection for the whole process lifetime.
        self._injected_es_client = es_client
        self._es_client = es_client
        self._es_lock = threading.Lock()
        self._es_retry_after = 0.0
        self._verdict_index = self._resolve_verdict_index(config)

        self.logger.info(
            "DedupStateHandler initialized (in-process) with dedup TTL: %s seconds.",
            self._dedup_ttl_seconds,
        )
        if self._protect_confirmed_enabled:
            self.logger.info(
                "Confirmed verdict protection enabled (ES-backed, index=%s, TTL=%ss)",
                self._verdict_index, self._protect_confirmed_ttl,
            )
        if self._end_delta_enabled:
            self.logger.info("End time delta filter enabled (threshold=%ss, TTL=%ss)",
                             self._end_delta_threshold, self._end_delta_ttl)

    @staticmethod
    def _validate_ttl(value, name: str) -> float:
        """Return ``value`` as a finite positive TTL in seconds, else raise.

        Guards against a mistaken ``0``/negative/NaN TTL becoming a
        non-expiring cache entry.
        """
        try:
            ttl = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a positive number of seconds, got {value!r}")
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError(f"{name} must be a finite positive number of seconds, got {value!r}")
        return ttl

    # ─────────────────────────────────────────────────────────────────────
    # Key building (unchanged from the Redis implementation)
    # ─────────────────────────────────────────────────────────────────────

    def _build_key(self, msg: dict, rate_limit: bool = False, is_last_chunk: bool = False) -> str:
        """Build a deterministic VLM dedup key from the VLM alert schema.

        Required fields: sensorId, timestamp, end, objectIds, category.
        analyticsModule.id is optional and included if present.
        """
        if 'objectIds' in msg:
            sensor_id = (msg.get('sensorId') or '').strip().lower()
            timestamp = msg.get('timestamp') or ''
            end = msg.get('end') or ''
            category = (msg.get('category') or '').strip().lower()
            am_id = ((msg.get('analyticsModule') or {}).get('id') or '').strip().lower()

            object_ids = msg.get('objectIds') or []
            sorted_ids = sorted(str(x) for x in object_ids)
            obj_digest = hashlib.sha1(
                (','.join(sorted_ids)).encode('utf-8')
            ).hexdigest()[:16]

            include_end = (not rate_limit) and self._should_include_end(category)
            if include_end and not end:
                self.logger.warning(
                    "Incident category '%s' requires end timestamp but field is missing; "
                    "falling back to empty value.",
                    category,
                )

            parts = ["vlm", sensor_id, timestamp]
            if include_end:
                parts.append(end)
            parts.extend([obj_digest, category, am_id, str(is_last_chunk).lower()])
            return ':'.join(parts)
        else:
            timestamp = msg.get("timestamp")
            sensor_id = msg.get("sensor", {}).get("id")
            vehicle_id = msg.get("object", {}).get("id")
            anomaly_type = msg.get('analyticsModule', {}).get('id', '')
            return f"anomaly:{timestamp}:{sensor_id}:{vehicle_id}:{anomaly_type}"

    def _load_incident_end_categories(self, state_config: dict) -> set[str]:
        raw_categories = state_config.get('end_time_in_dedup_key_categories') or []
        if isinstance(raw_categories, dict):
            return {
                str(name).strip().lower()
                for name, enabled in raw_categories.items()
                if enabled
            }
        return {str(name).strip().lower() for name in raw_categories}

    def _should_include_end(self, category: str) -> bool:
        if not category:
            return False
        return category.strip().lower() in self._incident_end_categories

    # ─────────────────────────────────────────────────────────────────────
    # TTL dedup + rate limit (in-process)
    # ─────────────────────────────────────────────────────────────────────

    def process_event(self, msg: dict, rate_limit: bool = False, is_last_chunk: bool = False) -> bool:
        if rate_limit:
            category = (msg.get('category') or '').strip().lower()
            if not self._should_include_end(category):
                self.logger.debug("VLM rate limit skipped for category without end-time requirement: %s", category)
                return True

        key = self._build_key(msg, rate_limit, is_last_chunk)
        ttl = self._rate_limit_ttl if rate_limit else self._dedup_ttl_seconds
        try:
            newly_set = self._dedup_cache.set_if_absent(key, 1, ttl=ttl)
            if newly_set:
                if self._dedup_verbose:
                    self.logger.debug("VLM %s set key with TTL=%s: %s",
                                      "rate-limit" if rate_limit else "dedup", ttl, key)
                return True
            if self._dedup_verbose:
                self.logger.debug("VLM %s HIT for key: %s",
                                  "rate-limit" if rate_limit else "dedup", key)
            return False
        except Exception as e:
            self.logger.error("In-process dedup failed (%s); allowing event: %s", e, key)
            return True

    @staticmethod
    def _is_last_chunk(msg: dict) -> bool:
        """Read the canonical completeness flag ``info.isComplete``.

        Shared by dedup and the end-delta filter so both derive the same
        ``isComplete`` component of the canonical cohort key.
        """
        info = msg.get('info')
        if not isinstance(info, dict):
            return False
        return info.get('isComplete') in (True, 'true', 'True', 'TRUE')

    def filter_new_events(self, messages: list[dict], rate_limit: bool = False, verify_only_finished_events: bool = False) -> list[dict]:
        """Filter a list of VLM events, keeping only not-seen items within TTL."""
        kept: list[dict] = []
        for msg in messages:
            is_last_chunk = self._is_last_chunk(msg)
            if not is_last_chunk and verify_only_finished_events:
                continue
            if self.process_event(msg, rate_limit, is_last_chunk):
                kept.append(msg)
        return kept

    # ─────────────────────────────────────────────────────────────────────
    # End Time Delta Filter (in-process)
    # ─────────────────────────────────────────────────────────────────────

    def filter_by_end_time_delta(self, messages: list[dict]) -> list[dict]:
        """Filter incidents where end time hasn't changed significantly.

        Independent of existing dedup. Applies only to incident messages (with objectIds).
        """
        if not self._end_delta_enabled:
            return messages
        kept = []
        for msg in messages:
            if 'objectIds' not in msg or self._check_end_delta(msg):
                kept.append(msg)
        return kept

    def _check_end_delta(self, msg: dict) -> bool:
        """Check if end time changed significantly. Returns True to process, False to skip."""
        # Derive the end-delta key from the single canonical cohort-key
        # builder — the one definition used by every dedup-family filter.
        # ``rate_limit=True`` deliberately excludes the ``end``
        # component: end-delta compares successive ``end`` values *within* a
        # cohort, so ``end`` must not be part of the key that identifies the
        # cohort. ``isComplete`` is included so in-progress and final chunks
        # remain distinct cohorts, matching dedup.
        cohort_key = self._build_key(
            msg, rate_limit=True, is_last_chunk=self._is_last_chunk(msg)
        )
        key = f"enddelta:{cohort_key}"

        current_end = msg.get('end')
        current_epoch = self._parse_iso_to_epoch(current_end)
        if current_epoch is None:
            return True  # Can't parse, allow through

        try:
            stored = self._enddelta_cache.get(key)
            if stored is None:
                self._enddelta_cache.set(key, str(current_epoch), ttl=self._end_delta_ttl)
                if self._dedup_verbose:
                    self.logger.debug("End delta: new key, storing end=%s", current_end)
                return True

            stored_epoch = float(stored)
            delta = abs(current_epoch - stored_epoch)

            if delta >= self._end_delta_threshold:
                self._enddelta_cache.set(key, str(current_epoch), ttl=self._end_delta_ttl)
                if self._dedup_verbose:
                    self.logger.debug("End delta: significant change %.2fs, processing", delta)
                return True
            if self._dedup_verbose:
                self.logger.debug("End delta: skip, delta %.2fs < %ss", delta, self._end_delta_threshold)
            return False
        except Exception as e:
            self.logger.error("End delta check failed (%s); allowing event", e)
            return True  # Fail-open

    def _parse_iso_to_epoch(self, iso_str: str) -> float | None:
        """Parse ISO timestamp to epoch seconds. Returns None on failure."""
        if not iso_str:
            return None
        try:
            dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
            return dt.timestamp()
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────────────────
    # Confirmed Verdict Protection (Elasticsearch-backed)
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_verdict_index(self, config: dict) -> str:
        persistence_cfg = config.get('persistence') or {}
        prefix = persistence_cfg.get('index_prefix', 'ab-')
        return f"{prefix}{self._VERDICT_INDEX_SUFFIX}"

    def set_es_client(self, es_client) -> None:
        """Inject an externally-managed ES client for verdict protection.

        Lets the wiring layer share the app's configured persistence client
        (which carries auth / TLS / timeouts) instead of this handler
        constructing a partially-configured one of its own.
        """
        with self._es_lock:
            self._injected_es_client = es_client
            self._es_client = es_client
            self._es_retry_after = 0.0

    def _build_elastic_config(self):
        """Build an :class:`ElasticConfig` that propagates auth / TLS / timeout.

        Prefers ``persistence.elasticsearch`` overrides, falling back to the
        top-level ``elastic`` block — the same precedence the persistence
        layer uses — so a secured cluster is reached with the same
        credentials rather than an unauthenticated hosts-only client.
        """
        from clients.elastic import ElasticConfig

        elastic_cfg = self._app_config.get('elastic', {}) or {}
        persistence_cfg = self._app_config.get('persistence', {}) or {}
        es_override = (persistence_cfg.get('elasticsearch') or {})

        def _pick(key, default=None):
            if key in es_override:
                return es_override.get(key)
            return elastic_cfg.get(key, default)

        cloud_id = _pick('cloud_id')
        hosts_config = es_override.get('hosts') or elastic_cfg.get('hosts')
        if isinstance(hosts_config, str):
            hosts = (hosts_config,)
        elif isinstance(hosts_config, (list, tuple)):
            hosts = tuple(str(h).strip() for h in hosts_config if h)
        else:
            hosts = tuple()
        if not hosts and not cloud_id:
            raise ValueError("No Elasticsearch hosts configured for verdict protection")
        # cloud_id and hosts are mutually exclusive in elasticsearch-py; when a
        # cloud_id is configured, do not also pass hosts.
        if cloud_id:
            hosts = tuple()

        return ElasticConfig(
            hosts=hosts,
            username=_pick('username'),
            password=_pick('password'),
            api_key=_pick('api_key'),
            cloud_id=_pick('cloud_id'),
            verify_certs=bool(_pick('verify_certs', False)),
            ca_certs=_pick('ca_certs'),
            request_timeout=int(_pick('request_timeout', 10)),
        )

    def _get_es_client(self):
        """Return the ES client used for verdict protection, or ``None``.

        Uses an injected client when one was provided; otherwise builds one
        lazily from config. On a construction failure it does NOT disable
        protection permanently — it schedules a retry after
        ``_es_backoff_seconds`` so protection recovers once ES does.
        Returns ``None`` while unavailable / in backoff, which callers treat
        as fail-open.
        """
        if self._es_client is not None:
            return self._es_client
        if self._injected_es_client is not None:
            self._es_client = self._injected_es_client
            return self._es_client
        with self._es_lock:
            if self._es_client is not None:
                return self._es_client
            if time.time() < self._es_retry_after:
                return None
            try:
                from clients.elastic import ElasticClient

                self._es_client = ElasticClient(config=self._build_elastic_config())
                self._es_retry_after = 0.0
                return self._es_client
            except Exception as exc:
                self._es_retry_after = time.time() + self._es_backoff_seconds
                self.logger.warning(
                    "Verdict protection ES client unavailable (%s); failing open "
                    "and retrying after %ss.", exc, self._es_backoff_seconds,
                )
                return None

    @property
    def verdict_index(self) -> str:
        """Name of the ES index holding confirmed-verdict markers."""
        return self._verdict_index

    def ensure_verdict_index(self) -> bool:
        """Idempotently create the confirmed-verdict index up front.

        Returns ``True`` when the index is confirmed ready, ``False`` when it
        could not be created (ES unavailable / disabled). Safe to call at
        startup regardless of whether verdict protection is enabled.
        """
        if not self._protect_confirmed_enabled:
            return True
        client = self._get_es_client()
        if client is None:
            _metric("set_index_ready", self._VERDICT_INDEX_SUFFIX, False)
            return False
        try:
            client.ensure_json_index(self._verdict_index)
            _metric("set_index_ready", self._VERDICT_INDEX_SUFFIX, True)
            return True
        except Exception as exc:
            self.logger.warning("Failed to ensure verdict index %s: %s", self._verdict_index, exc)
            _metric("set_index_ready", self._VERDICT_INDEX_SUFFIX, False)
            return False

    def mark_verdict_confirmed(self, fingerprint: str) -> bool:
        """Mark fingerprint as confirmed in ES. Returns True if marked, False if disabled/error."""
        if not self._protect_confirmed_enabled or not fingerprint:
            return False
        client = self._get_es_client()
        if client is None:
            # Marker could not be written → a later check will not find it and
            # will re-verify (fail-open). Surface it on the fail-open counter.
            _metric("inc_verdict_fail_open", "es_down")
            return False
        try:
            client.ensure_json_index(self._verdict_index)
            expires_at = time.time() + self._protect_confirmed_ttl
            client.write_json(
                self._verdict_index,
                {
                    "fingerprint": fingerprint,
                    "expires_at": expires_at,
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
                doc_id=fingerprint,
            )
            if self._dedup_verbose:
                self.logger.debug("Marked confirmed (ES): %s", fingerprint)
            return True
        except Exception as e:
            self.logger.warning("Failed to mark confirmed verdict: %s", e)
            _metric("inc_verdict_fail_open", "write_error")
            return False

    def purge_expired_verdicts(self, requests_per_second: float = 50.0) -> int:
        """Delete expired confirmed-verdict markers in one throttled pass.

        Runs a sliced, throttled ``delete_by_query`` for markers whose
        ``expires_at < now``. Returns the number of markers deleted (0 when
        protection is disabled or ES is unavailable). Records run/deleted/
        last-run metrics. Never raises — the caller (scheduler) logs failures.
        """
        if not self._protect_confirmed_enabled:
            return 0
        client = self._get_es_client()
        if client is None:
            _metric("record_verdict_retention_run", 0, False)
            return 0
        query = {"range": {"expires_at": {"lt": time.time()}}}
        try:
            result = client.delete_by_query(
                self._verdict_index,
                query,
                requests_per_second=requests_per_second,
                slices="auto",
                conflicts="proceed",
            )
            deleted = int(result.get("deleted", 0) or 0)
            _metric("record_verdict_retention_run", deleted, True)
            if deleted:
                self.logger.info(
                    "Verdict retention: deleted %d expired marker(s) from %s",
                    deleted, self._verdict_index,
                )
            return deleted
        except Exception as e:
            self.logger.warning("Verdict retention pass failed: %s", e)
            _metric("record_verdict_retention_run", 0, False)
            return 0

    def is_verdict_confirmed(self, fingerprint: str) -> bool:
        """Check if a fingerprint has a live confirmed-verdict marker.

        Returns ``True`` only for a marker that is present AND carries a
        valid, finite, future ``expires_at``. Every other outcome — ES
        unavailable, marker absent, expired, or a malformed/missing/
        non-numeric ``expires_at`` — fails open (returns ``False``) so a
        damaged or legacy marker can never suppress VLM indefinitely.
        Each fail-open path increments a reason-labelled counter.
        """
        if not self._protect_confirmed_enabled or not fingerprint:
            return False
        client = self._get_es_client()
        if client is None:
            _metric("inc_verdict_fail_open", "es_down")
            return False
        start = time.time()
        try:
            doc = client.get_document(self._verdict_index, fingerprint)
        except Exception as e:
            self.logger.warning("Failed to check confirmed (%s); allowing write: %s", e, fingerprint)
            _metric("inc_verdict_fail_open", "error")
            return False  # Fail-open
        finally:
            _metric("observe_verdict_es_get", time.time() - start)

        if not doc:
            return False  # No marker → not confirmed (normal, not a fail-open)

        expires_at = doc.get("expires_at")
        # A marker must carry a valid, finite, future expiry. Missing /
        # non-numeric / NaN / inf all count as malformed and fail open —
        # NOT as a permanent confirmation.
        try:
            expires_at_f = float(expires_at)
        except (TypeError, ValueError):
            self.logger.warning(
                "Confirmed-verdict marker has missing/non-numeric expires_at; failing open."
            )
            _metric("inc_verdict_fail_open", "malformed")
            return False
        if not math.isfinite(expires_at_f):
            self.logger.warning(
                "Confirmed-verdict marker has non-finite expires_at; failing open."
            )
            _metric("inc_verdict_fail_open", "malformed")
            return False
        if expires_at_f <= time.time():
            _metric("inc_verdict_fail_open", "expired")
            return False
        if self._dedup_verbose:
            self.logger.debug("Checked confirmed verdict (ES): %s => True", fingerprint)
        return True

    async def is_verdict_confirmed_async(self, fingerprint: str) -> bool:
        """Async mirror of ``is_verdict_confirmed`` (AsyncElasticsearch lookup,
        same fail-open semantics and metrics)."""
        import asyncio

        if not self._protect_confirmed_enabled or not fingerprint:
            return False
        client = await asyncio.to_thread(self._get_es_client)
        if client is None:
            _metric("inc_verdict_fail_open", "es_down")
            return False
        start = time.time()
        try:
            doc = await client.get_document_async(self._verdict_index, fingerprint)
        except Exception as e:
            self.logger.warning("Failed to check confirmed (%s); allowing write: %s", e, fingerprint)
            _metric("inc_verdict_fail_open", "error")
            return False  # Fail-open
        finally:
            _metric("observe_verdict_es_get", time.time() - start)

        if not doc:
            return False

        expires_at = doc.get("expires_at")
        try:
            expires_at_f = float(expires_at)
        except (TypeError, ValueError):
            self.logger.warning(
                "Confirmed-verdict marker has missing/non-numeric expires_at; failing open."
            )
            _metric("inc_verdict_fail_open", "malformed")
            return False
        if not math.isfinite(expires_at_f):
            self.logger.warning(
                "Confirmed-verdict marker has non-finite expires_at; failing open."
            )
            _metric("inc_verdict_fail_open", "malformed")
            return False
        if expires_at_f <= time.time():
            _metric("inc_verdict_fail_open", "expired")
            return False
        if self._dedup_verbose:
            self.logger.debug("Checked confirmed verdict (ES): %s => True", fingerprint)
        return True

    async def mark_verdict_confirmed_async(self, fingerprint: str) -> bool:
        """Async mirror of ``mark_verdict_confirmed``."""
        import asyncio

        if not self._protect_confirmed_enabled or not fingerprint:
            return False
        client = await asyncio.to_thread(self._get_es_client)
        if client is None:
            _metric("inc_verdict_fail_open", "es_down")
            return False
        try:
            await client.ensure_json_index_async(self._verdict_index)
            expires_at = time.time() + self._protect_confirmed_ttl
            await client.write_json_async(
                self._verdict_index,
                {
                    "fingerprint": fingerprint,
                    "expires_at": expires_at,
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
                doc_id=fingerprint,
            )
            if self._dedup_verbose:
                self.logger.debug("Marked confirmed (ES): %s", fingerprint)
            return True
        except Exception as e:
            self.logger.warning("Failed to mark confirmed verdict: %s", e)
            _metric("inc_verdict_fail_open", "write_error")
            return False
