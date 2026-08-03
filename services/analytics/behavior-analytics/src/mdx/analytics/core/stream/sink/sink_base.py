# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import datetime
import json

from abc import ABC, abstractmethod
import logging
from typing import Any
from collections.abc import Callable, Mapping

from mdx.analytics.core.schema.models import convert_datetime_to_iso_8601_with_z_suffix

logger = logging.getLogger(__name__)


class Sink(ABC):
    """
    Abstract base class for data sinks.

    Defines the interface for writing messages to various destinations
    (e.g., Kafka, Redis Stream, MQTT).

    A destination that the configuration does not define is treated as a disabled output rather than
    an error -- see :meth:`resolve_destination`.
    """

    def resolve_destination(self, dest_key: str, lookup: Callable[[str], str]) -> str | None:
        """
        Resolve a destination key to a configured topic or stream, or ``None`` if undefined.

        Configuring a destination is what enables that output. An app writes to every destination its
        processors produce, and a deployment keeps the ones it wants by defining them; the rest are
        dropped. Raising instead would make every app require the union of all topics, and would fail
        on the first batch rather than when the output was actually wanted.

        The miss is logged once per key, not per batch, so a mistyped destination is visible in the
        logs without drowning them.

        :param str dest_key: Destination key, e.g. ``behavior`` or ``anomaly``.
        :param Callable[[str], str] lookup: Config accessor for this sink's destination type.
        :return str | None: The configured destination, or ``None`` when it is not defined.
        """
        destination = lookup(dest_key)
        if destination:
            return destination

        if dest_key not in self._undefined_destinations:
            self._undefined_destinations.add(dest_key)
            logger.warning(
                f"No destination configured for '{dest_key}'; output for it is disabled. "
                f"Define it in the config to enable this stream."
            )

        return None

    @property
    def _undefined_destinations(self) -> set[str]:
        """Destination keys already reported as undefined, so each is logged once."""
        if not hasattr(self, "_undefined_destinations_seen"):
            self._undefined_destinations_seen: set[str] = set()
        return self._undefined_destinations_seen

    @abstractmethod
    def write(
        self,
        dest_key: str,
        messages: list[Any],
        value_serializer: Callable,
        key_extractor: Callable | None = None,
        key_serializer: Callable | None = None,
        headers: Mapping[str, str | bytes] | None = None
    ) -> None:
        """
        Abstract method to write multiple messages to a destination.
        
        :param str dest_key: The destination key where messages will be written.
        :param list[Any] messages: List of messages to write.
        :param Callable value_serializer: Function to serialize message values.
        :param Callable | None key_extractor: Function to extract keys from messages. Defaults to None.
        :param Callable | None key_serializer: Function to serialize message keys. Defaults to None.
        :param Mapping[str, str | bytes] | None headers: Optional headers to include.
        :return: None
        """

        raise NotImplementedError("Subclasses must implement method before invoking")


    def write_msg(
        self,
        dest_key: str,
        message: bytes,
        key: bytes | None,
        headers: Mapping[str, str | bytes] | None = None
    
    ) -> None:
        """
        Write a single message to a destination.
        
        :param str dest_key: The destination key where the message will be written.
        :param bytes message: The message content in bytes format.
        :param bytes | None key: Optional key for the message.
        :param Mapping[str, str | bytes] | None headers: Optional headers to include.
        :return: None
        """



    def close(self) -> None:
        """
        Close the sink and release resources.

        :return: None
        """



def _datetime_json_serializer(object_instance):
    """
    JSON serializer for datetime objects.
    
    :param object_instance: The object to serialize, expected to be a datetime object.
    :return str: ISO 8601 formatted datetime string with Z suffix.
    :raises TypeError: If the object is not a datetime instance.
    """

    if isinstance(object_instance, datetime.datetime):
        return convert_datetime_to_iso_8601_with_z_suffix(object_instance)

    raise TypeError("Type not serializable")


# Serializer: string -> UTF-8 bytes
StrBytesSerializer = lambda s: s.encode('utf-8')

# Serializer: protobuf object -> bytes
ProtoBytesSerializer = lambda p: p.SerializeToString()

# Serializer: dict -> JSON string (with datetime support)
JsonStrSerializer = lambda d: json.dumps(d, default=_datetime_json_serializer)

# Serializer: dict -> JSON bytes (with datetime support)
JsonBytesSerializer = lambda d: StrBytesSerializer(JsonStrSerializer(d))