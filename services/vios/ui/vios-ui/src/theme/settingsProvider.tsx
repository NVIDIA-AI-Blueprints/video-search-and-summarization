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
import React, { useEffect, useState } from 'react';
import store from 'store2';
import { logInfo } from '../utils/misc/Logs';
import SettingsContext, { UISettings } from './settingsContext';
import { DEFAULT_NETWORK_QUALITY_SETTINGS } from './defaultSettings';

interface ChildrenProps {
    children?: React.ReactNode;
}

const DEFAULT_SETTINGS: UISettings = {
    theme: 'dark',
    networkQualityWidget: DEFAULT_NETWORK_QUALITY_SETTINGS,
    analytics: {
        showSettings: false, // Default to hidden
    },
};

const getStoredSettings = (): UISettings => {
    const storedSettings = store('ui_settings');
    if (!storedSettings) {
        return DEFAULT_SETTINGS;
    }

    // Deep merge stored settings with defaults
    return {
        ...DEFAULT_SETTINGS,
        ...storedSettings,
        networkQualityWidget: {
            ...DEFAULT_SETTINGS.networkQualityWidget,
            ...(storedSettings.networkQualityWidget || {}),
            thresholds: {
                ...DEFAULT_SETTINGS.networkQualityWidget.thresholds,
                ...(storedSettings.networkQualityWidget?.thresholds || {}),
            },
        },
        analytics: {
            ...DEFAULT_SETTINGS.analytics,
            ...(storedSettings.analytics || {}),
        },
    };
};

const SettingsProvider: React.FC<ChildrenProps> = ({ children }) => {
    const [settings, setSettings] = useState<UISettings>(getStoredSettings());

    useEffect(() => {
        try {
            store.set('ui_settings', settings);
            logInfo('Current UI settings: ', settings);
        } catch (err) {
            logInfo('Error saving UI settings: ', err);
        }
    }, [settings]);

    const updateSettings = (newSettings: Partial<UISettings>) => {
        setSettings(prev => {
            // Deep merge of settings
            const updatedSettings = {
                ...prev,
                ...newSettings,
                networkQualityWidget: newSettings.networkQualityWidget
                    ? {
                          ...prev.networkQualityWidget,
                          ...newSettings.networkQualityWidget,
                          thresholds: {
                              ...prev.networkQualityWidget.thresholds,
                              ...newSettings.networkQualityWidget.thresholds,
                          },
                      }
                    : prev.networkQualityWidget,
                analytics: newSettings.analytics
                    ? {
                          ...prev.analytics,
                          ...newSettings.analytics,
                      }
                    : prev.analytics,
            };
            return updatedSettings;
        });
    };

    return <SettingsContext.Provider value={{ settings, updateSettings }}>{children}</SettingsContext.Provider>;
};

export default SettingsProvider;
