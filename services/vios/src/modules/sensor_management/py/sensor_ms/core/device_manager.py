# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""DeviceManager — holds the DB engine/session factory and repo.

The C++ DeviceManager also keeps an in-memory sensor cache. We start read-through (no cache) for
correctness; a cache can be layered later if profiling shows the DB round-trips matter.
"""
from __future__ import annotations

from ..config import Config
from ..db.engine import make_engine, make_session_factory
from ..db.repo import SensorRepo


class DeviceManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.engine = make_engine(cfg)
        self.session_factory = make_session_factory(self.engine)
        self.repo = SensorRepo(self.session_factory, cfg.vst_data_path)

    def dispose(self) -> None:
        self.engine.dispose()
