/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

export type BaseAPIs = {
    startStream: string;
    stopStream: string;
    iceServers: string;
    configuration: string;
    iceCandidate: string;
    setAnswer: string;
    peerConnectionStatus: string;
    ping: string;
    streamStatus: string;
    streamQuery: string;
};

const APIPaths: Record<StreamType, BaseAPIs> = {
    streambridge: {
        startStream: 'api/v1/streambridge/stream/start',
        stopStream: 'api/v1/streambridge/stream/stop',
        iceServers: 'api/v1/streambridge/iceServers',
        configuration: 'api/v1/streambridge/configuration',
        iceCandidate: 'api/v1/streambridge/iceCandidate',
        setAnswer: 'api/v1/streambridge/setAnswer',
        peerConnectionStatus: '/event/streambridge/ext/peerconnection/status',
        ping: 'api/v1/streambridge/ping',
        streamStatus: 'api/v1/streambridge/stream/status',
        streamQuery: 'api/v1/streambridge/stream/query',
    },
    live: {
        startStream: 'api/v1/live/stream/start',
        stopStream: 'api/v1/live/stream/stop',
        iceServers: 'api/v1/live/iceServers',
        configuration: 'api/v1/live/configuration',
        iceCandidate: 'api/v1/live/iceCandidate',
        setAnswer: 'api/v1/live/setAnswer',
        peerConnectionStatus: '',
        ping: 'api/v1/live/ping',
        streamStatus: 'api/v1/live/stream/status',
        streamQuery: 'api/v1/live/stream/query',
    },
    replay: {
        startStream: 'api/v1/replay/stream/start',
        stopStream: 'api/v1/replay/stream/stop',
        iceServers: 'api/v1/replay/iceServers',
        configuration: 'api/v1/replay/configuration',
        iceCandidate: 'api/v1/replay/iceCandidate',
        setAnswer: 'api/v1/replay/setAnswer',
        peerConnectionStatus: '',
        ping: 'api/v1/replay/ping',
        streamStatus: 'api/v1/replay/stream/status',
        streamQuery: 'api/v1/replay/stream/query',
    },
    videowall: {
        startStream: 'api/v1/live/stream/start',
        stopStream: 'api/v1/live/stream/stop',
        iceServers: 'api/v1/live/iceServers',
        configuration: 'api/v1/live/configuration',
        iceCandidate: 'api/v1/live/iceCandidate',
        setAnswer: 'api/v1/live/setAnswer',
        peerConnectionStatus: '',
        ping: 'api/v1/live/ping',
        streamStatus: 'api/v1/live/stream/status',
        streamQuery: 'api/v1/live/stream/query',
    },
};

export enum StreamType {
    Streambridge = 'streambridge',
    Live = 'live',
    Replay = 'replay',
    VideoWall = 'videowall',
}

export default function getAPIPath(key: StreamType): (typeof APIPaths)[keyof typeof APIPaths] {
    return APIPaths[key];
}
