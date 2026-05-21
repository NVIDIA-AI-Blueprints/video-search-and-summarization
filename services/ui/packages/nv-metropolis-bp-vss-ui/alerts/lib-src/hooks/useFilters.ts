// SPDX-License-Identifier: MIT
/**
 * useFilters Hook - Advanced Filter State Management for Alerts
 * 
 * This file contains the useFilters custom React hook which provides comprehensive filter
 * state management for the alerts management system. The hook handles multiple filter
 * categories simultaneously, maintains filter state consistency, and provides efficient
 * data filtering operations with performance optimizations for large datasets.
 * 
 * **Key Features:**
 * - Multi-category filter management (sensors, alert types, trigger conditions)
 * - Real-time data filtering with performance optimization using React.useMemo
 * - Dynamic unique value extraction from current dataset for filter options
 * - Efficient Set-based filter storage for O(1) lookup performance
 * - Automatic filter state synchronization with data changes
 * - Memory-efficient operations with minimal re-renders and computations
 * - Type-safe filter operations with comprehensive TypeScript support
 * - Support for external state management (for server-side filtering via API)
 * - Accumulated unique values that persist across filter changes
 * - VLM-verified-aware alert type and trigger lists
 * 
 */

import { useState, useMemo, useCallback, useEffect, useRef, Dispatch, SetStateAction } from 'react';
import { AlertData, FilterState, FilterType } from '../types';

const sortedArray = (set: Set<string>) => [...set].sort((a, b) => a.localeCompare(b));

/**
 * Interface for accumulated unique values (sensors only — alertTypes & alertTriggered
 * are accumulated for general backwards-compat but the dropdowns use the vlm-aware
 * values derived directly from the current `alerts` array).
 */
interface UniqueValuesState {
  sensors: Set<string>;
  alertTypes: Set<string>;
  alertTriggered: Set<string>;
  byVlmVerified: {
    enabled: {
      alertTypes: Set<string>;
      alertTriggered: Set<string>;
    };
    disabled: {
      alertTypes: Set<string>;
      alertTriggered: Set<string>;
    };
  };
}

/**
 * Default empty filter state
 */
export const createEmptyFilterState = (): FilterState => ({
  sensors: new Set(),
  alertTypes: new Set(),
  alertTriggered: new Set()
});

const createEmptyUniqueValuesState = (): UniqueValuesState => ({
  sensors: new Set(),
  alertTypes: new Set(),
  alertTriggered: new Set(),
  byVlmVerified: {
    enabled: {
      alertTypes: new Set(),
      alertTriggered: new Set(),
    },
    disabled: {
      alertTypes: new Set(),
      alertTriggered: new Set(),
    },
  },
});

interface UseFiltersOptions {
  alerts: AlertData[];
  vlmVerified?: boolean;
  /** Optional external filter state - if provided, hook won't manage its own state */
  externalFilters?: FilterState;
  /** Optional external setter for filter state */
  onFiltersChange?: Dispatch<SetStateAction<FilterState>>;
  /** Optional sensor list from API - if provided, uses this instead of accumulating from data */
  sensorList?: string[];
}

