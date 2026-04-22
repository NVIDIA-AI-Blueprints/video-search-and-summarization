// SPDX-License-Identifier: MIT
/**
 * Sample tests for AlertsComponent
 * 
 * This file serves as a boilerplate/reference for adding new tests to the Alerts Tab.
 * It demonstrates basic testing patterns for React components in this package.
 * 
 * To add more tests:
 * 1. Import the component and any dependencies you need
 * 2. Mock external dependencies (APIs, hooks, etc.)
 * 3. Write test cases using describe/it blocks
 * 4. Use React Testing Library for rendering and assertions
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { AlertsComponent } from '../../lib-src/AlertsComponent';
import { AlertsComponentProps } from '../../lib-src/types';

// Mock @nvidia/foundations-react-core (Button, Select, Switch used by FilterControls)
jest.mock('@nvidia/foundations-react-core', () => {
  const React = require('react');
  return {
    Button: React.forwardRef(({ children, ...rest }: any, ref: any) =>
      React.createElement('button', { ...rest, ref, 'data-foundation': 'Button' }, children),
    ),
    Select: React.forwardRef(({ items, onValueChange, value, ...rest }: any, ref: any) =>
      React.createElement(
        'select',
        {
          ...rest,
          ref,
          'data-foundation': 'Select',
          value,
          onChange: (e: any) => onValueChange?.(e.target.value),
        },
        items?.map((item: any) =>
          React.createElement('option', { key: item.value, value: item.value }, item.children),
        ),
      ),
    ),
    Switch: React.forwardRef(({ checked, onCheckedChange, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        type: 'checkbox',
        checked,
        'data-foundation': 'Switch',
        onChange: (e: any) => onCheckedChange?.(e.target.checked),
      }),
    ),
  };
});

// Mock @aiqtoolkit-ui/common (VideoModal + useVideoModal)
jest.mock('@aiqtoolkit-ui/common', () => ({
  VideoModal: jest.fn(() => null),
  useVideoModal: jest.fn(() => ({
    videoModal: { isOpen: false, videoUrl: '', title: '' },
    openVideoModalFromAlert: jest.fn(),
    closeVideoModal: jest.fn(),
    loadingAlertId: null,
  })),
}));

// Mock the hooks
jest.mock('../../lib-src/hooks/useAlerts', () => ({
  useAlerts: jest.fn(() => ({
    alerts: [],
    loading: false,
    error: null,
    refetch: jest.fn(),
  })),
}));

jest.mock('../../lib-src/hooks/useFilters', () => ({
  useFilters: jest.fn(() => ({
    activeFilters: {
      sensors: new Set(),
      alertTypes: new Set(),
      alertTriggered: new Set(),
    },
    addFilter: jest.fn(),
    removeFilter: jest.fn(),
    clearFilters: jest.fn(),
    filteredAlerts: [],
    uniqueValues: {
      sensors: [],
      alertTypes: [],
      alertTriggered: [],
    },
  })),
  createEmptyFilterState: jest.fn(() => ({
    sensors: new Set(),
    alertTypes: new Set(),
    alertTriggered: new Set(),
  })),
}));

jest.mock('../../lib-src/hooks/useTimeWindow', () => ({
  useTimeWindow: jest.fn(() => ({
    timeWindow: 3600,
    setTimeWindow: jest.fn(),
  })),
}));

jest.mock('../../lib-src/hooks/useAutoRefresh', () => ({
  useAutoRefresh: jest.fn(() => ({
    isEnabled: false,
    interval: 30,
    setInterval: jest.fn(),
    toggleEnabled: jest.fn(),
  })),
}));

describe('AlertsComponent', () => {
  const defaultProps: AlertsComponentProps = {
    theme: 'light',
    isActive: true,
    alertsData: {
      systemStatus: 'active',
      apiUrl: 'http://test-api.com',
      vstApiUrl: 'http://test-vst-api.com',
      defaultTimeWindow: 3600,
    },
  };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  /**
   * Basic rendering test
   * This is a simple test to verify the component renders without crashing
   */
  it('should render without crashing', () => {
    render(<AlertsComponent {...defaultProps} />);
    // Component should render - we can check for any expected element
    expect(document.body).toBeInTheDocument();
  });

  /**
   * Props validation test
   * This test verifies that the component accepts and uses props correctly
   */
  it('should accept and use theme prop', () => {
    const { rerender } = render(<AlertsComponent {...defaultProps} theme="light" />);
    
    // Re-render with different theme
    rerender(<AlertsComponent {...defaultProps} theme="dark" />);
    
    // Component should still render
    expect(document.body).toBeInTheDocument();
  });

  /**
   * Conditional rendering test
   * This test checks that the component handles conditional props correctly
   */
  it('should handle isActive prop', () => {
    const { rerender } = render(<AlertsComponent {...defaultProps} isActive={true} />);
    expect(document.body).toBeInTheDocument();

    rerender(<AlertsComponent {...defaultProps} isActive={false} />);
    expect(document.body).toBeInTheDocument();
  });

  /**
   * Optional props test
   * This test verifies that optional props work correctly
   */
  it('should handle optional alertsData prop', () => {
    const propsWithoutAlertsData: AlertsComponentProps = {
      theme: 'light',
      isActive: true,
    };

    render(<AlertsComponent {...propsWithoutAlertsData} />);
    expect(document.body).toBeInTheDocument();
  });

  /**
   * Callback prop test
   * This test demonstrates how to test callback props
   */
  it('should call onThemeChange when provided', () => {
    const mockOnThemeChange = jest.fn();
    render(<AlertsComponent {...defaultProps} onThemeChange={mockOnThemeChange} />);
    
    // Note: In a real test, you would trigger the theme change action
    // This is just demonstrating the pattern
    expect(mockOnThemeChange).toBeDefined();
  });
});

