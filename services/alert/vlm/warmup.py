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

"""VLM warmup module — polls NIM readiness then sends a dummy inference.

Uses VLMClient with an extended timeout so the warmup exercises the exact
same code path (message construction, mm_processor_kwargs, media_io_kwargs)
as production inference.
"""

import copy
import logging
import os
import time

import requests

from vlm.vlm_client import VLMClient

logger = logging.getLogger(__name__)

WARMUP_VIDEO = "/app/warmup/test.mp4"
_POLL_TIMEOUT = 300  # 5 minutes
_POLL_INTERVAL = 10
_INFERENCE_TIMEOUT = 120
_INFERENCE_RETRIES = 3
_WARMUP_REQUESTS = 3


def _poll_readiness(base_url: str, timeout: int = _POLL_TIMEOUT,
                    interval: int = _POLL_INTERVAL) -> None:
    """Poll NIM /v1/health/ready until it responds 200 or timeout."""
    url = f"{base_url.rstrip('/')}/health/ready"
    deadline = time.monotonic() + timeout

    logger.info("Polling NIM readiness at %s (timeout %ds)", url, timeout)

    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                logger.info("NIM is ready")
                return
            logger.debug("NIM not ready (HTTP %d), retrying in %ds", resp.status_code, interval)
        except requests.RequestException as exc:
            logger.debug("NIM not reachable (%s), retrying in %ds", exc, interval)

        time.sleep(interval)

    raise RuntimeError(f"NIM not ready after {timeout}s — aborting startup")


def _send_warmup_inference(client: VLMClient, video_path: str) -> bool:
    """Send a dummy video inference via VLMClient to warm the model.

    Uses the provided VLMClient (pre-configured with extended timeout)
    so the warmup exercises the exact same code path as production.

    Returns True on success, False when all retries are exhausted.
    """
    for attempt in range(1, _INFERENCE_RETRIES + 1):
        try:
            logger.info("Warmup inference attempt %d/%d", attempt, _INFERENCE_RETRIES)
            client.analyze_local_video(
                video_path,
                user_prompt="Describe this video in one sentence.",
            )
            logger.info("Warmup inference succeeded")
            return True
        except Exception as exc:
            logger.warning("Warmup inference attempt %d failed: %s", attempt, exc)

    logger.warning("All %d warmup inference attempts failed — continuing anyway", _INFERENCE_RETRIES)
    return False


def _run_warmup_rounds(vlm_config: dict, video_path: str,
                       num_requests: int, inference_timeout: int) -> None:
    """Send num_requests successful warmup inferences sequentially.

    Creates a single VLMClient with extended timeout and reuses it across
    all rounds.  Stops early if a round fails after exhausting its retries
    (non-fatal).
    """
    if not os.path.isfile(video_path):
        raise RuntimeError(f"Warmup video not found: {video_path}")

    warmup_config = copy.deepcopy(vlm_config)
    warmup_config.pop('warmup', None)
    warmup_config['request_timeout'] = inference_timeout
    warmup_config['max_tokens'] = 16  # minimal output for warmup
    client = VLMClient(warmup_config)

    for i in range(1, num_requests + 1):
        logger.info("Warmup round %d/%d", i, num_requests)
        if not _send_warmup_inference(client, video_path):
            logger.warning("Warmup stopped early at round %d/%d", i, num_requests)
            return
    logger.info("All %d warmup rounds completed successfully", num_requests)


def warmup_vlm(vlm_config: dict, video_path: str = WARMUP_VIDEO) -> None:
    """Run full VLM warmup: poll readiness then send dummy inference."""
    base_url = vlm_config.get('base_url', 'http://localhost:8080/v1')
    model = vlm_config.get('model', 'unknown')
    warmup_cfg = vlm_config.get('warmup', {})

    poll_timeout = warmup_cfg.get('poll_timeout', _POLL_TIMEOUT)
    poll_interval = warmup_cfg.get('poll_interval', _POLL_INTERVAL)
    inference_timeout = warmup_cfg.get('inference_timeout', _INFERENCE_TIMEOUT)
    num_requests = warmup_cfg.get('num_requests', _WARMUP_REQUESTS)

    logger.info("Starting VLM warmup (base_url=%s, model=%s)", base_url, model)
    t0 = time.monotonic()

    _poll_readiness(base_url, poll_timeout, poll_interval)
    t_poll = time.monotonic()

    _run_warmup_rounds(vlm_config, video_path, num_requests, inference_timeout)
    t_end = time.monotonic()

    poll_elapsed = t_poll - t0
    inference_elapsed = t_end - t_poll
    total = t_end - t0
    logger.info(
        "VLM warmup complete in %.1fs (poll=%.1fs, inference=%.1fs)",
        total, poll_elapsed, inference_elapsed,
    )