export const useFilters = (options: UseFiltersOptions) => {
  const { alerts, vlmVerified = true, externalFilters, onFiltersChange, sensorList } = options;

  // Internal state - only used if external state is not provided
  const [internalFilters, setInternalFilters] = useState<FilterState>(createEmptyFilterState);

  // Use external state if provided, otherwise use internal state
  const activeFilters = externalFilters ?? internalFilters;
  const setActiveFilters = onFiltersChange ?? setInternalFilters;

  // Accumulated unique values - persists across filter changes so dropdown
  // options don't disappear when server-side filters narrow the result set.
  // Only used for sensors and the general alertTypes/alertTriggered lists.
  const accumulatedValuesRef = useRef<UniqueValuesState>(createEmptyUniqueValuesState());
  const [uniqueValuesVersion, setUniqueValuesVersion] = useState(0);
  const prevAlertsRef = useRef<AlertData[] | null>(null);

  // Accumulate unique values without clearing existing options.
  // For alertType/alertTriggered we keep a dedicated cache per vlmVerified bucket
  // so each toggle state has an isolated, persistent option list.
  useEffect(() => {
    if (alerts.length === 0) return;
    const alertsChanged = alerts !== prevAlertsRef.current;
    if (!alertsChanged) return;
    prevAlertsRef.current = alerts;

    let hasNewValues = false;
    const accumulated = accumulatedValuesRef.current;
    const hasSensorListFromApi = sensorList && sensorList.length > 0;
    const vlmBucket = vlmVerified
      ? accumulated.byVlmVerified.enabled
      : accumulated.byVlmVerified.disabled;

    for (const alert of alerts) {
      if (!hasSensorListFromApi && alert.sensor && !accumulated.sensors.has(alert.sensor)) {
        accumulated.sensors.add(alert.sensor);
        hasNewValues = true;
      }
      if (alert.alertType && !accumulated.alertTypes.has(alert.alertType)) {
        accumulated.alertTypes.add(alert.alertType);
        hasNewValues = true;
      }
      if (alert.alertType && !vlmBucket.alertTypes.has(alert.alertType)) {
        vlmBucket.alertTypes.add(alert.alertType);
        hasNewValues = true;
      }
      if (alert.alertTriggered && !accumulated.alertTriggered.has(alert.alertTriggered)) {
        accumulated.alertTriggered.add(alert.alertTriggered);
        hasNewValues = true;
      }
      if (alert.alertTriggered && !vlmBucket.alertTriggered.has(alert.alertTriggered)) {
        vlmBucket.alertTriggered.add(alert.alertTriggered);
        hasNewValues = true;
      }
    }

    if (hasNewValues) {
      setUniqueValuesVersion(v => v + 1);
    }
  }, [alerts, sensorList, vlmVerified]);

  const addFilter = useCallback((type: FilterType, value: string) => {
    setActiveFilters(prev => ({
      ...prev,
      [type]: new Set([...prev[type], value])
    }));
  }, [setActiveFilters]);

  const removeFilter = useCallback((type: FilterType, value: string) => {
    setActiveFilters(prev => {
      const newSet = new Set(prev[type]);
      newSet.delete(value);
      return { ...prev, [type]: newSet };
    });
  }, [setActiveFilters]);

  const filteredAlerts = useMemo(() => {
    return alerts.filter(alert => {
      if (activeFilters.sensors.size > 0 && !activeFilters.sensors.has(alert.sensor)) {
        return false;
      }
      if (activeFilters.alertTypes.size > 0 && !activeFilters.alertTypes.has(alert.alertType)) {
        return false;
      }
      if (activeFilters.alertTriggered.size > 0 && !activeFilters.alertTriggered.has(alert.alertTriggered)) {
        return false;
      }
      return true;
    });
  }, [alerts, activeFilters]);

  // Convert accumulated Sets to sorted arrays for the UI.
  // sensorList from API takes precedence over accumulated sensors.
  const uniqueValues = useMemo(() => {
    const accumulated = accumulatedValuesRef.current;
    const { enabled, disabled } = accumulated.byVlmVerified;
    return {
      sensors: sensorList && sensorList.length > 0
        ? sensorList
        : sortedArray(accumulated.sensors),
      alertTypes: sortedArray(accumulated.alertTypes),
      alertTriggered: sortedArray(accumulated.alertTriggered),
      byVlmVerified: {
        enabled: {
          alertTypes: sortedArray(enabled.alertTypes),
          alertTriggered: sortedArray(enabled.alertTriggered),
        },
        disabled: {
          alertTypes: sortedArray(disabled.alertTypes),
          alertTriggered: sortedArray(disabled.alertTriggered),
        },
      },
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uniqueValuesVersion, sensorList]);

  return {
    activeFilters,
    addFilter,
    removeFilter,
    filteredAlerts,
    uniqueValues
  };
};
