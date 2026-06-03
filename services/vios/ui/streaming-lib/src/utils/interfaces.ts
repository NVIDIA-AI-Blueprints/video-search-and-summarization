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

export interface IceServer {
    urls: string[];
    username?: string;
    credential?: string;
}

export interface BitrateSettings {
    bitrate_min: number | null;
    bitrate_max: number | null;
    bitrate_start: number | null;
}

export interface StreamOptions {
    quality?: string;
    rtptransport?: string;
    timeout?: number;
    framerate?: number;
    composite?: StreamCompositeOptions;
    overlay?: StreamOverlayOptions;
}

export interface StreamConfig {
    streamId?: string;
    mainStreamId?: string;
    streamIds?: string[];
    startTime?: string;
    endTime?: string;
    options: StreamOptions;
    tag?: string;
}

export enum StreamState {
    NOT_PLAYING = 'NOT_PLAYING',
    PLAYING = 'PLAYING',
    PAUSED = 'PAUSED',
    ERROR = 'ERROR',
}

export interface StreamStatusResponse {
    error: boolean;
    state: StreamState;
}

export interface StreamQueryResponse {
    ts: number;
}

export interface BboxOverlayOptions {
    showAll?: boolean;
    objectId?: number[];
    classType?: string[];
    showObjId?: boolean;
    objIdPosition?: number;
    objIdTextColor?: string;
    objIdTextBGColor?: string;
}

export interface TripwireOverlayOptions {
    showAll?: boolean;
    id?: number[];
}

export interface RoiOverlayOptions {
    showAll?: boolean;
    id?: number[];
}

export interface StreamOverlayOptions {
    bbox?: BboxOverlayOptions;
    tripwire?: TripwireOverlayOptions;
    roi?: RoiOverlayOptions;
    color?: string;
    thickness?: number;
    debug?: boolean;
    opacity?: number;
    proximityClass?: string[];
    entrantClass?: string[];
    proximityAreaFactor?: number;
    overlayColorCode?: Array<{
        [key: string]: [number, number, number, number];
    }>;
    proximityAnimation?: string;
    pose?: boolean;
    needHalo?: boolean;
}

export interface StreamCompositeOptions {
    includeFloorPlan?: boolean;
    doComposite?: boolean;
    streamIds?: string[];
    showSensorName?: {
        enable?: boolean;
        position?: [number, number];
    };
}

export interface WebRTCConfig {
    WebrtcOutDefaultResolution: string;
    webrtc_video_quality_tunning: {
        resolution_480: {
            bitrate_range: [number, number];
            bitrate_start: number;
        };
        resolution_720: {
            bitrate_range: [number, number];
            bitrate_start: number;
        };
        resolution_1080: {
            bitrate_range: [number, number];
            bitrate_start: number;
        };
        resolution_1440: {
            bitrate_range: [number, number];
            bitrate_start: number;
        };
        resolution_2160: {
            bitrate_range: [number, number];
            bitrate_start: number;
        };
    };
}

// VST WebSocket Message Payloads
export interface VstMessage {
    apiKey: string;
    peerId: string;
    data?: unknown;
}

export interface SdpOfferPayload {
    sessionDescription: RTCSessionDescriptionInit;
    streamId: string;
}

export interface SdpAnswerPayload {
    sessionDescription: RTCSessionDescriptionInit;
}

export type IceCandidatePayload = RTCIceCandidateInit[];

export interface IceServersPayload {
    iceServers: IceServer[];
}

export interface RetryConfig {
    maxRetries: number;
    retryDelayMs: number;
    backoffMultiplier: number;
    maxRetryDelayMs: number;
}

export interface RetryState {
    currentRetryCount: number;
    isRetrying: boolean;
    lastRetryTime: number;
    retryTimer: NodeJS.Timeout | null;
}

export interface StreamRestartInfo {
    type: 'websocket' | 'webrtc';
    reason: string;
    retryAttempt: number;
    timestamp: number;
    previousFailureTime: number;
}
