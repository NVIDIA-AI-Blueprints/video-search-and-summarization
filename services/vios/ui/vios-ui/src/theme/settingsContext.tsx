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
import { createContext } from 'react';
import { PaletteMode } from '@mui/material';
import { DEFAULT_NETWORK_QUALITY_SETTINGS } from './defaultSettings';

export interface UISettings {
    theme: PaletteMode;
    networkQualityWidget: {
        initialDelayMs: number;
        consecutiveIssuesThreshold: number;
        widgetDisplayDurationMs: number;
        userHideDurationMs: number;
        maxGraphPoints: number;
        thresholds: {
            severePacketLoss: number;
            severeJitterMs: number;
            lowFps: number;
            highPli: number;
            highNack: number;
            highFir: number;
            highJitterMs: number;
            moderatePacketLoss: number;
            moderateNack: number;
            moderatePli: number;
            moderateFir: number;
            highLatencyMs: number;
        };
    };
    analytics: {
        showSettings: boolean;
    };
}

export interface SettingsContextProps {
    settings: UISettings;
    updateSettings: (newSettings: Partial<UISettings>) => void;
}

const SettingsContext = createContext<SettingsContextProps>({
    settings: {
        theme: 'dark',
        networkQualityWidget: DEFAULT_NETWORK_QUALITY_SETTINGS,
        analytics: {
            showSettings: false,
        },
    },
    updateSettings: () => {},
});

export default SettingsContext;
