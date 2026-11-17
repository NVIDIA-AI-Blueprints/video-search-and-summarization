// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VST DASH start URLs are origin-relative (`/vst/api/v1/live/dash/start`).
 * Warehouse media config is usually a host:port path ending in `/vst/api/v1`.
 *
 * Deployed UI and VST share that origin behind haproxy. A local Next server
 * does not: the browser is on :3005 and VST is on the stack origin. Start
 * is CORS-enabled, but `/vst/dash/` manifests and segments are not, so a
 * cross-origin GET fails with TypeError "Failed to fetch". When the page
 * origin differs, return it instead so DashStream talks same-origin `/vst`
 * and Next can rewrite those paths to the configured VST origin.
 */
function stripPlaybackOrigin(href: string): string {
  const marker = '/vst/api/v1';
  const index = href.toLowerCase().indexOf(marker);
  const cut = index >= 0 ? href.slice(0, index) : href;
  let end = cut.length;
  while (end > 0 && cut.charCodeAt(end - 1) === 47) {
    end -= 1;
  }
  return end === cut.length ? cut : cut.slice(0, end);
}

export function vstHttpOrigin(
  mediaServiceBaseUrl: string,
  pageOrigin: string = typeof window !== 'undefined' ? window.location.origin : '',
): string {
  const href = String(mediaServiceBaseUrl || '').trim();
  if (!href) return '';
  try {
    const parsed = (() => {
      try {
        return new URL(href);
      } catch (error) {
        if (!pageOrigin) throw error;
        return new URL(href, pageOrigin);
      }
    })();
    if (pageOrigin && parsed.origin !== pageOrigin) {
      return pageOrigin;
    }
    return parsed.origin;
  } catch {
    return stripPlaybackOrigin(href);
  }
}
