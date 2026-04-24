// SPDX-License-Identifier: MIT
/**
 * Custom React hook for managing alerts data fetching and state
 * 
 * This hook provides comprehensive alerts data management including API calls,
 * sensor mapping, error handling, and real-time data synchronization with
 * configurable time windows and verification filters.
 *
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { AlertData, VlmVerdict, VLM_VERDICT, FilterState } from '../types';

/**
 * ISO-8601 instant in UTC with optional fractional seconds (`Z` or `+00:00`).
 * Fractional digits are interpreted exactly (not rounded) so paging cursors stay strictly after `end`.
 */
const ISO_INSTANT_UTC = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.(\d+))?(Z|\+00:00)$/;
const NS_PER_MS = BigInt(1000000);
const NS_PER_SECOND = BigInt(1000000000);

/**
 * Load-more **toTimestamp** = min(`end`) − this many ms. Helps when the API treats `toTimestamp` as an
 * **inclusive** upper bound on `end`: subtracting pulls `to` below the loaded minimum `end` so the
 * same “bottom” rows are not returned again (try vs +offset per backend contract).
 */
export const LOAD_MORE_TO_TIMESTAMP_SUBTRACT_MS = 1;

/**
 * Nanoseconds since Unix epoch. For `...Z` / `...+00:00` uses the string's fractional digits with
 * rational arithmetic (no float). Other formats fall back to `Date.parse` (millisecond precision).
 */
export function isoUtcToEpochNanoseconds(iso: string): bigint | null {
  const s = iso.trim();
  const m = ISO_INSTANT_UTC.exec(s);
  if (!m) {
    const t = Date.parse(s);
    if (Number.isNaN(t)) return null;
    return BigInt(t) * NS_PER_MS;
  }
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  const h = Number(m[4]);
  const mi = Number(m[5]);
  const sec = Number(m[6]);
  const epochMsAtWholeSecond = Date.UTC(y, mo - 1, d, h, mi, sec, 0);
  const fracDigits = m[8];
  if (!fracDigits) {
    return BigInt(epochMsAtWholeSecond) * NS_PER_MS;
  }
  const len = fracDigits.length;
  const num = BigInt(fracDigits);
  const scale = BigInt(10) ** BigInt(len);
  const fracNs = (num * NS_PER_SECOND) / scale;
  return BigInt(epochMsAtWholeSecond) * NS_PER_MS + fracNs;
}

const BIGINT_ZERO = BigInt(0);

/** After adding whole milliseconds, map resulting instant to ISO using ms ceil when sub-ms remainder exists. */
function epochNsToIsoAfterAdd(nextNs: bigint): string {
  if (nextNs % NS_PER_MS === BIGINT_ZERO) {
    return new Date(Number(nextNs / NS_PER_MS)).toISOString();
  }
  const epochMsCeil = (nextNs + BigInt(999999)) / NS_PER_MS;
  return new Date(Number(epochMsCeil)).toISOString();
}

/** After subtracting whole milliseconds, map resulting instant to ISO at containing millisecond (floor). */
function epochNsToIsoFloorMs(nextNs: bigint): string {
  return new Date(Number(nextNs / NS_PER_MS)).toISOString();
}

/**
 * `iso` + `deltaMs` whole milliseconds (UTC), preserving precision like {@link isoUtcToEpochNanoseconds}.
 */
export function addMillisecondsIso(iso: string, deltaMs: number): string | null {
  if (!Number.isInteger(deltaMs) || deltaMs < 1) return null;
  const ns = isoUtcToEpochNanoseconds(iso.trim());
  if (ns === null) return null;
  const nextNs = ns + BigInt(deltaMs) * NS_PER_MS;
  return epochNsToIsoAfterAdd(nextNs);
}

/** Strictly after `iso` by 1 ms (shorthand for {@link addMillisecondsIso}(iso, 1)). */
export function addOneMillisecondIso(iso: string): string | null {
  return addMillisecondsIso(iso, 1);
}

/**
 * `iso` − `deltaMs` whole milliseconds (UTC). Truncates sub-ms remainders to the containing ms for a
 * stable query string (same ns basis as {@link isoUtcToEpochNanoseconds}).
 */
