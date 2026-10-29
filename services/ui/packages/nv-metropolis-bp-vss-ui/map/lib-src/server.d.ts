// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
export declare function fetchWarehouseAnalyticsData(): Promise<{
  systemStatus: string;
  apiUrl: string | null;
  mediaStreamingUrl: string | null;
  googleMapsApiKey: string | null;
  uiConfigUrl: string;
}>;
export declare function fetchMapData(): ReturnType<typeof fetchWarehouseAnalyticsData>;
