// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { render, screen } from '@testing-library/react';
import { MapComponent } from '../lib-src/MapComponent';
import { getMapApp, MAP_APP_TRAFFIC_LIGHT } from '../lib-src/mapApp';

jest.mock('../lib-src/mapApp', () => ({
  MAP_APP_TRAFFIC_LIGHT: 'traffic-light',
  getMapApp: jest.fn(() => 'warehouse'),
}));

jest.mock('../lib-src/warehouse/WarehouseAnalyticsComponent', () => ({
  WarehouseAnalyticsComponent: ({
    theme,
    mapData,
    warehouseData,
  }: {
    theme?: string;
    mapData?: { apiUrl?: string | null };
    warehouseData?: { apiUrl?: string | null };
  }) => (
    <div
      data-testid="warehouse-analytics"
      className={theme === 'dark' ? 'bg-black' : 'bg-white'}
    >
      Warehouse Analytics {(warehouseData || mapData)?.apiUrl}
    </div>
  ),
  WarehouseAnalyticsSidebarControls: () => null,
}));

jest.mock('../lib-src/traffic-light/TrafficLightComponent', () => ({
  TrafficLightComponent: ({
    theme,
    mapData,
    warehouseData,
  }: {
    theme?: string;
    mapData?: { apiUrl?: string | null };
    warehouseData?: { apiUrl?: string | null };
  }) => (
    <div
      data-testid="traffic-light"
      className={theme === 'dark' ? 'bg-black' : 'bg-white'}
    >
      Traffic Light {(warehouseData || mapData)?.apiUrl}
    </div>
  ),
}));

const mockedGetMapApp = getMapApp as jest.MockedFunction<typeof getMapApp>;

describe('MapComponent', () => {
  beforeEach(() => {
    mockedGetMapApp.mockReturnValue('warehouse');
  });

  it('renders warehouse analytics natively by default', () => {
    render(
      <MapComponent mapData={{ apiUrl: 'http://example.test/video-analytics-api' }} />,
    );
    expect(screen.getByTestId('warehouse-analytics')).toHaveTextContent(
      'Warehouse Analytics',
    );
    expect(screen.getByTestId('warehouse-analytics')).toHaveTextContent(
      'http://example.test/video-analytics-api',
    );
  });

  it('renders traffic-light when NEXT_PUBLIC_MAP_APP selects it', () => {
    mockedGetMapApp.mockReturnValue(MAP_APP_TRAFFIC_LIGHT);
    render(
      <MapComponent mapData={{ apiUrl: 'http://example.test/video-analytics-api' }} />,
    );
    expect(screen.getByTestId('traffic-light')).toHaveTextContent('Traffic Light');
    expect(screen.queryByTestId('warehouse-analytics')).not.toBeInTheDocument();
  });

  it('applies dark theme classes', () => {
    const { container } = render(<MapComponent theme="dark" />);
    expect(container.firstChild).toHaveClass('bg-black');
  });

  it('applies light theme classes by default', () => {
    const { container } = render(<MapComponent />);
    expect(container.firstChild).toHaveClass('bg-white');
  });
});
