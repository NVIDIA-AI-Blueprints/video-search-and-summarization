# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Brev environment for a NemoClaw sandbox provisioned by Build Vision AI."""

from __future__ import annotations

import logging

from envs.brev_env import BrevEnvironment

logger = logging.getLogger(__name__)

class NemoClawBrevEnvironment(BrevEnvironment):
    """Validate the Build Vision AI-provisioned sandbox without redeploying it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nemoclaw_ready = False

    async def start(self, force_build: bool) -> None:
        if self._nemoclaw_ready:
            return

        await super().start(force_build)
        self._nemoclaw_ready = True
        # headless_runner performs the real gateway health check immediately
        # before every prompt. Do not duplicate an OpenShell CLI probe here:
        # it would become a second, version-specific sandbox contract.
        logger.info("Using Build Vision AI-provisioned NemoClaw on %s", self._instance_name)
