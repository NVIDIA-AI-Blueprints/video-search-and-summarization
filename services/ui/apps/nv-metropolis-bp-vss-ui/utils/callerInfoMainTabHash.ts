// SPDX-License-Identifier: MIT
/**
 * Caller-info HTML is rendered inside embedded chat via `dangerouslySetInnerHTML` (no React handlers).
 * Tab links use same-document hash URLs; this app listens for `hashchange` and focuses the main tab.
 *
 * Convention: `#vss-mt-<mainTabId>` e.g. `#vss-mt-search`, `#vss-mt-video-management`
 */
export const VSS_CALLER_INFO_MAIN_TAB_HASH_PREFIX = 'vss-mt-';

export function hrefForCallerInfoMainTab(tabId: string): string {
  return `#${VSS_CALLER_INFO_MAIN_TAB_HASH_PREFIX}${tabId}`;
}

/** Returns raw main tab id from `location.hash`, or null if not our link. */
export function parseMainTabIdFromCallerInfoHash(hash: string): string | null {
  if (!hash.startsWith('#')) return null;
  const body = hash.slice(1);
  if (!body.startsWith(VSS_CALLER_INFO_MAIN_TAB_HASH_PREFIX)) return null;
  const id = body.slice(VSS_CALLER_INFO_MAIN_TAB_HASH_PREFIX.length);
  return id.length > 0 ? id : null;
}
