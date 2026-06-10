# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SQLAlchemy engine/session factory.

Mirrors DatabaseConnectionFactory (database.h:24): PostgreSQL when use_centralize_db, else SQLite.
"""
from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Config


def build_url(cfg: Config) -> str:
    if cfg.use_centralize_db:
        host = cfg.centralize_remote_db_hostaddr or "localhost"
        port = cfg.centralize_remote_db_port or "5432"
        return (
            f"postgresql+psycopg://{cfg.centralize_db_username}:"
            f"{cfg.centralize_remote_db_password}@{host}:{port}/{cfg.centralize_db_name}"
        )
    return f"sqlite:///{cfg.sqlite_db_path}"


def make_engine(cfg: Config) -> Engine:
    # pool_pre_ping avoids stale connections; the C++ uses a fixed pool (max_centralize_db_conn).
    return create_engine(build_url(cfg), pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_schema(engine: Engine) -> None:
    """Create the sensor tables if missing (checkfirst), so a standalone fresh deployment works
    without the C++ service having pre-created the schema. Idempotent: a no-op on a shared DB that
    already has the tables."""
    from .models import Base

    Base.metadata.create_all(engine, checkfirst=True)
