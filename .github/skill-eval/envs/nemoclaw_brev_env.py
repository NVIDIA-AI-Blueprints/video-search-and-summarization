# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Brev environment for a NemoClaw sandbox provisioned by Build Vision AI."""

from __future__ import annotations

import json
import logging
import os
import shlex

from envs.brev_env import BrevEnvironment

logger = logging.getLogger(__name__)

class NemoClawBrevEnvironment(BrevEnvironment):
    """Reuse the Build Vision AI-provisioned sandbox without redeploying it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nemoclaw_ready = False

    async def start(self, force_build: bool) -> None:
        if self._nemoclaw_ready:
            return

        await super().start(force_build)
        declared = os.environ.get('SKILL_EVAL_NEMOCLAW_HOST_FIXTURE')
        if declared:
            fixture = json.loads(declared)
            if (
                not isinstance(fixture, dict)
                or set(fixture) != {'nvstreamer_scan_file'}
                or not isinstance(fixture['nvstreamer_scan_file'], str)
            ):
                raise ValueError('unsupported NemoClaw host fixture')
            sandbox = os.environ['NEMOCLAW_SANDBOX_NAME']
            result = await self.exec(
                'python3 "$HOME/video-search-and-summarization/.github/skill-eval/nemoclaw/stage_fixtures.py" '
                f'--sandbox {shlex.quote(sandbox)} --nvstreamer-scan-file {shlex.quote(fixture["nvstreamer_scan_file"])}',
                timeout_sec=210,
            )
            if result.return_code != 0:
                raise RuntimeError(f'NemoClaw host fixture preparation failed (exit {result.return_code})')
        self._nemoclaw_ready = True
        # headless_runner performs the real gateway health check immediately
        # before every prompt. Do not duplicate an OpenShell CLI probe here:
        # it would become a second, version-specific sandbox contract.
        logger.info("Using Build Vision AI-provisioned NemoClaw on %s", self._instance_name)
