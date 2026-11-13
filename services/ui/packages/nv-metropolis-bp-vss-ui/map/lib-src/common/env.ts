// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Environment access for warehouse analytics.
 *
 * Accepts the original CRA `REACT_APP_*` names and the Next.js
 * `NEXT_PUBLIC_*` names used by this repo.
 */

const ALIASES: Record<string, string[]> = {
  REACT_APP_API_URL_BASE: [
    'NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_URL',
    'NEXT_PUBLIC_MDX_WEB_API_URL',
    'REACT_APP_API_URL_BASE',
    'REACT_APP_API_URL',
  ],
  REACT_APP_API_URL: [
    'NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_URL',
    'NEXT_PUBLIC_MDX_WEB_API_URL',
    'REACT_APP_API_URL_BASE',
    'REACT_APP_API_URL',
  ],
  REACT_APP_MEDIA_STREAMING_URL_BASE: [
    'NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_URL',
    'REACT_APP_MEDIA_STREAMING_URL_BASE',
  ],
  REACT_APP_GOOGLE_MAPS_API_KEY: [
    'NEXT_PUBLIC_GOOGLE_MAPS_API_KEY',
    'REACT_APP_GOOGLE_MAPS_API_KEY',
  ],
  REACT_APP_OBTAIN_DYNAMIC_SERVICE_URLS: [
    'NEXT_PUBLIC_WAREHOUSE_OBTAIN_DYNAMIC_SERVICE_URLS',
    'REACT_APP_OBTAIN_DYNAMIC_SERVICE_URLS',
  ],
  REACT_APP_ANALYTICS_API_BASE_URL_VERSION: [
    'NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_BASE_URL_VERSION',
    'REACT_APP_ANALYTICS_API_BASE_URL_VERSION',
  ],
  REACT_APP_MEDIA_STREAMING_API_VERSION: [
    'NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_API_VERSION',
    'REACT_APP_MEDIA_STREAMING_API_VERSION',
  ],
};

const readNextRuntimeEnv = (name: string): string | undefined => {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const runtime = require('next-runtime-env') as {
      env?: (key: string) => string | undefined;
    };
    const value = runtime.env?.(name);
    return value || undefined;
  } catch {
    return undefined;
  }
};

export function getEnv(name: string, defaultValue?: string): string | undefined {
  const candidates = ALIASES[name] || [name];
  for (const key of candidates) {
    const runtime =
      typeof window !== 'undefined'
        ? (window as unknown as { __ENV?: Record<string, string> }).__ENV?.[key]
        : undefined;
    const nextRuntime = readNextRuntimeEnv(key);
    const buildTime = typeof process !== 'undefined' ? process.env[key] : undefined;
    const value = runtime || nextRuntime || buildTime;
    if (value) return value;
  }
  return defaultValue;
}

export function isDevelopment(): boolean {
  return process.env.NODE_ENV === 'development';
}

export function isProduction(): boolean {
  return process.env.NODE_ENV === 'production';
}

export function isObtainDynamicServiceUrls(): boolean {
  return getEnv('REACT_APP_OBTAIN_DYNAMIC_SERVICE_URLS') === 'true';
}

export function stripTrailingSlash(url: string): string {
  return url.replace(/\/+$/, '');
}
