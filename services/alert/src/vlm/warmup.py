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

    while True:
        # Read once per pass and reused for both the request and the sleep:
        # neither may carry the poll past the window it was given, and the
        # fixed five-second request plus a full interval used to do exactly
        # that on the last iteration.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            # Never longer than what is left. Flooring this at a second let a
            # request outlast the whole window when under a second remained.
            resp = requests.get(url, timeout=min(5, remaining))
            if resp.status_code == 200:
                logger.info("NIM is ready")
                return
            logger.debug("NIM not ready (HTTP %d), retrying in %ds", resp.status_code, interval)
        except requests.RequestException as exc:
            logger.debug("NIM not reachable (%s), retrying in %ds", exc, interval)

        # Recomputed: the request above may have spent everything that was
        # left, and sleeping on the reading from before it carried the poll
        # past its window on exactly the attempt that had none to spare.
        time.sleep(max(0.0, min(interval, deadline - time.monotonic())))

    raise RuntimeError(f"NIM not ready after {timeout}s — aborting startup")


def _send_warmup_inference(client: VLMClient, video_path: str,
                           deadline: float = None) -> bool:
    """Send a dummy video inference via VLMClient to warm the model.

    Uses the provided VLMClient (pre-configured with extended timeout)
    so the warmup exercises the exact same code path as production.

    Returns True on success, False when all retries are exhausted or the
    startup deadline leaves no room to begin another.
    """
    for attempt in range(1, _INFERENCE_RETRIES + 1):
        if deadline is not None and time.monotonic() >= deadline:
            # Checked per attempt, not per round: three retries at the full
            # timeout would otherwise spend three times the window reserved,
            # which is how warmup came to eat the fleet's share.
            logger.warning(
                "Stopping warmup inference at attempt %d/%d: the startup "
                "budget is spent", attempt, _INFERENCE_RETRIES,
            )
            return False
        try:
            logger.info("Warmup inference attempt %d/%d", attempt, _INFERENCE_RETRIES)
            if deadline is not None:
                # Re-clamped per attempt: the timeout was sized once, when the
                # poll ended, so a later attempt could outlast what is left.
                client.client = client.client.with_options(
                    timeout=max(0.1, deadline - time.monotonic())
                )
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
                       num_requests: int, inference_timeout: int,
                       deadline: float = None) -> None:
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
    # No SDK-level retries. The loop below does its own, and checks the
    # deadline before each; the SDK's would run three HTTP attempts inside a
    # single one of ours, unseen by that check.
    warmup_config['max_retries'] = 0
    warmup_config['max_tokens'] = 16  # minimal output for warmup
    client = VLMClient(warmup_config)

    for i in range(1, num_requests + 1):
        if deadline is not None and time.monotonic() >= deadline:
            # Checked between rounds rather than divided across them: the
            # division made each attempt too short to ever succeed.
            logger.warning(
                "Stopping VLM warmup at round %d/%d: the startup budget is spent",
                i, num_requests,
            )
            return
        logger.info("Warmup round %d/%d", i, num_requests)
        if not _send_warmup_inference(client, video_path, deadline=deadline):
            logger.warning("Warmup stopped early at round %d/%d", i, num_requests)
            return
    logger.info("All %d warmup rounds completed successfully", num_requests)


def warmup_vlm(vlm_config: dict, video_path: str = WARMUP_VIDEO,
               deadline: float = None) -> None:
    """Run full VLM warmup: poll readiness then send dummy inference.

    ``deadline`` is a ``time.monotonic()`` instant the whole of warmup must
    finish by. It bounds the poll and is re-checked between inference rounds,
    which is what lets the configured timeouts stay at values a cold inference
    can actually use: dividing the budget across the worst-case attempt count
    made ``inference_timeout`` around two seconds against a documented hundred
    and twenty, so warmup could never succeed and quietly warmed nothing.
    """
    base_url = vlm_config.get('base_url', 'http://localhost:8080/v1')
    model = vlm_config.get('model', 'unknown')
    warmup_cfg = vlm_config.get('warmup', {})

    def left() -> float:
        return _POLL_TIMEOUT if deadline is None else max(0.0, deadline - time.monotonic())

    poll_timeout = min(warmup_cfg.get('poll_timeout', _POLL_TIMEOUT), left())
    poll_interval = min(warmup_cfg.get('poll_interval', _POLL_INTERVAL),
                        max(1.0, poll_timeout / 2))
    num_requests = warmup_cfg.get('num_requests', _WARMUP_REQUESTS)

    logger.info("Starting VLM warmup (base_url=%s, model=%s)", base_url, model)
    t0 = time.monotonic()

    _poll_readiness(base_url, poll_timeout, poll_interval)
    t_poll = time.monotonic()

    # Read after the poll, so an inference gets what the poll did not spend
    # rather than a share carved out before either had run.
    inference_timeout = min(warmup_cfg.get('inference_timeout', _INFERENCE_TIMEOUT), left())
    if inference_timeout <= 0:
        logger.warning("Skipping VLM warmup inference: the startup budget is spent")
        return

    _run_warmup_rounds(vlm_config, video_path, num_requests, inference_timeout,
                       deadline=deadline)
    t_end = time.monotonic()

    poll_elapsed = t_poll - t0
    inference_elapsed = t_end - t_poll
    total = t_end - t0
    logger.info(
        "VLM warmup complete in %.1fs (poll=%.1fs, inference=%.1fs)",
        total, poll_elapsed, inference_elapsed,
    )
