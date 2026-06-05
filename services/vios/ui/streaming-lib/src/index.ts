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

import 'webrtc-adapter';
import StreamManager from './StreamManager';
import { StreamType } from './utils/apis';
import {
    StreamConfig,
    StreamState,
    StreamOverlayOptions,
    StreamCompositeOptions,
    BboxOverlayOptions,
    TripwireOverlayOptions,
    RoiOverlayOptions,
    RetryConfig,
    StreamRestartInfo,
} from './utils/interfaces';
import { AppConfig } from './StreamManager';
import { ErrorCode, ErrorType } from './utils/error';
import { WebRTCIssue, WebRTCNetworkScores, WebRTCIssueDetectorCallbacks } from './webrtc/WebRTCIssueDetector';

export default StreamManager;
export type {
    AppConfig,
    StreamConfig,
    ErrorType,
    StreamOverlayOptions,
    StreamCompositeOptions,
    BboxOverlayOptions,
    TripwireOverlayOptions,
    RoiOverlayOptions,
    WebRTCIssue,
    WebRTCNetworkScores,
    WebRTCIssueDetectorCallbacks,
    RetryConfig,
    StreamRestartInfo,
};
export { StreamType, StreamState, ErrorCode };
