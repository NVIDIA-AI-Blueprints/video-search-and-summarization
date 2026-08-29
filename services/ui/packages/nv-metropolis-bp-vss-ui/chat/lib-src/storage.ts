// SPDX-License-Identifier: MIT
/**
 * Conversation persistence.
 *
 * Ported from the toolkit's `utils/app/conversationDb.ts`, including the part
 * that is easy to miss: conversations live in IndexedDB but are *scoped to the
 * lifetime of a browser tab*. Every key is tagged with a per-tab id kept in
 * sessionStorage (which dies with the tab); on startup a BroadcastChannel
 * discovers which tab ids are still live and sweeps the rest. That reproduces
 * sessionStorage's wipe semantics — survives reload, cleared on tab close and
 * on browser restart — while allowing payloads far larger than sessionStorage
 * would hold.
 *
 * Dropping this and using plain sessionStorage would look identical until a
 * conversation with a few base64 frames in it blew the 5 MB quota.
 */
import { openDB, type IDBPDatabase } from 'idb';

import type { Conversation } from './types';

const DB_NAME = 'vss-chat';
const DB_VERSION = 1;
const STORE_NAME = 'conversations';

const TAB_SESSION_STORAGE_KEY = 'vss-chat-tab-session';
const TAB_KEY_SEPARATOR = '__';
const BROADCAST_CHANNEL_NAME = 'vss-chat-tab-presence';
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

/** `<tabId>__<prefix>_<base>` — the prefix is what separates two panels. */
export function storeKey(base: string, prefix?: string | null): string {
  const userPrefix = prefix ? `${prefix}_` : '';
  return `${getTabSessionId()}${TAB_KEY_SEPARATOR}${userPrefix}${base}`;
}

export async function saveConversations(
  conversations: Conversation[],
  prefix?: string | null,
): Promise<void> {
  const db = await getDb();
  await db.put(STORE_NAME, conversations, storeKey('conversationHistory', prefix));
}

export async function loadConversations(prefix?: string | null): Promise<Conversation[]> {
  const db = await getDb();
  const data = await db.get(STORE_NAME, storeKey('conversationHistory', prefix));
  return (data as Conversation[]) ?? [];
}

export async function saveSelectedConversationId(
  id: string | null,
  prefix?: string | null,
): Promise<void> {
  const db = await getDb();
  await db.put(STORE_NAME, id, storeKey('selectedConversationId', prefix));
}

export async function loadSelectedConversationId(
  prefix?: string | null,
): Promise<string | null> {
  const db = await getDb();
  return ((await db.get(STORE_NAME, storeKey('selectedConversationId', prefix))) as string) ?? null;
}

export async function clearAllConversations(prefix?: string | null): Promise<void> {
  const db = await getDb();
  await db.delete(STORE_NAME, storeKey('conversationHistory', prefix));
  await db.delete(STORE_NAME, storeKey('selectedConversationId', prefix));
}

function extractTabIdFromKey(key: unknown): string | null {
  if (typeof key !== 'string') return null;
  const sepIdx = key.indexOf(TAB_KEY_SEPARATOR);
  return sepIdx <= 0 ? null : key.substring(0, sepIdx);
}

async function deleteKeysForTabIds(shouldDelete: (id: string) => boolean): Promise<void> {
  const db = await getDb();
  const keys = await db.getAllKeys(STORE_NAME);
  if (!keys?.length) return;
  const tx = db.transaction(STORE_NAME, 'readwrite');
  const store = tx.objectStore(STORE_NAME);
  const deletions: Promise<void>[] = [];
  for (const key of keys) {
    const tabId = extractTabIdFromKey(key);
    // Untagged keys predate the per-tab namespacing and are stale by definition.
    if (tabId === null || shouldDelete(tabId)) {
      deletions.push(store.delete(key as IDBValidKey));
    }
  }
  await Promise.all(deletions);
  await tx.done;
}

async function discoverLiveTabIds(currentTabId: string): Promise<Set<string>> {
  const liveTabIds = new Set<string>([currentTabId]);
  if (typeof BroadcastChannel === 'undefined') return liveTabIds;

  const channel = new BroadcastChannel(BROADCAST_CHANNEL_NAME);
  channel.onmessage = (event: MessageEvent) => {
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if (data.type === 'announce' && typeof data.tabId === 'string') {
      liveTabIds.add(data.tabId);
    } else if (data.type === 'request-presence') {
      try {
        channel.postMessage({ type: 'announce', tabId: currentTabId });
      } catch {
        // Channel closed because the tab is unloading.
      }
    }
  };

  try {
    channel.postMessage({ type: 'request-presence' });
    channel.postMessage({ type: 'announce', tabId: currentTabId });
  } catch {
    // Best effort: without replies we only keep this tab's keys.
  }

  await new Promise((resolve) => setTimeout(resolve, ORPHAN_CLEANUP_DISCOVERY_MS));
  return liveTabIds;
}

/**
 * Call once on mount. Sweeps keys belonging to tabs that are no longer open,
 * and registers a pagehide handler to drop this tab's keys on close.
 */
export function initConversationSessionLifecycle(): void {
  if (typeof window === 'undefined' || lifecycleInitialized) return;
  lifecycleInitialized = true;

  const currentTabId = getTabSessionId();

  void (async () => {
    try {
      const liveTabIds = await discoverLiveTabIds(currentTabId);
      await deleteKeysForTabIds((tabId) => !liveTabIds.has(tabId));
    } catch (error) {
      console.warn('vss-chat: could not sweep orphaned conversations', error);
    }
  })();

  window.addEventListener('pagehide', () => {
    // Fire and forget — the orphan sweep on next load is the safety net.
    deleteKeysForTabIds((tabId) => tabId === currentTabId).catch(() => {});
  });
}

export function __resetStorageForTests(): void {
  lifecycleInitialized = false;
  dbPromise = null;
}