export function subtractMillisecondsIso(iso: string, deltaMs: number): string | null {
  if (!Number.isInteger(deltaMs) || deltaMs < 1) return null;
  const ns = isoUtcToEpochNanoseconds(iso.trim());
  if (ns === null) return null;
  const nextNs = ns - BigInt(deltaMs) * NS_PER_MS;
  if (nextNs < BIGINT_ZERO) return null;
  return epochNsToIsoFloorMs(nextNs);
}

/**
 * Smallest `end` across all loaded incidents (load-more **toTimestamp** = this value −
 * {@link LOAD_MORE_TO_TIMESTAMP_SUBTRACT_MS}). Returns the **verbatim** trimmed field from the row.
 * If no row has a parseable `end`, uses smallest parseable `timestamp` the same way.
 */
export function getMinEndIsoForPaging(loaded: AlertData[]): string | null {
  let minEndNs: bigint | null = null;
  let minEndIso: string | null = null;
  let minTsNs: bigint | null = null;
  let minTsIso: string | null = null;

  for (const a of loaded) {
    const end = a.end?.trim();
    if (end) {
      const ns = isoUtcToEpochNanoseconds(end);
      if (ns !== null && (minEndNs === null || ns < minEndNs)) {
        minEndNs = ns;
        minEndIso = end;
      }
    }
    const ts = a.timestamp?.trim();
    if (ts) {
      const ns = isoUtcToEpochNanoseconds(ts);
      if (ns !== null && (minTsNs === null || ns < minTsNs)) {
        minTsNs = ns;
        minTsIso = ts;
      }
    }
  }

  if (minEndIso !== null) return minEndIso;
  return minTsIso;
}

interface RawIncident {
  Id?: string;
  uniqueId?: string;
  timestamp?: string;
  end?: string;
  sensorId?: string;
  category?: string;
  analyticsModule?: { info?: { triggerModules?: string; verdict?: string }; description?: string };
  [key: string]: unknown;
}

function transformIncidentsPayload(data: { incidents?: RawIncident[] }): AlertData[] {
  return (data.incidents || []).map((incident, index) => ({
    id: incident.Id || incident.uniqueId || `alert-${incident.timestamp}-${incident.sensorId}-${index}`,
    timestamp: incident.timestamp || '',
    end: incident.end || '',
    sensor: incident.sensorId || '',
    alertType: incident.category || '',
    alertTriggered: incident.analyticsModule?.info?.triggerModules || '',
    alertDescription: incident.analyticsModule?.description || '',
    metadata: incident,
  }));
}

function mergeAlertsDedupe(existing: AlertData[], incoming: AlertData[]): AlertData[] {
  const seen = new Set(existing.map((a) => a.id));
  const out = [...existing];
  for (const a of incoming) {
    if (!seen.has(a.id)) {
      seen.add(a.id);
      out.push(a);
    }
  }
  return out;
}

/**
 * Configuration options for the useAlerts hook
 */
interface UseAlertsOptions {
  apiUrl?: string;
  vstApiUrl?: string;
  vlmVerified?: boolean;
  vlmVerdict?: VlmVerdict;
  timeWindow?: number;
  maxResults?: number;
  activeFilters?: FilterState;
}

/**
 * Escapes special characters in a filter value for use in query string
 * Escapes quotes, backslashes, and HTML special characters to prevent XSS
 */
const escapeFilterValue = (value: string): string => {
  return value.replaceAll(/[\\"]/g, String.raw`\$&`).replaceAll(/[<>&'"]/g, (match) => {
    const escapeMap: Record<string, string> = {
      '<': '&lt;',
      '>': '&gt;',
      '&': '&amp;',
      "'": '&#x27;',
      '"': '&quot;'
    };
    return escapeMap[match];
  });
};

/**
 * Builds a queryString for the API from active filters
 * 
 * @param activeFilters - The current active filter state
 * @returns Query string or empty string if no filters
 * 
 * Example output:
 * sensorId.keyword:"4_test_output_1_m" AND category.keyword:"Tailgating" AND analyticsModule.info.triggerModules.keyword:"Abnormal Movement"
 * 
 * For multiple values in same filter:
 * sensorId.keyword:"val1" OR sensorId.keyword:"val2"
 */
