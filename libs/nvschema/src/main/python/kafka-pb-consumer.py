# SPDX-FileCopyrightText: Copyright (c) 2022-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from aiokafka import AIOKafkaConsumer
import asyncio
import schema_pb2 as nvSchema
import logging
import ext_pb2 as nvExtSchema
import google.protobuf.json_format as json_format

logging.basicConfig(format="%(asctime)s - %(message)s",
                    datefmt="%y/%m/%d %H:%M:%S", level=logging.INFO)

async def main():
    consumer = AIOKafkaConsumer(
        "mdx-raw",
        bootstrap_servers='<kafka-broker:port>',
        group_id="mdx-pb-consumer",           # Consumer must be in a group to commit
        enable_auto_commit=True,      # Will enable autocommit
        auto_offset_reset="latest"
    )

    await consumer.start()

    async for msg in consumer:
        #print(msg.value) # type: <class 'bytes'>
        proto_message=nvSchema.Frame().FromString(msg.value) # Raw data
        #proto_message=nvExtSchema.FrameMessage().FromString(msg.value) # Frame data
        #proto_message=nvExtSchema.Behavior().FromString(msg.value) # Behavior/Behavior-plus/Tripwire/Alert data
        print(proto_message) # Protobuf message
        message_dict=json_format.MessageToDict(proto_message,including_default_value_fields=True)
        print(message_dict) # type: dict


asyncio.run(main())