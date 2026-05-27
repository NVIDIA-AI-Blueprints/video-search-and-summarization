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
import { Config } from './interfaces/interfaces';

const isDevelopment = () => {
    return process.env.NODE_ENV === 'development';
};

const getPort = () => {
    return isDevelopment() ? '30000' : window.location.port;
};

const mdatWebAPIDefaultPort = '8081';
const analyticsUIServerDefaultPort = '8003';

const config: Config = {
    sensorManagementEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    streamRecorderEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    storageManagementEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    liveStreamEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    replayStreamEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,
    streambridgeEndpoint: `${window.location.protocol}//${window.location.hostname}:${getPort()}`,

    mdatWebApiEndpoint: `${window.location.protocol}//${window.location.hostname}:${mdatWebAPIDefaultPort}`,
    analyticsUIServerEndpoint: `${window.location.protocol}//${window.location.hostname}:${analyticsUIServerDefaultPort}`,
    enableLogs: true, // enable-disable console logs
};

export default config;
