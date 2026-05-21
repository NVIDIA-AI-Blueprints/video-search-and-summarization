// SPDX-License-Identifier: MIT
/**
 * VST (VIOS) sensor list helpers — `GET /v1/sensor/list` maps friendly sensor
 * `name` → `sensorId` for online sensors. Shared by thumbnails and realtime alerts.
 */

export interface VstSensorListEntry {
  name?: string;
  sensorId?: string;
  state?: string;
}

export interface ResolvedVstSensor {
  sensor_name: string;
  sensor_id: string;
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
 * Extension is kept — NVStreamer registers sensors with the full filename and
 * VST/alert-bridge lookups match by exact name.
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
 * Resolve `sensor_name` and `sensor_id` for a live stream URL via VST sensor list.
 */
export const resolveSensorForLiveStreamUrl = async (
  vstApiUrl: string,
  liveStreamUrl: string,
): Promise<ResolvedVstSensor> => {
  const sensor_name = deriveSensorNameFromLiveStreamUrl(liveStreamUrl);
  if (!sensor_name) {
    throw new Error(
      'Could not derive sensor name from live_stream_url; check the RTSP path.',
    );
  }

  const map = await fetchSensorMap(vstApiUrl);
  const sensor_id = map.get(sensor_name);
  if (!sensor_id) {
    throw new Error(
      `Sensor "${sensor_name}" is not registered with VST (online). Register the stream first.`,
    );
  }

  return { sensor_name, sensor_id };
};
