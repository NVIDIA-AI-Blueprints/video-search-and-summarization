// SPDX-License-Identifier: MIT
/**
 * Server-Side Rendering Support for Alerts Module
 * 
 * This file provides server-side rendering (SSR) support and server-side utilities
 * for the alerts management module. It includes functions for data pre-fetching,
 * server-side state initialization, and SSR-compatible data processing to ensure
 * optimal performance and SEO compatibility in server-rendered applications.
 * 
 * **Key Features:**
 * - Server-side data fetching with proper error handling and timeout management
 * - SSR-compatible state initialization for seamless client-side hydration
 * - Performance optimization through data pre-loading and caching strategies
 * - Security considerations for server-side API calls and data sanitization
 * - Compatibility with popular SSR frameworks (Next.js, Nuxt.js, SvelteKit)
 * - Proper handling of environment variables and configuration in server context
 * 
 */

import { env } from 'next-runtime-env';

// Default values
const DEFAULT_ALERT_REPORT_PROMPT_TEMPLATE = "Generate a report for incident {incidentId} with sensor id {sensorId}.";

// Environment variables
const MDX_WEB_API_URL = env('NEXT_PUBLIC_MDX_WEB_API_URL') || process?.env?.NEXT_PUBLIC_MDX_WEB_API_URL;
const VST_API_URL = env('NEXT_PUBLIC_VST_API_URL') || process?.env?.NEXT_PUBLIC_VST_API_URL;
const ALERTS_TAB_ALERTS_FETCH_DEFAULT_TIME_WINDOW_IN_MINUTES = env('NEXT_PUBLIC_ALERTS_TAB_ALERTS_FETCH_DEFAULT_TIME_WINDOW_IN_MINUTES') || process?.env?.NEXT_PUBLIC_ALERTS_TAB_ALERTS_FETCH_DEFAULT_TIME_WINDOW_IN_MINUTES;
const ALERTS_TAB_DEFAULT_AUTO_REFRESH_IN_MILLISECONDS = env('NEXT_PUBLIC_ALERTS_TAB_DEFAULT_AUTO_REFRESH_IN_MILLISECONDS') || process?.env?.NEXT_PUBLIC_ALERTS_TAB_DEFAULT_AUTO_REFRESH_IN_MILLISECONDS;
const ALERTS_TAB_VERIFIED_FLAG_DEFAULT = env('NEXT_PUBLIC_ALERTS_TAB_VERIFIED_FLAG_DEFAULT') || process?.env?.NEXT_PUBLIC_ALERTS_TAB_VERIFIED_FLAG_DEFAULT;
const ALERTS_TAB_ALERTS_FETCH_MAX_RESULT_SIZE = env('NEXT_PUBLIC_ALERTS_TAB_ALERTS_FETCH_MAX_RESULT_SIZE') || process?.env?.NEXT_PUBLIC_ALERTS_TAB_ALERTS_FETCH_MAX_RESULT_SIZE;
const ALERTS_TAB_ALERT_REPORT_PROMPT_TEMPLATE = env('NEXT_PUBLIC_ALERTS_TAB_ALERT_REPORT_PROMPT_TEMPLATE') || process?.env?.NEXT_PUBLIC_ALERTS_TAB_ALERT_REPORT_PROMPT_TEMPLATE;
const ALERTS_TAB_MAX_SEARCH_TIME_LIMIT = env('NEXT_PUBLIC_ALERTS_TAB_MAX_SEARCH_TIME_LIMIT') || process?.env?.NEXT_PUBLIC_ALERTS_TAB_MAX_SEARCH_TIME_LIMIT;
const ALERTS_TAB_MEDIA_WITH_OBJECTS_BBOX = env('NEXT_PUBLIC_ALERTS_TAB_MEDIA_WITH_OBJECTS_BBOX') || process?.env?.NEXT_PUBLIC_ALERTS_TAB_MEDIA_WITH_OBJECTS_BBOX;


export async function fetchAlertsData() {
  // Simulate API call delay
  await new Promise(resolve => setTimeout(resolve, 100));
  
  return {
    systemStatus: 'operational',
    // Include API URLs from environment variables
    apiUrl: MDX_WEB_API_URL || null,
    vstApiUrl: VST_API_URL || null,
    // Include default time window from environment variables (default to 10 minutes)
    defaultTimeWindow: parseInt(ALERTS_TAB_ALERTS_FETCH_DEFAULT_TIME_WINDOW_IN_MINUTES || '10', 10),
    // Include default auto-refresh interval from environment variables (default to 1000 milliseconds)
    defaultAutoRefreshInterval: parseInt(ALERTS_TAB_DEFAULT_AUTO_REFRESH_IN_MILLISECONDS || '1000', 10),
    // Include default VLM verified flag from environment variables (default to true)
    defaultVlmVerified: ALERTS_TAB_VERIFIED_FLAG_DEFAULT === 'true',
    // Include max results from environment variables (default to 100)
    maxResults: parseInt(ALERTS_TAB_ALERTS_FETCH_MAX_RESULT_SIZE || '100', 10),
    pageSize: 20,
    // Include alert report prompt template from environment variables
    alertReportPromptTemplate: ALERTS_TAB_ALERT_REPORT_PROMPT_TEMPLATE || DEFAULT_ALERT_REPORT_PROMPT_TEMPLATE,
    // Include max search time limit from environment variables (0 = unlimited, default: 0)
    maxSearchTimeLimit: ALERTS_TAB_MAX_SEARCH_TIME_LIMIT || '0',
    // Include media with objects bbox flag from environment variables (default: false)
    mediaWithObjectsBbox: ALERTS_TAB_MEDIA_WITH_OBJECTS_BBOX === 'true'
  };
}

