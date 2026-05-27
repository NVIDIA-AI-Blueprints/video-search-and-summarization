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
import React from 'react';
import { Navigate, useRoutes, RouteObject } from 'react-router-dom';

import UILayout from '../Layout';

import Settings from '../../pages/common/experimentalPages/Settings';

import NvStreamerDashboard from '../../pages/nvstreamer/NvStreamerDashboard';
import MediaConfiguration from '../../pages/nvstreamer/MediaConfiguration';
import MediaManagement from '../../pages/nvstreamer/MediaManagement';

import VSTDashboard from '../../pages/vst/VSTDashboard';
import SensorConfiguration from '../../pages/vst/SensorConfiguration';
import SensorDetails from '../../pages/vst/SensorDetails';
import SensorManagement from '../../pages/vst/SensorManagement';
import SensorRecording from '../../pages/vst/SensorRecording';
import StreamDetails from '../../pages/vst/StreamDetails';
import VSTMediaManagement from '../../pages/vst/MediaManagement';

import useVSTUIStore from '../../services/StateManagement';

type AdaptorType = 'streamer' | 'default';

const PATHS = {
    DASHBOARD: '/dashboard',
    SETTINGS: '/settings',
} as const;

const STREAMER_ROUTES: RouteObject[] = [
    {
        path: '/',
        element: <UILayout />,
        children: [
            { element: <Navigate to={PATHS.DASHBOARD} />, index: true },
            { path: 'dashboard', element: <NvStreamerDashboard /> },
            { path: 'media-configuration', element: <MediaConfiguration /> },
            { path: 'media-management', element: <MediaManagement /> },
            { path: 'media-streams', element: <></> },
            { path: 'media-upload', element: <></> },
            { path: 'settings', element: <Settings /> },
            { path: 'experimental', element: <></> },
        ],
    },
];

const DEFAULT_ROUTES: RouteObject[] = [
    {
        path: '/',
        element: <UILayout />,
        children: [
            { element: <Navigate to={PATHS.DASHBOARD} />, index: true },
            { path: 'dashboard', element: <VSTDashboard /> },
            { path: 'sensor-management', element: <SensorManagement /> },
            { path: 'sensor-configuration', element: <SensorConfiguration /> },
            { path: 'record-settings', element: <SensorRecording /> },
            { path: 'sensor-details', element: <SensorDetails /> },
            { path: 'media-management', element: <VSTMediaManagement /> },
            { path: 'stream-details', element: <StreamDetails /> },
            { path: 'live-streams', element: <></> },
            { path: 'recorded-streams', element: <></> },
            { path: 'video-wall', element: <></> },
            { path: 'settings', element: <Settings /> },
            { path: 'experimental', element: <></> },
        ],
    },
];

const Router = () => {
    const adaptorType = useVSTUIStore(state => state.vstAdaptorType) ?? ('default' as AdaptorType);
    const routesArr = adaptorType === 'streamer' ? STREAMER_ROUTES : DEFAULT_ROUTES;
    return useRoutes(routesArr);
};

export default Router;
