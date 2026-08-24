// SPDX-License-Identifier: MIT
/**
 * VST (VIOS) sensor helpers.
 *
 *  - `GET /v1/sensor/list` maps friendly sensor `name` → `sensorId` for online
 *    sensors. Used by the thumbnail component.
 *  - `GET /v1/live/streams` returns the live-stream catalog — each entry
 *    carries `name`, RTSP `url`, and `streamId`. Used by the realtime alert
 *    creator so users can pick a sensor by name (matching the chat flow in
 *    `services/agent/.../rtvi_vlm_alert.py`) instead of pasting an RTSP URL.
 */

export interface VstSensorListEntry {
  name?: string;
  sensorId?: string;
  state?: string;
}

export interface VstLiveStream {
  name: string;
  url: string;
  streamId: string;
}

export interface ResolvedVstStream {
  sensor_name: string;
  live_stream_url: string;
}

// TTL ensures sensors registered elsewhere appear without a hard reload.
const SENSOR_LIST_TTL_MS = 60_000;

interface SensorMapCacheEntry {
  promise: Promise<Map<string, string>>;
  createdAt: number;
}

const sensorListCache = new Map<string, SensorMapCacheEntry>();

/** Strip trailing `/` characters in O(n) without regex (Sonar S5852). */
const stripTrailingSlashes = (value: string): string => {
  let end = value.length;
  while (end > 0 && value.charCodeAt(end - 1) === 47) {
    end -= 1;
  }
  return end === value.length ? value : value.slice(0, end);
};

/** Drop `?query` and `#fragment` in O(n) without regex (Sonar S5852). */
const stripUrlQueryAndFragment = (value: string): string => {
  const query = value.indexOf('?');
  const fragment = value.indexOf('#');
  let end = value.length;
  if (query >= 0) {
    end = Math.min(end, query);
  }
  if (fragment >= 0) {
    end = Math.min(end, fragment);
  }
  return end === value.length ? value : value.slice(0, end);
};

export const clearSensorListCache = (vstApiUrl?: string): void => {
  if (vstApiUrl) {
    sensorListCache.delete(vstApiUrl);
  } else {
    sensorListCache.clear();
  }
};

/**
 * Cached map of VST sensor `name` → `sensorId` (online sensors only).
 */
export const fetchSensorMap = (
  vstApiUrl: string,
  options?: { forceRefresh?: boolean },
): Promise<Map<string, string>> => {
  const now = Date.now();
  const cached = sensorListCache.get(vstApiUrl);
  if (
    cached &&
    !options?.forceRefresh &&
    now - cached.createdAt < SENSOR_LIST_TTL_MS
  ) {
    return cached.promise;
  }

  const promise = fetch(`${stripTrailingSlashes(vstApiUrl)}/v1/sensor/list`)
    .then((response) => {
      if (!response.ok) {
        throw new Error(`VST /v1/sensor/list returned ${response.status}`);
      }
      return response.json();
    })
    .then((data) => {
      const map = new Map<string, string>();
      if (Array.isArray(data)) {
        for (const entry of data as VstSensorListEntry[]) {
          // Online-only — same convention as useAlerts/useFilter.
          if (entry?.name && entry?.sensorId && entry.state === 'online') {
            map.set(entry.name, entry.sensorId);
          }
        }
      }
      return map;
    })
    .catch((err) => {
      // Evict failed entry so subsequent renders can retry before TTL.
      const existing = sensorListCache.get(vstApiUrl);
      if (existing && existing.promise === promise) {
        sensorListCache.delete(vstApiUrl);
      }
      throw err;
    });

  sensorListCache.set(vstApiUrl, { promise, createdAt: now });
  return promise;
};

/**
 * Last path segment of the RTSP URL, e.g.
 * `rtsp://.../sample-warehouse-ladder.mp4` → `sample-warehouse-ladder.mp4`.
 * Used only as a thumbnail fallback for legacy rules saved before the server
 * began returning `sensor_name`. Not safe for live-stream URLs with UUID paths
 * (`rtsp://host:port/live/<uuid>`) — that's why rule creation goes through the
 * live-stream catalog instead.
 */
export const deriveSensorNameFromLiveStreamUrl = (
  liveStreamUrl: string,
): string | undefined => {
  const trimmed = liveStreamUrl.trim();
  if (!trimmed) return undefined;
  const pathOnly = stripUrlQueryAndFragment(trimmed);
  const segments = pathOnly.split('/').filter(Boolean);
  const last = segments.at(-1);
  return last || undefined;
};

/**
 * Live-stream catalog from `GET /v1/live/streams`. Not cached — sensors can
 * be added/removed in VST from another window, and the picker needs to reflect
 * those changes immediately. The wire shape nests one entry per stream key —
 * `[{<key>: [{name, url, streamId}]}, …]` — so we flatten to `VstLiveStream[]`.
 */
export const fetchVstLiveStreamCatalog = async (
  vstApiUrl: string,
): Promise<VstLiveStream[]> => {
  const response = await fetch(`${stripTrailingSlashes(vstApiUrl)}/v1/live/streams`);
  if (!response.ok) {
    throw new Error(`VST /v1/live/streams returned ${response.status}`);
  }
  // VST returns text/plain content-type but the body is JSON.
  const text = await response.text();
  const data = JSON.parse(text) as unknown;
  const result: VstLiveStream[] = [];
  if (Array.isArray(data)) {
    for (const item of data) {
      if (!item || typeof item !== 'object') continue;
      for (const streams of Object.values(item) as unknown[]) {
        if (!Array.isArray(streams) || streams.length === 0) continue;
        const info = streams[0] as Record<string, unknown>;
        const name = typeof info.name === 'string' ? info.name : undefined;
        const url = typeof info.url === 'string' ? info.url : undefined;
        const streamId =
          typeof info.streamId === 'string' ? info.streamId : undefined;
        if (name && url && streamId) {
          result.push({ name, url, streamId });
        }
      }
    }
  }
  return result;
};

/**
 * Look up a sensor name in the VST live-stream catalog. Returns `undefined`
 * when the catalog has no matching entry — callers can decide whether to
 * forward the name to Alert Bridge anyway (e.g. for a stream that hasn't been
 * registered yet).
 */
export const resolveSensorByName = async (
  vstApiUrl: string,
  sensorName: string,
): Promise<ResolvedVstStream | undefined> => {
  const trimmed = sensorName.trim();
  if (!trimmed) return undefined;

  const catalog = await fetchVstLiveStreamCatalog(vstApiUrl);
  const match = catalog.find((entry) => entry.name === trimmed);
  if (!match) return undefined;

  return {
    sensor_name: match.name,
    live_stream_url: match.url,
  };
};
