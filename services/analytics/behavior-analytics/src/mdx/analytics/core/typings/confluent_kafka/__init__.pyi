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

from _typeshed import Incomplete

from ._model import ConsumerGroupState as ConsumerGroupState
from ._model import ConsumerGroupTopicPartitions as ConsumerGroupTopicPartitions
from ._model import ConsumerGroupType as ConsumerGroupType
from ._model import IsolationLevel as IsolationLevel
from ._model import Node as Node
from ._model import TopicCollection as TopicCollection
from ._model import TopicPartitionInfo as TopicPartitionInfo
from .cimpl import OFFSET_BEGINNING as OFFSET_BEGINNING
from .cimpl import OFFSET_END as OFFSET_END
from .cimpl import OFFSET_INVALID as OFFSET_INVALID
from .cimpl import OFFSET_STORED as OFFSET_STORED
from .cimpl import TIMESTAMP_CREATE_TIME as TIMESTAMP_CREATE_TIME
from .cimpl import TIMESTAMP_LOG_APPEND_TIME as TIMESTAMP_LOG_APPEND_TIME
from .cimpl import TIMESTAMP_NOT_AVAILABLE as TIMESTAMP_NOT_AVAILABLE
from .cimpl import Consumer as Consumer
from .cimpl import Message as Message
from .cimpl import Producer as Producer
from .cimpl import TopicPartition as TopicPartition
from .cimpl import Uuid as Uuid
from .cimpl import libversion as libversion
from .deserializing_consumer import DeserializingConsumer as DeserializingConsumer
from .error import KafkaError as KafkaError
from .error import KafkaException as KafkaException
from .serializing_producer import SerializingProducer as SerializingProducer

__all__ = [
    "Consumer",
    "Producer",
    "Message",
    "KafkaError",
    "KafkaException",
    "OFFSET_BEGINNING",
    "OFFSET_END",
    "OFFSET_INVALID",
    "OFFSET_STORED",
    "TIMESTAMP_CREATE_TIME",
    "TIMESTAMP_LOG_APPEND_TIME",
    "TIMESTAMP_NOT_AVAILABLE",
    "admin",
    "kafkatest",
    "libversion",
    "DeserializingConsumer",
    "SerializingProducer",
    "Node",
    "ConsumerGroupTopicPartitions",
    "ConsumerGroupState",
    "ConsumerGroupType",
    "Uuid",
    "IsolationLevel",
    "TopicCollection",
    "TopicPartitionInfo",
]

class ThrottleEvent:
    broker_name: Incomplete
    broker_id: Incomplete
    throttle_time: Incomplete
    def __init__(self, broker_name, broker_id, throttle_time) -> None: ...

# Names in __all__ with no definition:
#   admin
#   kafkatest
