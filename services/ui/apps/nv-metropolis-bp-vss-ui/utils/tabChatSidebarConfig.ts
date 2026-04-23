// SPDX-License-Identifier: MIT
import { env } from 'next-runtime-env';

/**
 * SessionStorage prefix for the single embedded chat instance (NemoAgentToolkitApp storage keys).
 */
export const CHAT_SIDEBAR_INSTANCE_STORAGE_PREFIX = 'side-bar';

/**
 * Env namespace for the shared sidebar chat: NEXT_PUBLIC_SIDEBAR_CHAT_* (see tabChatEnv).
 */
export const SIDEBAR_CHAT_ENV_TAB_KEY = 'SIDEBAR';

/** Map tab id to env key suffix, e.g. 'search' -> 'SEARCH_TAB', 'video-management' -> 'VIDEO_MANAGEMENT_TAB'. */
export function getTabEnvKey(tabId: string): string {
  return tabId.toUpperCase().replace(/-/g, '_') + '_TAB';
}

/** App-wide floating Chat sidebar (all tabs except main Chat). NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR === 'true'. */
export function getChatSidebarEnabled(): boolean {
  const key = 'NEXT_PUBLIC_ENABLE_CHAT_SIDEBAR';
  return (env(key) || process?.env?.[key as keyof NodeJS.ProcessEnv]) === 'true';
}

/** Default open state: NEXT_PUBLIC_CHAT_SIDEBAR_OPEN_DEFAULT === 'true' means open on first visit. */
export function getChatSidebarOpenDefault(): boolean {
  const key = 'NEXT_PUBLIC_CHAT_SIDEBAR_OPEN_DEFAULT';
  return (env(key) || process?.env?.[key as keyof NodeJS.ProcessEnv]) === 'true';
}

const CHAT_SIDEBAR_OPEN_SESSION_KEY = 'nvMetropolis_chatSidebarOpen';

export function getChatSidebarOpenSessionKey(): string {
  return CHAT_SIDEBAR_OPEN_SESSION_KEY;
}

/** Reads last user-selected sidebar open state. Returns null if unset. */
export function getChatSidebarOpenFromSession(): boolean | null {
  if (typeof window === 'undefined' || !window.sessionStorage) return null;
  const raw = window.sessionStorage.getItem(CHAT_SIDEBAR_OPEN_SESSION_KEY);
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  return null;
}

export function setChatSidebarOpenInSession(open: boolean): void {
  if (typeof window === 'undefined' || !window.sessionStorage) return;
  window.sessionStorage.setItem(CHAT_SIDEBAR_OPEN_SESSION_KEY, String(open));
}

/** Storage key prefix for legacy per-tab helpers (tests only). */
export function getTabStorageKeyPrefix(tabId: string): string {
  const camel = tabId
    .split('-')
    .map((s, i) => (i === 0 ? s : s.charAt(0).toUpperCase() + s.slice(1)))
    .join('');
  return camel + 'Tab';
}
