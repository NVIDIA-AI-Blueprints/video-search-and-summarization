// SPDX-License-Identifier: MIT
/**
 * Conversation persistence.
 *
 * Conversations live in IndexedDB but are scoped to the life of a browser tab,
 * matching sessionStorage semantics: they survive a reload, and die with the
 * tab, the window, or a reboot. IndexedDB has no such lifetime, so every key is
 * tagged with a per-tab id held in sessionStorage (which does die with the
 * tab), and keys whose tab is no longer live are swept on startup.
 *
 * IndexedDB rather than sessionStorage because conversation history with
 * inlined media outgrows the ~5MB sessionStorage quota.
 */
import { openDB, type IDBPDatabase } from 'idb';

import type { Conversation } from '../types/chat';

const DB_NAME = 'vss-chat';
const DB_VERSION = 1;
const STORE_NAME = 'conversations';

const TAB_SESSION_STORAGE_KEY = 'vss-chat-tab-session';
const TAB_KEY_SEPARATOR = '__';
const BROADCAST_CHANNEL_NAME = 'vss-chat-tab-presence';

/** How long to wait for other tabs to announce themselves before sweeping. */
const ORPHAN_CLEANUP_DISCOVERY_MS = 300;

let dbPromise: Promise<IDBPDatabase> | null = null;
let lifecycleInitialized = false;

function getDb(): Promise<IDBPDatabase> {
  if (!dbPromise) {
    dbPromise = openDB(DB_NAME, DB_VERSION, {
      upgrade(db) {
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          db.createObjectStore(STORE_NAME);
        }
      },
    });
  }
  return dbPromise;
}

/**
 * This tab's id, minted on first use.
 *
 * Falls back to a constant during SSR and when sessionStorage is unavailable
 * (private browsing, blocked storage) — persistence degrades rather than
 * throwing on a page the user is trying to load.
 */
