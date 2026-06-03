/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
import { SensorStreamData } from '../../interfaces/interfaces';

interface StreamMetadata {
    bitrate: string;
    codec: string;
    framerate: string;
    govlength: string;
    resolution: string;
}

interface OriginalStream {
    isMain: boolean;
    metadata: StreamMetadata;
    name: string;
    streamId: string;
    url: string;
    vodUrl: string;
}

type OriginalJson = Record<string, OriginalStream[]>[];

export default function streamsToJSONConvertor(originalJson: OriginalJson): {
    sensors: SensorStreamData[];
} {
    const sensors: SensorStreamData[] = originalJson.map(sensorObject => {
        const sensorId = Object.keys(sensorObject)[0];
        const streams = sensorObject[sensorId] || [];

        return {
            id: sensorId,
            name: streams.length > 0 ? decodeURIComponent(streams[0].name) : sensorId,
            streams: streams.map(stream => ({
                isMain: stream.isMain,
                metadata: stream.metadata,
                name: decodeURIComponent(stream.name),
                streamId: stream.streamId,
                url: stream.url,
                vodUrl: stream.vodUrl,
            })),
        };
    });

    return { sensors };
}
