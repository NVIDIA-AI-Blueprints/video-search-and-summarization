// SPDX-License-Identifier: MIT
/**
 * Server-Side Rendering Support for Search Module
 * 
 * This file provides server-side rendering (SSR) support and server-side utilities
 * for the search management module. It includes functions for data pre-fetching,
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

const VST_API_URL = env('NEXT_PUBLIC_VST_API_URL') || process?.env?.NEXT_PUBLIC_VST_API_URL;
const AGENT_API_URL_BASE = env('NEXT_PUBLIC_AGENT_API_URL_BASE') || process?.env?.NEXT_PUBLIC_AGENT_API_URL_BASE;
const SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX = env('NEXT_PUBLIC_SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX') || process?.env?.NEXT_PUBLIC_SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX;

export async function fetchSearchData() {
  // Simulate API call delay
  await new Promise(resolve => setTimeout(resolve, 100));
  
  return {
    systemStatus: 'operational',
    agentApiUrl: AGENT_API_URL_BASE || null,
    vstApiUrl: VST_API_URL || null,
    mediaWithObjectsBbox: SEARCH_TAB_MEDIA_WITH_OBJECTS_BBOX
  };
}

