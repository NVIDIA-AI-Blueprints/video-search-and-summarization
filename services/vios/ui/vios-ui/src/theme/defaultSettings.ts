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
export const DEFAULT_NETWORK_QUALITY_SETTINGS = {
    initialDelayMs: 10000,
    consecutiveIssuesThreshold: 10,
    widgetDisplayDurationMs: 10000,
    userHideDurationMs: 900000,
    maxGraphPoints: 20,
    thresholds: {
        // Critical Issues (Red)
        severePacketLoss: 20,
        severeJitterMs: 200,
        lowFps: 10,

        // Warning Issues (Orange)
        highPli: 8,
        highNack: 15,
        highFir: 8,
        highJitterMs: 200,
        moderatePacketLoss: 15,
        moderateNack: 8,
        moderatePli: 4,
        moderateFir: 4,
        highLatencyMs: 300,
    },
};
