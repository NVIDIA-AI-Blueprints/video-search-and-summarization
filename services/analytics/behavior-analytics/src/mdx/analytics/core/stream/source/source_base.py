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

import re
import google.protobuf.message as pbm

from abc import ABC, abstractmethod
from typing import Any
from collections.abc import Callable

from mdx.analytics.core.schema.models import StreamMessage


class Source(ABC):
    """Abstract base class for stream data sources.
    
    This class provides the interface for reading and polling messages from various
    stream sources. Concrete implementations must define the read method.
    """

    def poll(
        self,
        src_key: str,
        msg_deserializer: Callable,
        group_id_suffix: str | None = None
    ) -> list[Any]:
        """Poll messages from the source and deserialize them.
        
        Reads raw messages from the source and applies the provided deserializer
        function to each message.
        
        :param str src_key: The key identifying the source to poll from.
        :param Callable msg_deserializer: Function to deserialize raw messages.
        :param str | None group_id_suffix: Suffix to append to group ID.
        :return list[Any]: List of deserialized messages.
        """

        return [ msg_deserializer(s_msg) for s_msg in self.read(src_key, group_id_suffix) ]


    @abstractmethod
    def read(
        self,
        src_key: str,
        group_id_suffix: str | None = None
    ) -> list[StreamMessage]:
        """Read raw messages from the source.
        
        Abstract method that must be implemented by concrete source classes
        to read messages from their specific source type.
        
        :param str src_key: The key identifying the source to read from.
        :param str | None group_id_suffix: Suffix to append to group ID.
        :return list[StreamMessage]: List of raw stream messages.
        """

        raise NotImplementedError("Subclasses must implement method before invoking")


    def close(self) -> None:
        """Close the source and clean up resources.
        
        Default implementation does nothing. Subclasses can override to
        perform cleanup operations like closing connections or releasing resources.

        :return: None
        """
        return None


    def _get_group_id(self, src: str, group: str, group_id_suffix: str | None = None) -> str:
        """Generate a formatted group ID string.
        
        Creates a group ID by combining the group name with a sanitized source
        identifier and optional suffix.
        
        :param str src: Source identifier to include in group ID.
        :param str group: Base group name.
        :param str | None group_id_suffix: Additional suffix to append.
        :return str: Formatted group ID string.
        """

        group_id = f"{group} - {re.sub(r'[^a-zA-Z0-9._-]', '', src)}"

        return f"{group_id} - {group_id_suffix}" if group_id_suffix else group_id


# Deserializer: bytes -> UTF-8 string
BytesStrDeserializer: Callable[[bytes], str] = lambda b: b.decode('utf-8')


class BytesMessageProtoDeserializer:
    """Deserializer for converting bytes to protobuf messages."""

    def __init__(self, to_type: type[pbm.Message]) -> None:
        """Initialize the deserializer with target protobuf message type.
        
        :param type[pbm.Message] to_type: The protobuf message class to deserialize to.
        """

        self._to_type = to_type

    def __call__(self, b: bytes) -> pbm.Message | None:
        """Deserialize bytes to protobuf message.
        
        Converts raw bytes to the specified protobuf message type.
        
        :param bytes b: Raw byte data containing serialized protobuf message.
        :return pbm.Message | None: Deserialized protobuf message, or None if input bytes are empty/None.
        :raises google.protobuf.message.DecodeError: If bytes cannot be parsed as the specified protobuf message type.
        """

        return self._to_type.FromString(b) if b else None


class StreamMessageProtoDeserializer:
    """Deserializer for converting StreamMessage objects to protobuf messages."""

    def __init__(self, to_type: type[pbm.Message]) -> None:
        """Initialize the deserializer with target protobuf message type.
        
        :param type[pbm.Message] to_type: The protobuf message class to deserialize to.
        """

        self._bytes_proto_deserializer = BytesMessageProtoDeserializer(to_type)

    def __call__(self, s_msg: StreamMessage) -> pbm.Message | None:
        """Deserialize StreamMessage to protobuf message.
        
        Extracts byte data from a StreamMessage and converts it to the
        specified protobuf message type.
        
        :param StreamMessage s_msg: Stream message containing byte data.
        :return pbm.Message | None: Deserialized protobuf message, or None if the stream message contains no byte data.
        :raises google.protobuf.message.DecodeError: If the stream message bytes cannot be parsed as the specified protobuf message type.
        """

        return self._bytes_proto_deserializer(s_msg.value_bytes)
