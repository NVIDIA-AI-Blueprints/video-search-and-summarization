// SPDX-License-Identifier: MIT
/**
 * Search tab Chat sidebar config. Thin wrapper over the reusable tabChatEnv utils
 * so env vars follow NEXT_PUBLIC_SEARCH_TAB_CHAT_* (tabKey = 'SEARCH_TAB').
 * For other tabs (e.g. Alerts), use tabChatEnv.ts with tabKey 'ALERTS_TAB' and
 * env vars NEXT_PUBLIC_ALERTS_TAB_CHAT_*.
 */
import {
  getTabChatInitialStateOverride,
  getTabChatWorkflow,
  type TabChatInitialStateOverride,
} from './tabChatEnv';

const SEARCH_TAB_KEY = 'SEARCH_TAB';

/** NEXT_PUBLIC_SEARCH_TAB_CHAT_WORKFLOW (fallback: NEXT_PUBLIC_WORKFLOW). */
export function getSearchTabChatWorkflow(): string {
  return getTabChatWorkflow(SEARCH_TAB_KEY, 'Search Chat');
}

/** @deprecated Use TabChatInitialStateOverride from tabChatEnv. Kept for backward compatibility. */
export type SearchTabChatInitialStateOverride = TabChatInitialStateOverride;

/** Builds initial state override from NEXT_PUBLIC_SEARCH_TAB_CHAT_* env vars. */
export function getSearchTabChatInitialStateOverride(): SearchTabChatInitialStateOverride {
  return getTabChatInitialStateOverride(SEARCH_TAB_KEY);
}
