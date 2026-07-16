# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trusted runtime configuration for executable composition roots."""

from pathlib import Path

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VSS_MEMORY_", extra="ignore")

    elasticsearch_endpoint: AnyHttpUrl = AnyHttpUrl("http://localhost:9200")
    elasticsearch_index: str = Field(default="vss-unified-memory", pattern=r"^[a-z0-9][a-z0-9._-]*$")
    embedding_endpoint: AnyHttpUrl
    embedding_model: str = Field(default="cosmos-embed1-448p", min_length=1)
    embedding_dimensions: int = Field(default=768, ge=1)
    tokenizer_vocab_path: Path
    passage_max_tokens: int = Field(default=128, ge=4, le=128)
    passage_overlap_tokens: int = Field(default=16, ge=0)
    embedding_max_characters: int = Field(default=1000, ge=1, le=1000)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
