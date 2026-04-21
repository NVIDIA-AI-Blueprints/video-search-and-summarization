// SPDX-License-Identifier: MIT
import { env } from 'next-runtime-env';

/**
 * Tab ids that support the floating Chat sidebar.
 */
export const TAB_IDS_WITH_CHAT_SIDEBAR = ['search', 'alerts'] as const;

export type TabIdWithChatSidebar = (typeof TAB_IDS_WITH_CHAT_SIDEBAR)[number];

/** Map tab id to env key suffix, e.g. 'search' -> 'SEARCH_TAB', 'video-management' -> 'VIDEO_MANAGEMENT_TAB'. */
export function getTabEnvKey(tabId: string): string {
  return tabId.toUpperCase().replace(/-/g, '_') + '_TAB';
}

/** Whether the Chat sidebar is enabled for this tab (env NEXT_PUBLIC_${TAB}_CHAT_SIDEBAR_ENABLE === 'true'; default false). */
export function getTabChatSidebarEnabled(tabId: string): boolean {
  const key = `NEXT_PUBLIC_${getTabEnvKey(tabId)}_CHAT_SIDEBAR_ENABLE`;
  return (env(key) || process?.env?.[key as keyof NodeJS.ProcessEnv]) === 'true';
}

/** Default open state for this tab's sidebar (env NEXT_PUBLIC_${TAB}_CHAT_SIDEBAR_OPEN_DEFAULT === 'true' means open). Used for fresh launch (new tab / new session). */
export function getTabChatSidebarOpenDefault(tabId: string): boolean {
  const key = `NEXT_PUBLIC_${getTabEnvKey(tabId)}_CHAT_SIDEBAR_OPEN_DEFAULT`;
  return (env(key) || process?.env?.[key as keyof NodeJS.ProcessEnv]) === 'true';
}

const CHAT_SIDEBAR_OPEN_SESSION_KEY_PREFIX = 'nvMetropolis_chatSidebarOpen_';

/** Session storage key for this tab's last user-selected sidebar open state (used on same-tab refresh). */
export function getTabChatSidebarOpenSessionKey(tabId: string): string {
  return CHAT_SIDEBAR_OPEN_SESSION_KEY_PREFIX + tabId;
}

/** Reads the last user-selected sidebar open state from session storage. Returns null if not set (e.g. fresh tab). */
export function getTabChatSidebarOpenFromSession(tabId: string): boolean | null {
  if (typeof window === 'undefined' || !window.sessionStorage) return null;
  const raw = window.sessionStorage.getItem(getTabChatSidebarOpenSessionKey(tabId));
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  return null;
}

/** Persists the sidebar open state to session storage (so same-tab refresh restores it). */
export function setTabChatSidebarOpenInSession(tabId: string, open: boolean): void {
  if (typeof window === 'undefined' || !window.sessionStorage) return;
  window.sessionStorage.setItem(getTabChatSidebarOpenSessionKey(tabId), String(open));
}

/** Storage key prefix for this tab's chat instance (e.g. 'searchTab', 'alertsTab', 'videoManagementTab'). */
export function getTabStorageKeyPrefix(tabId: string): string {
  const camel = tabId
    .split('-')
    .map((s, i) => (i === 0 ? s : s.charAt(0).toUpperCase() + s.slice(1)))
    .join('');
  return camel + 'Tab';
}
