// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { getEnv } from 'common/map';
import { MAP_TAB_APP_TRAFFIC_LIGHT, MAP_TAB_APP_WAREHOUSE } from './mapTabAppConstants';

export { MAP_TAB_APP_TRAFFIC_LIGHT, MAP_TAB_APP_WAREHOUSE };

export function getMapTabApp(): string {
  return (getEnv('NEXT_PUBLIC_MAP_TAB_APP') || MAP_TAB_APP_WAREHOUSE).trim();
}

export function isTrafficLightMapTabApp(mapTabApp?: string | null): boolean {
  return (mapTabApp || getMapTabApp()) === MAP_TAB_APP_TRAFFIC_LIGHT;
}
