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
 * 
 */

import { useState, useMemo, useCallback, useEffect, useRef, Dispatch, SetStateAction } from 'react';
import { AlertData, FilterState, FilterType } from '../types';

/**
 * Interface for accumulated unique values
 */
interface UniqueValuesState {
  sensors: Set<string>;
  alertTypes: Set<string>;
  alertTriggered: Set<string>;
}

/**
 * Default empty filter state
 */
export const createEmptyFilterState = (): FilterState => ({
  sensors: new Set(),
  alertTypes: new Set(),
  alertTriggered: new Set()
});

/**
 * Default empty unique values state
 */
const createEmptyUniqueValuesState = (): UniqueValuesState => ({
  sensors: new Set(),
  alertTypes: new Set(),
  alertTriggered: new Set()
});

interface UseFiltersOptions {
  alerts: AlertData[];
  /** Optional external filter state - if provided, hook won't manage its own state */
  externalFilters?: FilterState;
  /** Optional external setter for filter state */
  onFiltersChange?: Dispatch<SetStateAction<FilterState>>;
  /** Optional sensor list from API - if provided, uses this instead of accumulating from data */
  sensorList?: string[];
}

export const useFilters = (options: UseFiltersOptions) => {
  const { alerts, externalFilters, onFiltersChange, sensorList } = options;

  // Internal state - only used if external state is not provided
  const [internalFilters, setInternalFilters] = useState<FilterState>(createEmptyFilterState);

  // Use external state if provided, otherwise use internal state
  const activeFilters = externalFilters ?? internalFilters;
  const setActiveFilters = onFiltersChange ?? setInternalFilters;

  // Accumulated unique values - persists across filter changes
  // Using ref to avoid unnecessary re-renders when accumulating
  const accumulatedValuesRef = useRef<UniqueValuesState>(createEmptyUniqueValuesState());
  const [uniqueValuesVersion, setUniqueValuesVersion] = useState(0);

  // Accumulate unique values from alerts data
  // This ensures filter options don't disappear when filters are applied
  // Note: Skip sensor accumulation if sensorList from API is provided
  useEffect(() => {
    if (alerts.length === 0) return;

    let hasNewValues = false;
    const accumulated = accumulatedValuesRef.current;
    const hasSensorListFromApi = sensorList && sensorList.length > 0;

    alerts.forEach(alert => {
      // Only accumulate sensors if no sensorList from API
      if (!hasSensorListFromApi && alert.sensor && !accumulated.sensors.has(alert.sensor)) {
        accumulated.sensors.add(alert.sensor);
        hasNewValues = true;
      }
      if (alert.alertType && !accumulated.alertTypes.has(alert.alertType)) {
        accumulated.alertTypes.add(alert.alertType);
        hasNewValues = true;
      }
      if (alert.alertTriggered && !accumulated.alertTriggered.has(alert.alertTriggered)) {
        accumulated.alertTriggered.add(alert.alertTriggered);
        hasNewValues = true;
      }
    });

    // Only trigger re-render if we found new values
    if (hasNewValues) {
      setUniqueValuesVersion(v => v + 1);
    }
  }, [alerts, sensorList]);

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

  // Convert accumulated Sets to sorted arrays for the UI
  // uniqueValuesVersion ensures this updates when new values are accumulated
  // sensorList from API takes precedence over accumulated sensors
  const uniqueValues = useMemo(() => {
    const accumulated = accumulatedValuesRef.current;
    return {
      // Use sensorList from API if provided, otherwise use accumulated sensors
      sensors: sensorList && sensorList.length > 0 
        ? sensorList 
        : [...accumulated.sensors].sort(),
      alertTypes: [...accumulated.alertTypes].sort(),
      alertTriggered: [...accumulated.alertTriggered].sort()
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

