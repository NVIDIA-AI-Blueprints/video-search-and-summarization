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
import axios, { AxiosError, AxiosResponse, InternalAxiosRequestConfig } from 'axios';
import useVSTUIStore from './StateManagement';

const nvAxios = axios.create({
    timeout: 60000,
});

// Add a request interceptor
nvAxios.interceptors.request.use(
    (config: InternalAxiosRequestConfig) => {
        // Do something before request is sent
        let proxy = window.location.pathname;
        if (proxy !== '/' && proxy.length > 0) {
            if (proxy[proxy.length - 1] === '/') {
                proxy = proxy.slice(0, -1);
            }
            const emdxEndpoint = useVSTUIStore.getState().emdxEndpoint;
            // If URL exists and either:
            // 1. There is no EMDX endpoint configured, or
            // 2. EMDX endpoint exists but URL doesn't contain it
            // Then replace /api with {proxy}/api in the URL to handle proxy deployments
            if (config.url && (!emdxEndpoint || (emdxEndpoint && !config.url.includes(emdxEndpoint)))) {
                config.url = config.url.replace('/api', `${proxy}/api`);
            }
        }
        return config;
    },
    (error: AxiosError) => {
        // Do something with request error
        return Promise.reject(error);
    }
);

// Add a response interceptor
nvAxios.interceptors.response.use(
    (response: AxiosResponse) => {
        // Any status code that lie within the range of 2xx cause this function to trigger
        // Do something with response data
        return response;
    },
    (error: AxiosError) => {
        // Any status codes that falls outside the range of 2xx cause this function to trigger
        // Do something with response error
        return Promise.reject(error);
    }
);

export default nvAxios;
