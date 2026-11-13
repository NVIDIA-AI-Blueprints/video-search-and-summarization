// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { env } from 'next-runtime-env';
import { MAP_APP_TRAFFIC_LIGHT, MAP_APP_WAREHOUSE } from './mapAppConstants';

const read = (key: string) => env(key) || process?.env?.[key];

const mapAppFromEnv = () =>
  (read('NEXT_PUBLIC_MAP_APP') || MAP_APP_WAREHOUSE).trim();

export async function fetchWarehouseAnalyticsData() {
  await new Promise((resolve) => setTimeout(resolve, 100));

  return {
    systemStatus: 'operational',
    mapApp: MAP_APP_WAREHOUSE,
    apiUrl:
      read('NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_URL') ||
      read('NEXT_PUBLIC_MDX_WEB_API_URL') ||
      null,
    mediaStreamingUrl: read('NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_URL') || null,
    googleMapsApiKey: read('NEXT_PUBLIC_GOOGLE_MAPS_API_KEY') || null,
    uiConfigUrl:
      read('NEXT_PUBLIC_WAREHOUSE_UI_CONFIG_URL') ||
      '/warehouse-analytics/default-ui-config.json',
  };
}

export async function fetchTrafficLightData() {
  await new Promise((resolve) => setTimeout(resolve, 100));

  return {
    systemStatus: 'operational',
    mapApp: MAP_APP_TRAFFIC_LIGHT,
    apiUrl:
      read('NEXT_PUBLIC_WAREHOUSE_ANALYTICS_API_URL') ||
      read('NEXT_PUBLIC_MDX_WEB_API_URL') ||
      null,
    mediaStreamingUrl: read('NEXT_PUBLIC_WAREHOUSE_MEDIA_STREAMING_URL') || null,
    googleMapsApiKey: read('NEXT_PUBLIC_GOOGLE_MAPS_API_KEY') || null,
    uiConfigUrl:
      read('NEXT_PUBLIC_TRAFFIC_LIGHT_UI_CONFIG_URL') ||
      '/traffic-light/default-ui-config.json',
  };
}

export async function fetchMapData() {
  return mapAppFromEnv() === MAP_APP_TRAFFIC_LIGHT
    ? fetchTrafficLightData()
    : fetchWarehouseAnalyticsData();
}
