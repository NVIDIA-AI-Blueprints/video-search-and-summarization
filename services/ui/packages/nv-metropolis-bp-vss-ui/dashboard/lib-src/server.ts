// SPDX-License-Identifier: MIT
// Server-side data fetching for Dashboard component
// In production, replace this with actual API calls to your backend

import { env } from 'next-runtime-env';

const KIBANA_BASE_URL = env('NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL') || process?.env?.NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL;

const FETCH_TIMEOUT_MS = 5000; // 5 seconds timeout

async function fetchKibanaDashboards() {
  if (!KIBANA_BASE_URL) {
    return [];
  }

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    const response = await fetch(
      `${KIBANA_BASE_URL}/api/saved_objects/_find?type=dashboard&fields=title&fields=description`,
      { signal: controller.signal }
    );

    clearTimeout(timeoutId);

    if (!response.ok) {
      console.error(`Failed to fetch dashboards: ${response.statusText}`);
      return [];
    }

    const data = await response.json();
    return data.saved_objects || [];
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      console.error('Fetch dashboards timed out after', FETCH_TIMEOUT_MS, 'ms');
    } else {
      console.error('Error fetching dashboards from Kibana:', error);
    }
    return [];
  }
}

export async function fetchDashboardData() {
  const dashboards = await fetchKibanaDashboards();
  
  return {
    systemStatus: 'operational',
    kibanaBaseUrl: KIBANA_BASE_URL || null,
    dashboards,
  };
}
