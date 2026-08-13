# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Runtime configuration API models."""

from enum import Enum
from typing import Annotated, Optional

from pydantic import Field

from .common import ANY_CHAR_PATTERN, CommonBaseModel

CONFIG_ALERT_TYPE = "config"
CONFIG_CHANGE_TYPE = "config"
CONFIG_WARNING_MAX_LENGTH = 2048
CONFIG_TOPIC_PARTITION_MAX_VALUE = 100000

ConfigWarning = Annotated[
    str,
    Field(max_length=CONFIG_WARNING_MAX_LENGTH, pattern=ANY_CHAR_PATTERN),
]
OptionalConfigTopicPartitionInt32 = (
    Annotated[
        int,
        Field(
            ge=1,
            le=CONFIG_TOPIC_PARTITION_MAX_VALUE,
            json_schema_extra={"format": "int32"},
        ),
    ]
    | None
)


class MessageBus(str, Enum):
    """Supported runtime output message buses."""

    KAFKA = "kafka"
    REDIS = "redis"


class MessageBusConfigMetadata(CommonBaseModel):
    """Config event metadata for output message-bus routing."""

    messagingbus: MessageBus = Field(
        description="Output message bus to use for generated messages.",
        examples=["kafka", "redis"],
    )
    region: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Optional VSS region identifier.",
    )
    group: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Optional VSS group identifier.",
    )
    topic_prefix: str = Field(
        alias="topic-prefix",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Kafka topic or Redis Stream name for generated messages.",
        examples=["mdx-bev"],
    )
    errorbus: Optional[MessageBus] = Field(
        default=None,
        description="Optional error-message output bus to update with this config event.",
        examples=["kafka", "redis"],
    )
    error_topic_prefix: Optional[str] = Field(
        default=None,
        alias="error-topic-prefix",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Optional Kafka topic or Redis channel name for error messages.",
        examples=["mdx-vlm-errors"],
    )
    create_topic: bool = Field(
        default=False,
        alias="create-topic",
        description="Best-effort Kafka topic creation flag. Ignored for Redis Streams.",
    )
    topic_partition: OptionalConfigTopicPartitionInt32 = Field(
        default=None,
        alias="topic-partition",
        description="Requested Kafka topic partition count when create-topic is true.",
    )


class ConfigEventHeaders(CommonBaseModel):
    """Optional VSS headers nested in the config event."""

    source: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        examples=["vios"],
    )
    created_at: Optional[str] = Field(
        default=None,
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
    )


class ConfigEvent(CommonBaseModel):
    """Config event payload."""

    camera_id: str = Field(default="", max_length=256, pattern=ANY_CHAR_PATTERN)
    name: Optional[str] = Field(default=None, max_length=256, pattern=ANY_CHAR_PATTERN)
    camera_url: str = Field(default="", max_length=1024, pattern=ANY_CHAR_PATTERN)
    change: str = Field(
        description="Config event operation type.",
        max_length=32,
        pattern=r"^[A-Za-z_]+$",
        examples=[CONFIG_CHANGE_TYPE],
    )
    metadata: MessageBusConfigMetadata = Field(description="Message-bus configuration.")
    headers: Optional[ConfigEventHeaders] = Field(default=None)


class ConfigRequest(CommonBaseModel):
    """VSS-style runtime config event request."""

    alert_type: str = Field(
        description="Config alert type.",
        max_length=128,
        pattern=ANY_CHAR_PATTERN,
        examples=[CONFIG_ALERT_TYPE],
    )
    created_at: str = Field(
        description="Config event creation timestamp.",
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
        examples=["2023-03-10T00:45:16Z"],
    )
    txn_id: str = Field(
        description="Config transaction identifier.",
        max_length=64,
        pattern=ANY_CHAR_PATTERN,
    )
    event: ConfigEvent
    source: str = Field(
        description="Config event source.",
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        examples=["vios"],
    )


class ConfigResponse(CommonBaseModel):
    """Runtime config update response."""

    txn_id: str = Field(max_length=64, pattern=ANY_CHAR_PATTERN)
    status: str = Field(max_length=32, pattern=r"^[A-Za-z_]+$", examples=["updated"])
    messagingbus: MessageBus = Field(description="Active output message bus.")
    topic: str = Field(
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Active Kafka topic or Redis Stream name.",
    )
    errorbus: Optional[MessageBus] = Field(
        default=None,
        description="Active error-message output bus when updated by the config event.",
    )
    error_topic: Optional[str] = Field(
        default=None,
        max_length=256,
        pattern=ANY_CHAR_PATTERN,
        description="Active Kafka topic or Redis channel for error messages.",
    )
    source: str = Field(max_length=256, pattern=ANY_CHAR_PATTERN)
    created_at: str = Field(max_length=64, pattern=ANY_CHAR_PATTERN)
    warnings: Optional[list[ConfigWarning]] = Field(
        default=None,
        max_length=16,
        description="Warnings about non-blocking runtime config side effects.",
    )
