// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Map tab surface. Renders warehouse analytics or the traffic-light (ITS)
 * app natively, selected by NEXT_PUBLIC_MAP_APP (smartcities vs warehouse).
 */
import React from 'react';
import {
  WarehouseAnalyticsComponent,
  type WarehouseAnalyticsComponentProps,
  type WarehouseAnalyticsSidebarControlHandlers,
} from './WarehouseAnalyticsComponent';
import { TrafficLightComponent } from './traffic-light/TrafficLightComponent';
import { getMapApp, MAP_APP_TRAFFIC_LIGHT } from './mapApp';

export type MapSidebarControlHandlers = WarehouseAnalyticsSidebarControlHandlers;
export type MapComponentProps = WarehouseAnalyticsComponentProps;

export const MapComponent: React.FC<MapComponentProps> = (props) =>
  getMapApp() === MAP_APP_TRAFFIC_LIGHT ? (
    <TrafficLightComponent {...props} />
  ) : (
    <WarehouseAnalyticsComponent {...props} />
  );

export default MapComponent;