const buildQueryString = (activeFilters?: FilterState): string => {
  if (!activeFilters) return '';

  const queryParts: string[] = [];

  // Map filter types to API field names
  const fieldMapping: Record<keyof FilterState, string> = {
    sensors: 'sensorId.keyword',
    alertTypes: 'category.keyword',
    alertTriggered: 'analyticsModule.info.triggerModules.keyword'
  };

  // Build query for each filter type
  for (const [filterType, fieldName] of Object.entries(fieldMapping)) {
    const values = activeFilters[filterType as keyof FilterState];
    if (values && values.size > 0) {
      const valuesArray = Array.from(values);
      if (valuesArray.length === 1) {
        // Single value - escape special characters
        queryParts.push(`${fieldName}:"${escapeFilterValue(valuesArray[0])}"`);
      } else {
        // Multiple values - join with OR, escape each value
        const orParts = valuesArray.map(v => `${fieldName}:"${escapeFilterValue(v)}"`);
        queryParts.push(`(${orParts.join(' OR ')})`);
      }
    }
  }

  // Join different filter types with AND
  return queryParts.join(' AND ');
};

/**
 * Serializes FilterState to a stable string for comparison
 * This prevents unnecessary re-renders when the filter object reference changes
 * but the actual values remain the same
 */
const serializeFilters = (filters?: FilterState): string => {
  if (!filters) return '';
  return JSON.stringify({
    sensors: Array.from(filters.sensors).sort((a, b) => a.localeCompare(b)),
    alertTypes: Array.from(filters.alertTypes).sort((a, b) => a.localeCompare(b)),
    alertTriggered: Array.from(filters.alertTriggered).sort((a, b) => a.localeCompare(b))
  });
};

/**
 * Custom React hook for managing alerts data fetching and state management
 *
 */
