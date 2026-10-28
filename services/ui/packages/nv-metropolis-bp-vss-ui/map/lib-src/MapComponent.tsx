// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Map tab surface. Renders warehouse analytics natively (the former iframe
 * host for the SD / warehouse UI).
 */
import React from 'react';
import {
  WarehouseAnalyticsComponent,
  type WarehouseAnalyticsComponentProps,
  type WarehouseAnalyticsSidebarControlHandlers,
} from '@nv-metropolis-bp-vss-ui/warehouse-analytics';

export type MapSidebarControlHandlers = WarehouseAnalyticsSidebarControlHandlers;
export type MapComponentProps = WarehouseAnalyticsComponentProps;

export const MapComponent: React.FC<MapComponentProps> = (props) => (
  <WarehouseAnalyticsComponent {...props} />
);

export default MapComponent;
