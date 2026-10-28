// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { env } from 'next-runtime-env';

const read = (key: string) => env(key) || process?.env?.[key];

export async function fetchWarehouseAnalyticsData() {
  await new Promise((resolve) => setTimeout(resolve, 100));

  return {
    systemStatus: 'operational',
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

export const fetchMapData = fetchWarehouseAnalyticsData;
