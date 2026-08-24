import { openDB, IDBPDatabase } from 'idb';

import { Conversation } from '@/types/chat';

const DB_NAME = 'aiqtoolkit-chat';
const DB_VERSION = 1;
const STORE_NAME = 'conversations';

/**
 * Tab session lifecycle constants.
 *
 * Conversations are persisted in IndexedDB but logically scoped to the lifetime
 * of a browser tab — matching the semantics of the previous sessionStorage
 * implementation (data wiped on tab close, window close, and browser reboot).
 *
 * To achieve this we tag every IndexedDB key with a per-tab session id stored
 * in sessionStorage (which itself dies with the tab). On startup we use a
 * BroadcastChannel to discover live tab ids in other open tabs and delete any
 * IndexedDB entries whose tab id is no longer live (orphans from closed tabs
 * or pre-reboot sessions).
 */
const TAB_SESSION_STORAGE_KEY = 'aiqtoolkit-chat-tab-session';
const TAB_KEY_SEPARATOR = '__';
const BROADCAST_CHANNEL_NAME = 'aiqtoolkit-chat-tab-presence';
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

function getTabSessionId(): string {
  if (typeof window === 'undefined') {
    return 'ssr';
  }
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

function storeKey(base: string, prefix?: string | null): string {
  const tabId = getTabSessionId();
  const userPrefix = prefix ? `${prefix}_` : '';
  return `${tabId}${TAB_KEY_SEPARATOR}${userPrefix}${base}`;
}

/**
 * Persistence helpers below propagate IndexedDB errors to the caller. Callers
 * that want fire-and-forget behavior (e.g. autosave on every keystroke) should
 * attach their own `.catch` to log/swallow; callers that need transactional
 * guarantees (e.g. `importData`) can `await` and react to failures.
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

function extractTabIdFromKey(key: unknown): string | null {
  if (typeof key !== 'string') return null;
  const sepIdx = key.indexOf(TAB_KEY_SEPARATOR);
  if (sepIdx <= 0) return null;
  return key.substring(0, sepIdx);
}

async function deleteKeysForTabIds(tabIdsToDelete: (id: string) => boolean): Promise<void> {
  const db = await getDb();
  const keys = await db.getAllKeys(STORE_NAME);
  if (!keys || keys.length === 0) return;
  const tx = db.transaction(STORE_NAME, 'readwrite');
  const store = tx.objectStore(STORE_NAME);
  const deletions: Promise<void>[] = [];
  for (const key of keys) {
    const tabId = extractTabIdFromKey(key);
    // Always delete legacy/untagged keys (no tab id) – they are stale data
    // from before the per-tab namespacing was introduced.
    if (tabId === null || tabIdsToDelete(tabId)) {
      deletions.push(store.delete(key as IDBValidKey));
    }
  }
  await Promise.all(deletions);
  await tx.done;
}

/**
 * Discover live tab session ids by broadcasting a presence request and
 * collecting responses for a short window.
 */
async function discoverLiveTabIds(currentTabId: string): Promise<Set<string>> {
  const liveTabIds = new Set<string>([currentTabId]);
  if (typeof BroadcastChannel === 'undefined') {
    return liveTabIds;
  }

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
        // Channel may be closed if the tab is unloading – ignore.
      }
    }
  };

  try {
    channel.postMessage({ type: 'request-presence' });
    channel.postMessage({ type: 'announce', tabId: currentTabId });
  } catch {
    // Best-effort; if the channel can't post, we'll just clean only this tab's id
  }

  await new Promise((resolve) => setTimeout(resolve, ORPHAN_CLEANUP_DISCOVERY_MS));
  return liveTabIds;
}

/**
 * Initialize per-tab lifecycle for IndexedDB conversation storage.
 *
 * Call once on app load. This makes IndexedDB conversation data follow the
 * same wipe semantics as sessionStorage:
 *   - persists across reloads of the same tab
 *   - cleared when the tab/window is closed
 *   - cleared on browser reboot
 *
 * Implementation:
 *   1. Sweep orphaned IndexedDB keys whose tab id is not currently live
 *      (identified via BroadcastChannel from other open tabs). This handles
 *      reboots and tab-close cases where pagehide cleanup didn't run.
 *   2. Register a pagehide handler that best-effort deletes this tab's keys
 *      when the tab unloads.
 */
export function initConversationSessionLifecycle(): void {
  if (typeof window === 'undefined' || lifecycleInitialized) return;
  lifecycleInitialized = true;

  const currentTabId = getTabSessionId();

  // Sweep orphans in the background. Current tab's keys are always preserved
  // because currentTabId is in the live set.
  (async () => {
    try {
      const liveTabIds = await discoverLiveTabIds(currentTabId);
      await deleteKeysForTabIds((tabId) => !liveTabIds.has(tabId));
    } catch (error) {
      console.warn('Failed to sweep orphaned conversation data from IndexedDB:', error);
    }
  })();

  const handlePagehide = () => {
    // Best-effort: fire the deletion but don't block the unload.
    deleteKeysForTabIds((tabId) => tabId === currentTabId).catch(() => {
      // Swallow – the orphan sweep on next load is the safety net.
    });
  };
  window.addEventListener('pagehide', handlePagehide);
}

/**
 * Test-only helper to reset the lifecycle initialized flag and dbPromise cache.
 * Not exported in production usage paths.
 */
export function __resetConversationDbForTests(): void {
  lifecycleInitialized = false;
  dbPromise = null;
}
