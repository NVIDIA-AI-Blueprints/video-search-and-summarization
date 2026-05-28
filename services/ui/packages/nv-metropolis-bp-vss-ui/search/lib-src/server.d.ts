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
export declare function fetchSearchData(): Promise<{
    systemStatus: string;
    agentApiUrl: string | undefined;
    vstApiUrl: string | undefined;
}>;