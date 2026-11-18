// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Environment access for both Map tab apps.
 *
 * Accepts the original CRA `REACT_APP_*` names and the Next.js
 * `NEXT_PUBLIC_*` names used by this repo.
 */

const ALIASES: Record<string, string[]> = {
  REACT_APP_API_URL_BASE: [
    'NEXT_PUBLIC_VIDEO_ANALYTICS_WEB_API_URL',
    'REACT_APP_API_URL_BASE',
    'REACT_APP_API_URL',
  ],
  REACT_APP_API_URL: [
    'NEXT_PUBLIC_VIDEO_ANALYTICS_WEB_API_URL',
    'REACT_APP_API_URL_BASE',
    'REACT_APP_API_URL',
  ],
  REACT_APP_MEDIA_STREAMING_URL_BASE: [
    'NEXT_PUBLIC_VIOS_API_URL',
    'REACT_APP_MEDIA_STREAMING_URL_BASE',
  ],
  REACT_APP_GOOGLE_MAPS_API_KEY: [
    'NEXT_PUBLIC_GOOGLE_MAPS_API_KEY',
    'REACT_APP_GOOGLE_MAPS_API_KEY',
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

export function stripTrailingSlash(url: string): string {
  let end = url.length;
  while (end > 0 && url.charCodeAt(end - 1) === 47) {
    end -= 1;
  }
  return end === url.length ? url : url.slice(0, end);
}

/** True when the URL path ends in a version segment such as `/v1`. */
function hasVersionSuffix(url: string): boolean {
  const slash = url.lastIndexOf('/');
  if (slash < 0 || slash >= url.length - 2) return false;
  const head = url.charCodeAt(slash + 1);
  if (head !== 86 && head !== 118) return false;
  for (let index = slash + 2; index < url.length; index += 1) {
    const code = url.charCodeAt(index);
    if (code < 48 || code > 57) return false;
  }
  return true;
}

/**
 * `NEXT_PUBLIC_VIOS_API_URL` is the VST root (`…/vst/api`). Map playback
 * calls `/sensor/list` and `/replay/stream` on that root, which live under `/v1`.
 */
export function vstApiV1Base(url: string | undefined): string | undefined {
  if (!url) return undefined;
  const trimmed = stripTrailingSlash(url.trim());
  if (!trimmed) return undefined;
  if (hasVersionSuffix(trimmed)) return trimmed;
  return `${trimmed}/v1`;
}

type MapServiceEndpoints = {
  apiUrl?: string | null;
  mediaStreamingUrl?: string | null;
  googleMapsApiKey?: string | null;
};

/**
 * Map callers pass analytics and VIOS URLs as props. Calibration and playback
 * still read them through getEnv, so copy the props into the runtime env
 * before those requests run. A playback URL that already ends in a version
 * segment is stored as the VIOS root; vstApiV1Base appends the version.
 */
export function applyMapServiceEndpoints(data?: MapServiceEndpoints | null): void {
  if (typeof window === 'undefined' || !data) return;
  const current =
    (window as unknown as { __ENV?: Record<string, string> }).__ENV || {};
  const next = { ...current };
  if (data.apiUrl) {
    next.NEXT_PUBLIC_VIDEO_ANALYTICS_WEB_API_URL = data.apiUrl;
  }
  if (data.mediaStreamingUrl) {
    const playbackBase = stripTrailingSlash(data.mediaStreamingUrl.trim());
    next.NEXT_PUBLIC_VIOS_API_URL = hasVersionSuffix(playbackBase)
      ? playbackBase.slice(0, playbackBase.lastIndexOf('/'))
      : playbackBase;
  }
  if (data.googleMapsApiKey) {
    next.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY = data.googleMapsApiKey;
  }
  (window as unknown as { __ENV?: Record<string, string> }).__ENV = next;
}
