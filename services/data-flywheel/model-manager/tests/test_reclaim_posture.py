# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""The startup line that says whether this process will delete a directory.

Pinned because an operator greps for it during the one window it matters: MM
deployed, DFW not yet.
"""

import logging

from src.config import log_reclaim_posture, settings


class TestReclaimPosture:
    def test_dry_run_says_it_will_delete_nothing(self, caplog, monkeypatch):
        monkeypatch.setattr(settings, "reclaim_dry_run", True)
        with caplog.at_level(logging.INFO):
            log_reclaim_posture()
        assert "DRY RUN" in caplog.text
        assert "delete nothing" in caplog.text

    def test_active_says_it_may_delete_and_names_the_escape_hatch(self, caplog, monkeypatch):
        """The remedy belongs in the same line as the diagnosis."""
        monkeypatch.setattr(settings, "reclaim_dry_run", False)
        monkeypatch.setattr(settings, "reclaim_min_age_s", 0.0)
        with caplog.at_level(logging.INFO):
            log_reclaim_posture()
        assert "ACTIVE" in caplog.text
        assert "MODEL_MANAGER_RECLAIM_DRY_RUN=true" in caplog.text
        assert "age floor of 0s" in caplog.text

    def test_it_is_not_a_warning(self, caplog, monkeypatch):
        """INFO, not WARNING: reclaiming is the normal steady state, so a warning
        would fire on every healthy deploy and get filtered before it mattered.
        """
        monkeypatch.setattr(settings, "reclaim_dry_run", False)
        with caplog.at_level(logging.DEBUG):
            log_reclaim_posture()
        assert [r.levelno for r in caplog.records] == [logging.INFO]

    def test_the_app_emits_it_on_startup(self, caplog):
        """Through the real lifespan, not by calling the function directly."""
        from fastapi.testclient import TestClient

        from main import app

        with caplog.at_level(logging.INFO), TestClient(app):
            pass
        assert "disk reclaim:" in caplog.text
