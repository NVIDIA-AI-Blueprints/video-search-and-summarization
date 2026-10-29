// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { render, screen } from '@testing-library/react';
import { MapComponent } from '../lib-src/MapComponent';

jest.mock('@nv-metropolis-bp-vss-ui/warehouse-analytics', () => ({
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

describe('MapComponent', () => {
  it('renders warehouse analytics natively', () => {
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

  it('applies dark theme classes', () => {
    const { container } = render(<MapComponent theme="dark" />);
    expect(container.firstChild).toHaveClass('bg-black');
  });

  it('applies light theme classes by default', () => {
    const { container } = render(<MapComponent />);
    expect(container.firstChild).toHaveClass('bg-white');
  });
});
