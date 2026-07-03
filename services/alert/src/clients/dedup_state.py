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
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import yaml


class _TTLCache:
    """Minimal thread-safe in-process cache with per-key TTL.

    Kept intentionally small (no external dependency): the alert dedup
    hot path only needs ``get`` / ``set`` / ``set_if_absent`` with a
    per-entry expiry. Expired entries are dropped lazily on access and
    swept periodically on write so memory stays bounded under churn.

    ``clock`` is injectable so tests can advance time deterministically.
    """

    def __init__(self, clock=time.monotonic, purge_interval: float = 30.0):
        self._data: Dict[str, tuple[Any, Optional[float]]] = {}
        self._lock = threading.Lock()
        self._clock = clock
        self._purge_interval = purge_interval
        self._last_purge = 0.0

    def _expired(self, expire_at: Optional[float], now: float) -> bool:
        return expire_at is not None and expire_at <= now

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

    def get(self, key: str) -> Any:
        now = self._clock()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            value, expire_at = item
            if self._expired(expire_at, now):
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        now = self._clock()
        expire_at = now + ttl if ttl else None
        with self._lock:
            self._maybe_purge_locked(now)
            self._data[key] = (value, expire_at)

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
            self._data[key] = (value, now + ttl if ttl else None)
            return True

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

    def __init__(self, config_file="config.yaml", rate_limit=300, clock=time.monotonic):
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

        self._rate_limit_ttl = rate_limit
        self._incident_end_categories = self._load_incident_end_categories(state_config)
        self._dedup_ttl_seconds = state_config.get('dedup_ttl_seconds', 300)

        # Confirmed verdict protection config (ES-backed).
        _protect_cfg = state_config.get('protect_confirmed_verdicts', {})
        self._protect_confirmed_enabled = _protect_cfg.get('enabled', False)
        self._protect_confirmed_ttl = _protect_cfg.get('ttl_seconds', 600)

        # End time delta filter config.
        _delta_cfg = state_config.get('end_time_delta_filter', {})
        self._end_delta_enabled = _delta_cfg.get('enabled', False)
        self._end_delta_threshold = _delta_cfg.get('threshold_seconds', 5)
        self._end_delta_ttl = _delta_cfg.get('ttl_seconds', 3600)

        # In-process state (per consumer). Dedup + rate-limit share one
        # keyspace to preserve the exact single-keyspace semantics of the
        # previous Redis ``SET NX`` path.
        self._dedup_cache = _TTLCache(clock)
        self._enddelta_cache = _TTLCache(clock)

        # Lazily-built ES client for verdict protection.
        self._es_client = None
        self._es_lock = threading.Lock()
        self._es_disabled = False
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

    def filter_new_events(self, messages: list[dict], rate_limit: bool = False, verify_only_finished_events: bool = False) -> list[dict]:
        """Filter a list of VLM events, keeping only not-seen items within TTL."""
        kept: list[dict] = []
        for msg in messages:
            is_last_chunk = False
            if 'info' in msg and 'isComplete' in msg['info'] and msg['info']['isComplete'] in [True, 'true', 'True', 'TRUE']:
                is_last_chunk = True
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
        sensor_id = (msg.get('sensorId') or '').strip().lower()
        timestamp = msg.get('timestamp') or ''
        category = (msg.get('category') or '').strip().lower()
        am_id = ((msg.get('analyticsModule') or {}).get('id') or '').strip().lower()
        object_ids = msg.get('objectIds') or []
        sorted_ids = sorted(str(x) for x in object_ids)
        obj_digest = hashlib.sha1((','.join(sorted_ids)).encode('utf-8')).hexdigest()[:16]
        key = f"vlm:enddelta:{sensor_id}:{timestamp}:{obj_digest}:{category}:{am_id}"

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

    def _get_es_client(self):
        """Lazily build the ES client used for verdict protection.

        Returns ``None`` (and disables further attempts) when ES cannot
        be reached / configured, so verdict protection fails open exactly
        like the previous Redis implementation did on a backend outage.
        """
        if self._es_disabled:
            return None
        if self._es_client is not None:
            return self._es_client
        with self._es_lock:
            if self._es_client is not None:
                return self._es_client
            if self._es_disabled:
                return None
            try:
                from clients.elastic import ElasticClient, ElasticConfig

                elastic_cfg = self._app_config.get('elastic', {}) or {}
                persistence_cfg = self._app_config.get('persistence', {}) or {}
                es_override = (persistence_cfg.get('elasticsearch') or {})
                hosts_config = es_override.get('hosts') or elastic_cfg.get('hosts')
                if isinstance(hosts_config, str):
                    hosts = (hosts_config,)
                elif isinstance(hosts_config, (list, tuple)):
                    hosts = tuple(str(h).strip() for h in hosts_config if h)
                else:
                    hosts = tuple()
                if not hosts:
                    raise ValueError("No Elasticsearch hosts configured for verdict protection")
                self._es_client = ElasticClient(config=ElasticConfig(hosts=hosts))
                return self._es_client
            except Exception as exc:
                self.logger.warning(
                    "Verdict protection ES client unavailable (%s); "
                    "confirmed-verdict protection will fail open.", exc,
                )
                self._es_disabled = True
                return None

    def mark_verdict_confirmed(self, fingerprint: str) -> bool:
        """Mark fingerprint as confirmed in ES. Returns True if marked, False if disabled/error."""
        if not self._protect_confirmed_enabled or not fingerprint:
            return False
        client = self._get_es_client()
        if client is None:
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
            self.logger.warning("Failed to mark confirmed (%s): %s", e, fingerprint)
            return False

    def is_verdict_confirmed(self, fingerprint: str) -> bool:
        """Check if fingerprint is already confirmed. Returns False if disabled/expired/error (fail-open)."""
        if not self._protect_confirmed_enabled or not fingerprint:
            return False
        client = self._get_es_client()
        if client is None:
            return False
        try:
            doc = client.get_document(self._verdict_index, fingerprint)
            if not doc:
                return False
            expires_at = doc.get("expires_at")
            # No expiry recorded → treat as still-valid marker.
            if expires_at is not None and float(expires_at) <= time.time():
                return False
            if self._dedup_verbose:
                self.logger.debug("Checked confirmed verdict (ES): %s => True", fingerprint)
            return True
        except Exception as e:
            self.logger.warning("Failed to check confirmed (%s); allowing write: %s", e, fingerprint)
            return False  # Fail-open
