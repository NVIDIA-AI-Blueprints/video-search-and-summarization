// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useFilters, createEmptyFilterState } from '../../lib-src/hooks/useFilters';
import { AlertData } from '../../lib-src/types';

const makeAlert = (overrides: Partial<AlertData> = {}): AlertData => ({
  id: 'alert-1',
  timestamp: '2024-01-15T09:00:00Z',
  end: '2024-01-15T09:05:00Z',
  sensor: 'Cam-1',
  alertType: 'Tailgating',
  alertTriggered: 'Motion',
  alertDescription: 'Test alert',
  metadata: {},
  ...overrides,
});

describe('createEmptyFilterState', () => {
  it('returns empty Sets for all filter types', () => {
    const state = createEmptyFilterState();
    expect(state.sensors).toBeInstanceOf(Set);
    expect(state.alertTypes).toBeInstanceOf(Set);
    expect(state.alertTriggered).toBeInstanceOf(Set);
    expect(state.sensors.size).toBe(0);
    expect(state.alertTypes.size).toBe(0);
    expect(state.alertTriggered.size).toBe(0);
  });
});

describe('useFilters', () => {
  const alerts: AlertData[] = [
    makeAlert({ id: '1', sensor: 'Cam-A', alertType: 'Tailgating', alertTriggered: 'Motion' }),
    makeAlert({ id: '2', sensor: 'Cam-B', alertType: 'Loitering', alertTriggered: 'Zone' }),
    makeAlert({ id: '3', sensor: 'Cam-A', alertType: 'Tailgating', alertTriggered: 'Thermal' }),
    makeAlert({ id: '4', sensor: 'Cam-C', alertType: 'Intrusion', alertTriggered: 'Motion' }),
  ];

  it('returns all alerts when no filters are active', () => {
    const { result } = renderHook(() => useFilters({ alerts }));
    expect(result.current.filteredAlerts).toHaveLength(4);
  });

  it('extracts unique values from alerts', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    expect(result.current.uniqueValues.sensors).toEqual(
      expect.arrayContaining(['Cam-A', 'Cam-B', 'Cam-C'])
    );
    expect(result.current.uniqueValues.alertTypes).toEqual(
      expect.arrayContaining(['Tailgating', 'Loitering', 'Intrusion'])
    );
    expect(result.current.uniqueValues.alertTriggered).toEqual(
      expect.arrayContaining(['Motion', 'Zone', 'Thermal'])
    );
  });

  it('uses sensorList from API when provided', () => {
    const { result } = renderHook(() =>
      useFilters({ alerts, sensorList: ['API-Cam-1', 'API-Cam-2'] })
    );

    expect(result.current.uniqueValues.sensors).toEqual(['API-Cam-1', 'API-Cam-2']);
  });

  it('filters alerts by sensor', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    act(() => {
      result.current.addFilter('sensors', 'Cam-A');
    });

    expect(result.current.filteredAlerts).toHaveLength(2);
    expect(result.current.filteredAlerts.every((a) => a.sensor === 'Cam-A')).toBe(true);
  });

  it('filters alerts by alertType', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    act(() => {
      result.current.addFilter('alertTypes', 'Tailgating');
    });

    expect(result.current.filteredAlerts).toHaveLength(2);
    expect(result.current.filteredAlerts.every((a) => a.alertType === 'Tailgating')).toBe(true);
  });

  it('filters alerts by alertTriggered', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    act(() => {
      result.current.addFilter('alertTriggered', 'Motion');
    });

    expect(result.current.filteredAlerts).toHaveLength(2);
  });

  it('combines multiple filter types with AND logic', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    act(() => {
      result.current.addFilter('sensors', 'Cam-A');
      result.current.addFilter('alertTriggered', 'Motion');
    });

    expect(result.current.filteredAlerts).toHaveLength(1);
    expect(result.current.filteredAlerts[0].id).toBe('1');
  });

  it('allows multiple values for the same filter type (OR logic)', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    act(() => {
      result.current.addFilter('sensors', 'Cam-A');
    });
    act(() => {
      result.current.addFilter('sensors', 'Cam-B');
    });

    expect(result.current.filteredAlerts).toHaveLength(3);
  });

  it('removes a filter value', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    act(() => {
      result.current.addFilter('sensors', 'Cam-A');
    });
    expect(result.current.filteredAlerts).toHaveLength(2);

    act(() => {
      result.current.removeFilter('sensors', 'Cam-A');
    });
    expect(result.current.filteredAlerts).toHaveLength(4);
  });

  it('initializes with empty filter state', () => {
    const { result } = renderHook(() => useFilters({ alerts }));

    expect(result.current.activeFilters.sensors.size).toBe(0);
    expect(result.current.activeFilters.alertTypes.size).toBe(0);
    expect(result.current.activeFilters.alertTriggered.size).toBe(0);
  });

  it('supports external filter state', () => {
    const externalFilters = createEmptyFilterState();
    externalFilters.sensors = new Set(['Cam-B']);
    const onFiltersChange = jest.fn();

    const { result } = renderHook(() =>
      useFilters({ alerts, externalFilters, onFiltersChange })
    );

    expect(result.current.filteredAlerts).toHaveLength(1);
    expect(result.current.filteredAlerts[0].sensor).toBe('Cam-B');

    act(() => {
      result.current.addFilter('alertTypes', 'Loitering');
    });
    expect(onFiltersChange).toHaveBeenCalled();
  });

  it('handles empty alerts array', () => {
    const { result } = renderHook(() => useFilters({ alerts: [] }));

    expect(result.current.filteredAlerts).toEqual([]);
    expect(result.current.uniqueValues.sensors).toEqual([]);
    expect(result.current.uniqueValues.alertTypes).toEqual([]);
  });

  it('accumulates unique values across data changes', () => {
    const initialAlerts = [makeAlert({ id: '1', sensor: 'Cam-A', alertType: 'Type1' })];

    const { result, rerender } = renderHook(
      ({ alerts: a }) => useFilters({ alerts: a }),
      { initialProps: { alerts: initialAlerts } }
    );

    expect(result.current.uniqueValues.alertTypes).toContain('Type1');

    const newAlerts = [makeAlert({ id: '2', sensor: 'Cam-B', alertType: 'Type2' })];
    rerender({ alerts: newAlerts });

    // Should contain both Type1 and Type2 (accumulated)
    expect(result.current.uniqueValues.alertTypes).toContain('Type1');
    expect(result.current.uniqueValues.alertTypes).toContain('Type2');
  });

  it('sorts unique values alphabetically', () => {
    const unsortedAlerts = [
      makeAlert({ id: '1', alertType: 'Zebra' }),
      makeAlert({ id: '2', alertType: 'Apple' }),
      makeAlert({ id: '3', alertType: 'Mango' }),
    ];

    const { result } = renderHook(() => useFilters({ alerts: unsortedAlerts }));
    expect(result.current.uniqueValues.alertTypes).toEqual(['Apple', 'Mango', 'Zebra']);
  });
});