function getTabSessionId(): string {
  if (typeof window === 'undefined') return 'ssr';

  try {
    let id = window.sessionStorage.getItem(TAB_SESSION_STORAGE_KEY);
    if (!id) {
      id = `tab_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
      window.sessionStorage.setItem(TAB_SESSION_STORAGE_KEY, id);
    }
    return id;
  } catch {
    return 'ssr';
  }
}

/** `<tabId>__<prefix>_<base>` — the prefix isolates chat instances on one page. */
function storeKey(base: string, prefix?: string | null): string {
  const userPrefix = prefix ? `${prefix}_` : '';
  return `${getTabSessionId()}${TAB_KEY_SEPARATOR}${userPrefix}${base}`;
}

/*
 * These helpers let IndexedDB errors reach the caller. Fire-and-forget callers
 * (autosave on keystroke) attach their own catch; callers that need the write
 * to have happened (import) await and react.
 */

export async function saveConversationToDb(
  conversation: Conversation,
  storageKeyPrefix?: string | null,
): Promise<void> {
  const db = await getDb();
  await db.put(STORE_NAME, conversation, storeKey('selectedConversation', storageKeyPrefix));
}

export async function saveConversationsToDb(
  conversations: Conversation[],
  storageKeyPrefix?: string | null,
): Promise<void> {
  const db = await getDb();
  await db.put(STORE_NAME, conversations, storeKey('conversationHistory', storageKeyPrefix));
}

export async function loadConversationFromDb(
  storageKeyPrefix?: string | null,
): Promise<Conversation | null> {
  const db = await getDb();
  const data = await db.get(STORE_NAME, storeKey('selectedConversation', storageKeyPrefix));
  return (data as Conversation) ?? null;
}

export async function loadConversationsFromDb(
  storageKeyPrefix?: string | null,
): Promise<Conversation[]> {
  const db = await getDb();
  const data = await db.get(STORE_NAME, storeKey('conversationHistory', storageKeyPrefix));
  return (data as Conversation[]) ?? [];
}

export async function removeConversationFromDb(
  storageKeyPrefix?: string | null,
): Promise<void> {
  const db = await getDb();
  await db.delete(STORE_NAME, storeKey('selectedConversation', storageKeyPrefix));
}

export async function clearAllConversationsFromDb(
  storageKeyPrefix?: string | null,
): Promise<void> {
  const db = await getDb();
  await db.delete(STORE_NAME, storeKey('selectedConversation', storageKeyPrefix));
  await db.delete(STORE_NAME, storeKey('conversationHistory', storageKeyPrefix));
}

/** The tab id a key belongs to, or null if it carries none. */
function extractTabIdFromKey(key: unknown): string | null {
  if (typeof key !== 'string') return null;

  const separatorIndex = key.indexOf(TAB_KEY_SEPARATOR);
  return separatorIndex > 0 ? key.substring(0, separatorIndex) : null;
}

/**
 * Deletes every key whose tab id satisfies `shouldDelete`.
 *
 * Untagged keys are always deleted: they predate per-tab namespacing and can
 * never be reclaimed by a live tab.
 */
async function deleteKeysForTabIds(shouldDelete: (tabId: string) => boolean): Promise<void> {
  const db = await getDb();
  const keys = await db.getAllKeys(STORE_NAME);
  if (!keys || keys.length === 0) return;

  const tx = db.transaction(STORE_NAME, 'readwrite');
  const store = tx.objectStore(STORE_NAME);

  const deletions: Promise<void>[] = [];
  for (const key of keys) {
    const tabId = extractTabIdFromKey(key);
    if (tabId === null || shouldDelete(tabId)) {
      deletions.push(store.delete(key as IDBValidKey));
    }
  }

  await Promise.all(deletions);
  await tx.done;
}

/**
 * Asks other tabs to announce themselves and collects the replies.
 *
 * The current tab is always in the returned set, so a failure to discover
 * anyone else can never sweep the data belonging to this tab.
 */
async function discoverLiveTabIds(currentTabId: string): Promise<Set<string>> {
  const liveTabIds = new Set<string>([currentTabId]);
  if (typeof BroadcastChannel === 'undefined') return liveTabIds;

  const channel = new BroadcastChannel(BROADCAST_CHANNEL_NAME);

  channel.onmessage = (event) => {
    const data = event.data;
    if (!data || typeof data !== 'object') return;

    if (data.type === 'announce' && typeof data.tabId === 'string') {
      liveTabIds.add(data.tabId);
    } else if (data.type === 'request-presence') {
      try {
        channel.postMessage({ type: 'announce', tabId: currentTabId });
      } catch {
        // The channel closes while a tab unloads; a missed reply only means
        // that tab's data is swept, which is what closing it should do.
      }
    }
  };

  try {
    channel.postMessage({ type: 'request-presence' });
    channel.postMessage({ type: 'announce', tabId: currentTabId });
  } catch {
    // Best effort: without discovery only this tab counts as live.
  }

  await new Promise((resolve) => setTimeout(resolve, ORPHAN_CLEANUP_DISCOVERY_MS));
  return liveTabIds;
}

/**
 * Starts per-tab lifecycle management. Call once on load.
 *
 * Sweeps keys belonging to tabs that are no longer live — covering reboots and
 * closes where the unload handler never ran — and registers a `pagehide`
 * handler to drop this tab's keys on the way out.
 */
export function initConversationSessionLifecycle(): void {
  if (typeof window === 'undefined' || lifecycleInitialized) return;
  lifecycleInitialized = true;

  const currentTabId = getTabSessionId();

  // Runs in the background: a load must not wait on the discovery window.
  void (async () => {
    try {
      const liveTabIds = await discoverLiveTabIds(currentTabId);
      await deleteKeysForTabIds((tabId) => !liveTabIds.has(tabId));
    } catch (error) {
      console.warn('Failed to sweep orphaned conversation data from IndexedDB:', error);
    }
  })();

  window.addEventListener('pagehide', () => {
    // Not awaited — unload will not wait for IndexedDB. The startup sweep is
    // the safety net when this does not finish.
    deleteKeysForTabIds((tabId) => tabId === currentTabId).catch(() => {});
  });
}

/** Test-only: clears the module-level lifecycle flag and connection cache. */
export function __resetConversationDbForTests(): void {
  lifecycleInitialized = false;
  dbPromise = null;
}
