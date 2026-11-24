/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

const isDevelopment = () => {
  return import.meta.env.DEV;
};

const getPort = () => {
  return isDevelopment() ? '8000' : window.location.port;
};

// API URL:
// - Development (npm run dev): uses localhost:8000
// - Production (Docker): MUST provide API_URL via docker run -e API_URL=...
const getApiUrl = () => {
  const configuredApiUrl = import.meta.env.VITE_API_URL;
  if (configuredApiUrl) {
    return configuredApiUrl.replace(/\/$/, '');
  }

  // Development mode: use localhost:8000
  if (isDevelopment()) {
    return `${window.location.protocol}//${window.location.hostname}:${getPort()}`;
  }

  // Production mode: API_URL is mandatory
  const windowEnv = (window as any).ENV;
  if (windowEnv?.API_URL && windowEnv.API_URL !== "__API_URL_PLACEHOLDER__" && windowEnv.API_URL !== "") {
    return windowEnv.API_URL;
  }

  // No API_URL provided in production - show error
  console.error("ERROR: API_URL is required! Run container with: docker run -e API_URL=http://your-server:port ...");
  return "API_URL_NOT_PROVIDED";
};

export const API_BASE_URL = getApiUrl();

export const APP_CONFIG = {
  name: 'AutoMagicCalib',
  version: '3.3.0',
  organization: 'NVIDIA',
  enableLogs: true,
};

export const UI_CONFIG = {
  primaryColor: '#76b900',
  secondaryColor: '#1a1a1a',
  snackbarDuration: 6000,
  warningSnackbarDuration: 12000,
};

const config = {
  apiEndpoint: API_BASE_URL,
  enableLogs: APP_CONFIG.enableLogs,
};

export default config;
