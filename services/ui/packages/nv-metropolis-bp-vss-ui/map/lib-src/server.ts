// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// Server-side data fetching for Map component
// In production, replace this with actual API calls to your backend

import { env } from 'next-runtime-env';

const MAP_URL = env('NEXT_PUBLIC_MAP_URL') || process?.env?.NEXT_PUBLIC_MAP_URL;

export async function fetchMapData() {
  await new Promise(resolve => setTimeout(resolve, 100));
  
  return {
    systemStatus: 'operational',
    mapUrl: MAP_URL || null,
  };
}

