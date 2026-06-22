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

import json
import logging
import os
from datetime import datetime
import os
import hashlib

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
import yaml


class RedisHandler:
    def __init__(self, config_file="config.yaml", rate_limit=300):
        """Initialize the Redis handler by reading configuration."""
        self.logger = logging.getLogger(self.__class__.__name__)
        # Optional: verbose per-key dedup logs only when explicitly enabled
        self._dedup_verbose = os.getenv("LOG_VERBOSE_DEDUP", "false").lower() in ("1", "true", "yes")

        # Minimal path validation to prevent path traversal
        normalized_path = os.path.normpath(config_file)
        # if '..' in normalized_path or normalized_path.startswith('/'):
        #     raise ValueError(f"Invalid config file path: {config_file}")
        if not normalized_path.lower().endswith((".yaml", ".yml")):
            raise ValueError(f"Config file must be a YAML file: {normalized_path}")
        if not os.path.isfile(normalized_path):
            raise FileNotFoundError(f"Config file not found: {normalized_path}")

        with open(normalized_path, 'r') as file:
            config = yaml.safe_load(file)

        # Prefer top-level 'redis' config; fallback to event_bridge.redis_source
        redis_config = config.get('redis') or config.get('event_bridge', {}).get('redis_source')
        if not redis_config:
            raise KeyError("Redis configuration not found under 'redis' or 'event_bridge.redis_source'")
        
        # Connection config contains hosts/ports; keep it at DEBUG to reduce INFO noise
        self.logger.debug(f"Connecting to Redis with config: {redis_config}")

        # Bound the cost of a Redis outage on the API hot path. Defaults
        # in ``redis-py`` retry connect_check_health with backoff and rely
        # on OS TCP timeouts (``tcp_syn_retries`` ≈ 127s on Linux), which
        # we measured at ~8s/call when the port is refused and ~9 minutes
        # when the host is a network blackhole — under sustained load
        # this turns a Redis blip into a cascading API stall.
        # ``socket_connect_timeout`` caps a single connect attempt;
        # ``Retry(NoBackoff(), 0)`` disables the inner per-connection
        # retry storm so the cache wrapper's ``except`` (which already
        # treats Redis as non-fatal) fires within ~2s instead of ~15s+.
        self._redis_client = redis.Redis(
            host=redis_config['host'],
            port=redis_config['port'],
            db=redis_config.get('db', 0),  # Make sure this matches the scheduler's DB
            decode_responses=True,
            socket_connect_timeout=redis_config.get('socket_connect_timeout', 2.0),
            socket_timeout=redis_config.get('socket_timeout', 2.0),
            health_check_interval=redis_config.get('health_check_interval', 30),
            retry=Retry(NoBackoff(), 0),
        )
        self._rate_limit_ttl = rate_limit  # Default rate limit TTL of 5 seconds
        self._schedule_key = redis_config.get('schedule_key', 'celery-beat-schedule')
        self._incident_end_categories = self._load_incident_end_categories(redis_config)
        self._dedup_ttl_seconds = redis_config.get('dedup_ttl_seconds', 300)
        # Confirmed verdict protection config
        _protect_cfg = redis_config.get('protect_confirmed_verdicts', {})
        self._protect_confirmed_enabled = _protect_cfg.get('enabled', False)
        self._protect_confirmed_ttl = _protect_cfg.get('ttl_seconds', 600)
        self.logger.info(
            "RedisHandler initialized successfully with dedup TTL: %s seconds.",
            self._dedup_ttl_seconds,
        )
        if self._protect_confirmed_enabled:
            self.logger.info("Confirmed verdict protection enabled (TTL=%ss)", self._protect_confirmed_ttl)

        # End time delta filter config
        _delta_cfg = redis_config.get('end_time_delta_filter', {})
        self._end_delta_enabled = _delta_cfg.get('enabled', False)
        self._end_delta_threshold = _delta_cfg.get('threshold_seconds', 5)
        self._end_delta_ttl = _delta_cfg.get('ttl_seconds', 3600)
        if self._end_delta_enabled:
            self.logger.info("End time delta filter enabled (threshold=%ss, TTL=%ss)",
                             self._end_delta_threshold, self._end_delta_ttl)

    def _build_key(self, msg: dict, rate_limit: bool = False, is_last_chunk: bool = False) -> str:
        """Build a deterministic VLM dedup key from the VLM alert schema.

        Required fields: sensorId, timestamp, end, objectIds, category.
        analyticsModule.id is optional and included if present.
        """
        #for incident messages
        if 'objectIds' in msg:
            sensor_id = (msg.get('sensorId') or '').strip().lower()
            timestamp = msg.get('timestamp') or ''
            end = msg.get('end') or ''
            category = (msg.get('category') or '').strip().lower()
            am_id = ((msg.get('analyticsModule') or {}).get('id') or '').strip().lower()

            object_ids = msg.get('objectIds') or []
            # Deterministic digest of sorted object IDs
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
            key = ':'.join(parts)

            return key
        else:
            #for ITS messages
            timestamp = msg.get("timestamp")
            sensor_id = msg.get("sensor", {}).get("id")
            vehicle_id = msg.get("object", {}).get("id")
            anomaly_type = msg.get('analyticsModule', {}).get('id', '')
            return f"anomaly:{timestamp}:{sensor_id}:{vehicle_id}:{anomaly_type}"

    def _load_incident_end_categories(self, redis_config: dict) -> set[str]:
        raw_categories = redis_config.get('end_time_in_dedup_key_categories') or []
        if isinstance(raw_categories, dict):
            categories = {
                str(name).strip().lower()
                for name, enabled in raw_categories.items()
                if enabled
            }
        else:
            categories = {
                str(name).strip().lower()
                for name in raw_categories
            }
        return categories

    def _should_include_end(self, category: str) -> bool:
        if not category:
            return False
        return category.strip().lower() in self._incident_end_categories

    def process_event(self, msg: dict, rate_limit: bool = False, is_last_chunk: bool = False) -> bool:
        if rate_limit:
            category = (msg.get('category') or '').strip().lower()
            if not self._should_include_end(category):
                self.logger.debug("VLM rate limit skipped for category without end-time requirement: %s", category)
                return True

        key = self._build_key(msg, rate_limit, is_last_chunk)
        ttl = self._rate_limit_ttl if rate_limit else self._dedup_ttl_seconds
        try:
            # Redis SET with NX returns 'OK' if set, None if key exists
            result = self._redis_client.set(key, 1, ex=ttl, nx=True)
            if result is not None:  # 'OK' means key was created
                if self._dedup_verbose:
                    self.logger.debug("VLM %s set key with TTL=%s: %s",
                                      "rate-limit" if rate_limit else "dedup", ttl, key)
                return True
            else:  # None means key already existed
                if self._dedup_verbose:
                    self.logger.debug("VLM %s HIT for key: %s",
                                      "rate-limit" if rate_limit else "dedup", key)
                return False
        except Exception as e:
            self.logger.error("Redis SET NX failed (%s); allowing event: %s", e, key)
            return True

    def filter_new_events(self, messages: list[dict], rate_limit: bool = False, verify_only_finished_events: bool = False) -> list[dict]:
        """Filter a list of VLM events, keeping only not-seen items within TTL.""" 
        kept: list[dict] = []
        for msg in messages:
            is_last_chunk = False
            if 'info' in msg and 'isComplete' in msg['info'] and msg['info']['isComplete'] in [True, 'true', 'True', 'TRUE']:
                is_last_chunk = True
            if not is_last_chunk and verify_only_finished_events:
                #skip the event if it is not the last chunk and verify_only_finished_events is True
                continue
            if self.process_event(msg, rate_limit, is_last_chunk):
                kept.append(msg)
        # self.logger.info("VLM dedup kept %s of %s messages", len(kept), len(messages))
        return kept

    # ─────────────────────────────────────────────────────────────────────
    # End Time Delta Filter (filters incidents by significant end time changes)
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
        # Build base key (same as _build_key but never includes end)
        sensor_id = (msg.get('sensorId') or '').strip().lower()
        timestamp = msg.get('timestamp') or ''
        category = (msg.get('category') or '').strip().lower()
        am_id = ((msg.get('analyticsModule') or {}).get('id') or '').strip().lower()
        object_ids = msg.get('objectIds') or []
        sorted_ids = sorted(str(x) for x in object_ids)
        obj_digest = hashlib.sha1((','.join(sorted_ids)).encode('utf-8')).hexdigest()[:16]
        key = f"vlm:enddelta:{sensor_id}:{timestamp}:{obj_digest}:{category}:{am_id}"

        # Parse current end to epoch
        current_end = msg.get('end')
        current_epoch = self._parse_iso_to_epoch(current_end)
        if current_epoch is None:
            return True  # Can't parse, allow through

        try:
            stored = self._redis_client.get(key)
            if stored is None:
                # First time - store and process
                self._redis_client.set(key, str(current_epoch), ex=self._end_delta_ttl)
                if self._dedup_verbose:
                    self.logger.debug("End delta: new key, storing end=%s", current_end)
                return True

            stored_epoch = float(stored)
            delta = abs(current_epoch - stored_epoch)

            if delta >= self._end_delta_threshold:
                # Significant change - update and process
                self._redis_client.set(key, str(current_epoch), ex=self._end_delta_ttl)
                if self._dedup_verbose:
                    self.logger.debug("End delta: significant change %.2fs, processing", delta)
                return True
            else:
                # Insignificant change - skip
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
    # Confirmed Verdict Protection (reuses existing Redis client & patterns)
    # ─────────────────────────────────────────────────────────────────────

    def mark_verdict_confirmed(self, fingerprint: str) -> bool:
        """Mark fingerprint as confirmed. Returns True if marked, False if disabled/error."""
        if not self._protect_confirmed_enabled:
            return False
        key = f"vlm:confirmed:{fingerprint}"
        try:
            self._redis_client.set(key, 1, ex=self._protect_confirmed_ttl)
            if self._dedup_verbose:
                self.logger.debug("Marked confirmed: %s", key)
            return True
        except Exception as e:
            self.logger.warning("Failed to mark confirmed (%s): %s", e, key)
            return False

    def is_verdict_confirmed(self, fingerprint: str) -> bool:
        """Check if fingerprint is already confirmed. Returns False if disabled/error (fail-open)."""
        if not self._protect_confirmed_enabled:
            return False
        key = f"vlm:confirmed:{fingerprint}"
        try:
            exists = self._redis_client.exists(key) > 0
            if self._dedup_verbose:
                self.logger.debug("Checked confirmed verdict: %s => %s", key, exists)
            return exists
        except Exception as e:
            self.logger.warning("Failed to check confirmed (%s); allowing write: %s", e, key)
            return False  # Fail-open

    def update_heartbeat_config(self, name: str, config: dict):
        """Update heartbeat configuration in Redis."""
        try:
            # Store in Redis
            self._redis_client.hset(self._schedule_key, name, json.dumps(config))
            self.logger.info(f"Updated heartbeat config: {name}")
            return True
        except Exception as e:
            self.logger.error(f"Error updating heartbeat config: {e}")
            raise

    def get_heartbeat_configs(self):
        """Get all heartbeat configurations."""
        try:
            configs = self._redis_client.hgetall(self._schedule_key)
            return {k: json.loads(v) for k, v in configs.items()}
        except Exception as e:
            self.logger.error(f"Error getting heartbeat configs: {e}")
            raise

    def delete_heartbeat_config(self, sensor_name: str) -> bool:
        """Delete heartbeat configuration for a sensor."""
        try:
            # Find config by sensor name
            configs = self.get_heartbeat_configs()
            for name, config in configs.items():
                if config.get('kwargs', {}).get('sensor_name') == sensor_name:
                    self._redis_client.hdel(self._schedule_key, name)
                    self.logger.info(f"Deleted heartbeat config for sensor: {sensor_name}")
                    return True
            return False
        except Exception as e:
            self.logger.error(f"Error deleting heartbeat config: {e}")
            raise
