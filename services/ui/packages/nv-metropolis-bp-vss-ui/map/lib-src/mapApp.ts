// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { getEnv } from 'common/warehouse';
import { MAP_APP_TRAFFIC_LIGHT, MAP_APP_WAREHOUSE } from './mapAppConstants';

export { MAP_APP_TRAFFIC_LIGHT, MAP_APP_WAREHOUSE };

export function getMapApp(): string {
  return (getEnv('NEXT_PUBLIC_MAP_APP') || MAP_APP_WAREHOUSE).trim();
}

export function isTrafficLightMapApp(mapApp?: string | null): boolean {
  return (mapApp || getMapApp()) === MAP_APP_TRAFFIC_LIGHT;
}
