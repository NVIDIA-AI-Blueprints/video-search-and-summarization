// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VST DASH start URLs are origin-relative (`/vst/api/v1/live/dash/start`).
 * Warehouse media config is usually `http://host:port/vst/api/v1`.
 *
 * Deployed UI and VST share that origin behind haproxy. A local Next server
 * does not: the browser is on :3005 and VST is on the stack origin. Start
 * is CORS-enabled, but `/vst/dash/` manifests and segments are not, so a
 * cross-origin GET fails with TypeError "Failed to fetch". When the page
 * origin differs, return it instead so DashStream talks same-origin `/vst`
 * and Next can rewrite those paths to the configured VST origin.
 */
export function vstHttpOrigin(
  mediaServiceBaseUrl: string,
  pageOrigin: string = typeof window !== 'undefined' ? window.location.origin : '',
): string {
  const href = String(mediaServiceBaseUrl || '').trim();
  if (!href) return '';
  try {
    const parsed =
      href.startsWith('http://') || href.startsWith('https://')
        ? new URL(href)
        : new URL(href, pageOrigin || 'http://localhost');
    if (pageOrigin && parsed.origin !== pageOrigin) {
      return pageOrigin;
    }
    return parsed.origin;
  } catch {
    return href.replace(/\/vst\/api\/v1\/?.*$/i, '').replace(/\/+$/, '');
  }
}