export const useAlerts = ({ apiUrl, vstApiUrl, vlmVerified = true, vlmVerdict = VLM_VERDICT.ALL, timeWindow = 10, maxResults = 100, activeFilters }: UseAlertsOptions) => {
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastBatchSize, setLastBatchSize] = useState(0);
  const [sensorMap, setSensorMap] = useState<Map<string, string>>(new Map());
  const [sensorList, setSensorList] = useState<string[]>([]);

  const loadMoreInFlightRef = useRef(false);
  const alertsRef = useRef(alerts);
  alertsRef.current = alerts;

  // Memoize the serialized filters to prevent unnecessary API calls
  // when the filter object reference changes but values remain the same
  const serializedFilters = useMemo(() => serializeFilters(activeFilters), [activeFilters]);
  
  // Memoize the query string based on serialized filters
  const queryString = useMemo(() => buildQueryString(activeFilters), [serializedFilters]);

  const buildIncidentsUrl = useCallback(
    (fromTimestamp: string, toTimestamp: string) => {
      let url = `${apiUrl}/incidents?vlmVerified=${vlmVerified}&fromTimestamp=${encodeURIComponent(fromTimestamp)}&toTimestamp=${encodeURIComponent(toTimestamp)}&maxResultSize=${maxResults}`;
      if (vlmVerified && vlmVerdict && vlmVerdict !== VLM_VERDICT.ALL) {
        url += `&vlmVerdict=${vlmVerdict}`;
      }
      if (queryString) {
        url += `&queryString=${encodeURIComponent(queryString).replaceAll(/[()]/g, encodeURIComponent)}`;
      }
      return url;
    },
    [apiUrl, vlmVerified, vlmVerdict, maxResults, queryString],
  );

  const fetchSensorList = useCallback(async () => {
    if (!vstApiUrl) return;
    
    try {
      const response = await fetch(`${vstApiUrl}/v1/sensor/list`);
      if (!response.ok) {
        console.error(`Failed to fetch sensor list: ${response.status}`);
        return;
      }
      const sensors = await response.json();
      
      const map = new Map<string, string>();
      const sensorNameSet = new Set<string>();
      (sensors as Array<{ name?: string; sensorId?: string; state?: string }>).forEach((sensor) => {
        if (sensor.name && sensor.sensorId && sensor.state === 'online') {
          map.set(sensor.name, sensor.sensorId);
          sensorNameSet.add(sensor.name);
        }
      });
      
      setSensorMap(map);
      setSensorList([...sensorNameSet].sort((a, b) => a.localeCompare(b)));
    } catch (err) {
      console.error('Error fetching sensor list:', err);
    }
  }, [vstApiUrl]);

  /**
   * Fetches alerts data from the incidents API with time-based filtering
   */
  const fetchAlerts = useCallback(async (): Promise<boolean> => {
    if (!apiUrl) {
      setError('API URL is not configured');
      setLoading(false);
      return false;
    }

    try {
      setLoading(true);
      setError(null);

      const now = new Date();
      const toTimestamp = now.toISOString();
      const fromTime = new Date(now.getTime() - (timeWindow * 60 * 1000));
      const fromTimestamp = fromTime.toISOString();

      const mdxWebApiIncidents = buildIncidentsUrl(fromTimestamp, toTimestamp);

      const response = await fetch(mdxWebApiIncidents);

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();

      const transformedAlerts = transformIncidentsPayload(data);
      setLastBatchSize(transformedAlerts.length);
      setAlerts(transformedAlerts);

      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch alerts');
      return false;
    } finally {
      setLoading(false);
    }
  }, [apiUrl, timeWindow, buildIncidentsUrl, vlmVerified, vlmVerdict, maxResults]);

  /**
   * Load the next slice with the same shape as the main query: **fromTimestamp** = now − period,
   * **toTimestamp** = min(loaded `end`) − {@link LOAD_MORE_TO_TIMESTAMP_SUBTRACT_MS}. Uses the
   * smallest `end` over all merged alerts (not the last row of the current page).
   */
  const loadMoreAlerts = useCallback(async (): Promise<boolean> => {
    if (!apiUrl) {
      setError('API URL is not configured');
      return false;
    }
    const anchor = getMinEndIsoForPaging(alertsRef.current);
    if (!anchor) {
      return false;
    }
    const toTimestamp = subtractMillisecondsIso(anchor, LOAD_MORE_TO_TIMESTAMP_SUBTRACT_MS);
    if (!toTimestamp) {
      return false;
    }
    const now = new Date();
    const fromTime = new Date(now.getTime() - timeWindow * 60 * 1000);
    const fromTimestamp = fromTime.toISOString();
    if (Date.parse(fromTimestamp) >= Date.parse(toTimestamp)) {
      return false;
    }
    if (loadMoreInFlightRef.current) {
      return false;
    }

    loadMoreInFlightRef.current = true;
    setLoadingMore(true);
    setError(null);

    try {
      const url = buildIncidentsUrl(fromTimestamp, toTimestamp);
      const response = await fetch(url);

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      const batch = transformIncidentsPayload(data);
      setLastBatchSize(batch.length);
      setAlerts((prev) => mergeAlertsDedupe(prev, batch));
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load more alerts');
      return false;
    } finally {
      loadMoreInFlightRef.current = false;
      setLoadingMore(false);
    }
  }, [apiUrl, buildIncidentsUrl, timeWindow]);

  // Fetch sensor list only once on mount (sensor list rarely changes)
  useEffect(() => {
    fetchSensorList();
  }, [fetchSensorList]);

  // Fetch alerts when dependencies change
  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // Refetch function - only refetches alerts by default, optionally refetches sensor list too
  // Returns true if fetch succeeded, false otherwise (used by auto-refresh to avoid overlapping calls)
  const refetch = useCallback(async (options?: { includeSensorList?: boolean }): Promise<boolean> => {
    if (options?.includeSensorList) {
      await fetchSensorList();
    }
    return fetchAlerts();
  }, [fetchSensorList, fetchAlerts]);

  const canLoadMore = maxResults > 0 && lastBatchSize >= maxResults;

  return {
    alerts,
    loading,
    loadingMore,
    error,
    sensorMap,
    sensorList,
    refetch,
    loadMoreAlerts,
    canLoadMore,
  };
};
