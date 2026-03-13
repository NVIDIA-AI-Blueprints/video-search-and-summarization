// SPDX-License-Identifier: MIT
/**
 * Custom React hook for managing alerts data fetching and state
 * 
 * This hook provides comprehensive alerts data management including API calls,
 * sensor mapping, error handling, and real-time data synchronization with
 * configurable time windows and verification filters.
 *
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { AlertData, VlmVerdict, VLM_VERDICT, FilterState } from '../types';

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
  return value.replace(/[\\"]/g, '\\$&').replace(/[<>&'"]/g, (match) => {
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
    sensors: Array.from(filters.sensors).sort(),
    alertTypes: Array.from(filters.alertTypes).sort(),
    alertTriggered: Array.from(filters.alertTriggered).sort()
  });
};

/**
 * Custom React hook for managing alerts data fetching and state management
 *
 */
export const useAlerts = ({ apiUrl, vstApiUrl, vlmVerified = true, vlmVerdict = VLM_VERDICT.ALL, timeWindow = 10, maxResults = 100, activeFilters }: UseAlertsOptions) => {
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sensorMap, setSensorMap] = useState<Map<string, string>>(new Map());
  const [sensorList, setSensorList] = useState<string[]>([]);

  // Memoize the serialized filters to prevent unnecessary API calls
  // when the filter object reference changes but values remain the same
  const serializedFilters = useMemo(() => serializeFilters(activeFilters), [activeFilters]);
  
  // Memoize the query string based on serialized filters
  const queryString = useMemo(() => buildQueryString(activeFilters), [serializedFilters]);

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
      const sensorNameSet = new Set<string>(); // Use Set to avoid duplicates
      sensors.forEach((sensor: any) => {
        if (sensor.name && sensor.sensorId && sensor.state === 'online') {
          map.set(sensor.name, sensor.sensorId);
          sensorNameSet.add(sensor.name); // Set automatically handles duplicates
        }
      });
      
      setSensorMap(map);
      setSensorList([...sensorNameSet].sort()); // Convert Set to sorted array
    } catch (err) {
      console.error('Error fetching sensor list:', err);
    }
  }, [vstApiUrl]);

  /**
   * Fetches alerts data from the incidents API with time-based filtering
   * 
   */
  const fetchAlerts = useCallback(async () => {
    if (!apiUrl) {
      setError('API URL is not configured. Please set NEXT_PUBLIC_ALERTS_API_URL in your environment.');
      setLoading(false);
      return;
    }

    try {
      setLoading(true);
      setError(null);
      
      // Calculate timestamps
      const now = new Date();
      const toTimestamp = now.toISOString();
      const fromTime = new Date(now.getTime() - (timeWindow * 60 * 1000)); // timeWindow in minutes
      const fromTimestamp = fromTime.toISOString();
      
      // Build API URL with verdict filter if vlmVerified is true and verdict is selected
      let mdxWebApiIncidents = `${apiUrl}/incidents?vlmVerified=${vlmVerified}&fromTimestamp=${fromTimestamp}&toTimestamp=${toTimestamp}&maxResultSize=${maxResults}`;
      if (vlmVerified && vlmVerdict && vlmVerdict !== VLM_VERDICT.ALL) {
        mdxWebApiIncidents += `&vlmVerdict=${vlmVerdict}`;
      }
      
      // Add queryString for sensor/alertType/alertTriggered filters (already memoized)
      if (queryString) {
        mdxWebApiIncidents += `&queryString=${encodeURIComponent(queryString).replace(/[()]/g, encodeURIComponent)}`;
      }
      
      const response = await fetch(mdxWebApiIncidents);
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      
      // Transform API response to AlertData format
      const transformedAlerts: AlertData[] = (data.incidents || []).map((incident: any, index: number) => ({
        id: incident.Id || incident.uniqueId || `alert-${incident.timestamp}-${incident.sensorId}-${index}`,
        timestamp: incident.timestamp || '',
        end: incident.end || '',
        sensor: incident.sensorId || '',
        alertType: incident.category || '',
        alertTriggered: incident.analyticsModule?.info?.triggerModules || '',
        alertDescription: incident.analyticsModule?.description || '',
        metadata: incident
      }));
      
      setAlerts(transformedAlerts);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch alerts');
      console.error('Error fetching alerts:', err);
    } finally {
      setLoading(false);
    }
  }, [apiUrl, vlmVerified, vlmVerdict, timeWindow, maxResults, queryString]);

  // Fetch sensor list only once on mount (sensor list rarely changes)
  useEffect(() => {
    fetchSensorList();
  }, [fetchSensorList]);

  // Fetch alerts when dependencies change
  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // Refetch function - only refetches alerts by default, optionally refetches sensor list too
  const refetch = useCallback(async (options?: { includeSensorList?: boolean }) => {
    if (options?.includeSensorList) {
      await fetchSensorList();
    }
    await fetchAlerts();
  }, [fetchSensorList, fetchAlerts]);

  return {
    alerts,
    loading,
    error,
    sensorMap,
    sensorList,
    refetch
  };
};
