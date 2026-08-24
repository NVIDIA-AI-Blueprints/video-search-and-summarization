/**
 * Tests for IndexedDB conversation persistence layer (conversationDb.ts).
 *
 * Validates the migration from sessionStorage to IndexedDB for storing
 * conversation history and selected conversation state, and verifies that
 * IndexedDB data is namespaced by a per-tab session id so it follows
 * sessionStorage wipe semantics (cleared on tab close, window close, reboot).
 */

const mockPut = jest.fn().mockResolvedValue(undefined);
const mockGet = jest.fn().mockResolvedValue(undefined);
const mockDelete = jest.fn().mockResolvedValue(undefined);
const mockGetAllKeys = jest.fn().mockResolvedValue([]);
const mockObjectStoreDelete = jest.fn().mockResolvedValue(undefined);
const mockTxDone = Promise.resolve();

jest.mock('idb', () => {
  return {
    openDB: jest.fn(() =>
      Promise.resolve({
        put: mockPut,
        get: mockGet,
        delete: mockDelete,
        getAllKeys: mockGetAllKeys,
        transaction: jest.fn(() => ({
          objectStore: jest.fn(() => ({
            delete: mockObjectStoreDelete,
          })),
          done: mockTxDone,
        })),
        objectStoreNames: { contains: () => false },
      }),
    ),
  };
});

const FIXED_TAB_ID = 'tab_test_fixed';
const TAB_KEY = 'aiqtoolkit-chat-tab-session';

import {
  saveConversationToDb,
  saveConversationsToDb,
  loadConversationFromDb,
  loadConversationsFromDb,
  removeConversationFromDb,
  clearAllConversationsFromDb,
  initConversationSessionLifecycle,
  __resetConversationDbForTests,
} from '@/utils/app/conversationDb';

const STORE = 'conversations';

/**
 * The repo-wide jest.setup.js replaces sessionStorage with bare jest mocks
 * (no internal storage). For these tests we need real persistence so the
 * tab session id is read back consistently. Wire up a Map-backed mock for
 * each test.
 */
function installFunctionalSessionStorage(initial: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initial));
  (sessionStorage.getItem as jest.Mock).mockImplementation(
    (k: string) => (store.has(k) ? (store.get(k) as string) : null),
  );
  (sessionStorage.setItem as jest.Mock).mockImplementation(
    (k: string, v: string) => {
      store.set(k, String(v));
    },
  );
  (sessionStorage.removeItem as jest.Mock).mockImplementation((k: string) => {
    store.delete(k);
  });
  (sessionStorage.clear as jest.Mock).mockImplementation(() => {
    store.clear();
  });
  return store;
}

describe('conversationDb – IndexedDB persistence layer', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    installFunctionalSessionStorage({ [TAB_KEY]: FIXED_TAB_ID });
    __resetConversationDbForTests();
  });

  describe('saveConversationToDb', () => {
    it('stores conversation under tab-prefixed selectedConversation key', async () => {
      const conv = { id: 'c1', name: 'Chat', messages: [], folderId: null };
      await saveConversationToDb(conv as any);
      expect(mockPut).toHaveBeenCalledWith(
        STORE,
        conv,
        `${FIXED_TAB_ID}__selectedConversation`,
      );
    });

    it('prefixes key when storageKeyPrefix is provided', async () => {
      const conv = { id: 'c1', name: 'Chat', messages: [], folderId: null };
      await saveConversationToDb(conv as any, 'searchTab');
      expect(mockPut).toHaveBeenCalledWith(
        STORE,
        conv,
        `${FIXED_TAB_ID}__searchTab_selectedConversation`,
      );
    });

    it('propagates IndexedDB errors to the caller', async () => {
      mockPut.mockRejectedValueOnce(new Error('IndexedDB write failed'));
      const conv = { id: 'c1', name: 'Chat', messages: [], folderId: null };
      await expect(saveConversationToDb(conv as any)).rejects.toThrow(
        'IndexedDB write failed',
      );
    });
  });

  describe('saveConversationsToDb', () => {
    it('stores conversations array under tab-prefixed conversationHistory key', async () => {
      const convs = [
        { id: 'c1', name: 'Chat 1', messages: [], folderId: null },
        { id: 'c2', name: 'Chat 2', messages: [], folderId: null },
      ];
      await saveConversationsToDb(convs as any);
      expect(mockPut).toHaveBeenCalledWith(
        STORE,
        convs,
        `${FIXED_TAB_ID}__conversationHistory`,
      );
    });

    it('prefixes key with storageKeyPrefix', async () => {
      await saveConversationsToDb([] as any, 'alertsTab');
      expect(mockPut).toHaveBeenCalledWith(
        STORE,
        [],
        `${FIXED_TAB_ID}__alertsTab_conversationHistory`,
      );
    });

    it('propagates IndexedDB errors to the caller', async () => {
      mockPut.mockRejectedValueOnce(new Error('IndexedDB bulk write failed'));
      await expect(saveConversationsToDb([] as any)).rejects.toThrow(
        'IndexedDB bulk write failed',
      );
    });
  });

  describe('loadConversationFromDb', () => {
    it('returns conversation when data exists', async () => {
      const conv = { id: 'c1', name: 'Chat', messages: [] };
      mockGet.mockResolvedValueOnce(conv);
      const result = await loadConversationFromDb();
      expect(result).toEqual(conv);
      expect(mockGet).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__selectedConversation`,
      );
    });

    it('returns null when no data exists', async () => {
      mockGet.mockResolvedValueOnce(undefined);
      const result = await loadConversationFromDb();
      expect(result).toBeNull();
    });

    it('propagates IndexedDB errors to the caller', async () => {
      mockGet.mockRejectedValueOnce(new Error('Read failed'));
      await expect(loadConversationFromDb()).rejects.toThrow('Read failed');
    });

    it('uses prefixed key when storageKeyPrefix is provided', async () => {
      mockGet.mockResolvedValueOnce(null);
      await loadConversationFromDb('myTab');
      expect(mockGet).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__myTab_selectedConversation`,
      );
    });
  });

  describe('loadConversationsFromDb', () => {
    it('returns conversations array when data exists', async () => {
      const convs = [{ id: 'c1' }, { id: 'c2' }];
      mockGet.mockResolvedValueOnce(convs);
      const result = await loadConversationsFromDb();
      expect(result).toEqual(convs);
    });

    it('returns empty array when no data exists', async () => {
      mockGet.mockResolvedValueOnce(undefined);
      const result = await loadConversationsFromDb();
      expect(result).toEqual([]);
    });

    it('propagates IndexedDB errors to the caller', async () => {
      mockGet.mockRejectedValueOnce(new Error('Read failed'));
      await expect(loadConversationsFromDb()).rejects.toThrow('Read failed');
    });
  });

  describe('removeConversationFromDb', () => {
    it('deletes tab-prefixed selectedConversation key from store', async () => {
      await removeConversationFromDb();
      expect(mockDelete).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__selectedConversation`,
      );
    });

    it('uses prefixed key', async () => {
      await removeConversationFromDb('tab1');
      expect(mockDelete).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__tab1_selectedConversation`,
      );
    });

    it('propagates IndexedDB errors to the caller', async () => {
      mockDelete.mockRejectedValueOnce(new Error('Delete failed'));
      await expect(removeConversationFromDb()).rejects.toThrow('Delete failed');
    });
  });

  describe('clearAllConversationsFromDb', () => {
    it('deletes both tab-prefixed selectedConversation and conversationHistory keys', async () => {
      await clearAllConversationsFromDb();
      expect(mockDelete).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__selectedConversation`,
      );
      expect(mockDelete).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__conversationHistory`,
      );
    });

    it('uses prefixed keys', async () => {
      await clearAllConversationsFromDb('p');
      expect(mockDelete).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__p_selectedConversation`,
      );
      expect(mockDelete).toHaveBeenCalledWith(
        STORE,
        `${FIXED_TAB_ID}__p_conversationHistory`,
      );
    });

    it('propagates IndexedDB errors to the caller', async () => {
      mockDelete.mockRejectedValueOnce(new Error('Delete failed'));
      await expect(clearAllConversationsFromDb()).rejects.toThrow('Delete failed');
    });
  });

  describe('tab session id', () => {
    it('reuses an existing tab session id from sessionStorage', async () => {
      // Beforeach pre-populated FIXED_TAB_ID; saving should use it.
      await saveConversationToDb({
        id: 'c1',
        name: 'Chat',
        messages: [],
        folderId: null,
      } as any);
      expect(mockPut).toHaveBeenCalledWith(
        STORE,
        expect.any(Object),
        `${FIXED_TAB_ID}__selectedConversation`,
      );
      expect(sessionStorage.getItem(TAB_KEY)).toBe(FIXED_TAB_ID);
    });

    it('generates a new tab session id when none exists yet', async () => {
      installFunctionalSessionStorage();
      __resetConversationDbForTests();
      await saveConversationToDb({
        id: 'c1',
        name: 'Chat',
        messages: [],
        folderId: null,
      } as any);
      const generatedId = sessionStorage.getItem(TAB_KEY);
      expect(generatedId).toBeTruthy();
      expect(generatedId).toMatch(/^tab_/);
      expect(mockPut).toHaveBeenCalledWith(
        STORE,
        expect.any(Object),
        `${generatedId}__selectedConversation`,
      );
    });
  });

  describe('initConversationSessionLifecycle – orphan sweep', () => {
    const realBroadcastChannel = (global as any).BroadcastChannel;

    afterEach(() => {
      (global as any).BroadcastChannel = realBroadcastChannel;
    });

    it('deletes IndexedDB keys whose tab id is not currently live', async () => {
      // Simulate no other tabs alive
      (global as any).BroadcastChannel = class {
        onmessage: any = null;
        postMessage = jest.fn();
        close = jest.fn();
      };

      mockGetAllKeys.mockResolvedValueOnce([
        `${FIXED_TAB_ID}__selectedConversation`,
        `${FIXED_TAB_ID}__conversationHistory`,
        'tab_old_abc__selectedConversation',
        'tab_old_abc__searchTab_conversationHistory',
        'legacyKeyWithoutTabId',
      ]);

      initConversationSessionLifecycle();

      // Wait for the discovery window + microtasks to flush
      await new Promise((resolve) => setTimeout(resolve, 350));

      // Current tab keys should NOT be deleted
      expect(mockObjectStoreDelete).not.toHaveBeenCalledWith(
        `${FIXED_TAB_ID}__selectedConversation`,
      );
      expect(mockObjectStoreDelete).not.toHaveBeenCalledWith(
        `${FIXED_TAB_ID}__conversationHistory`,
      );
      // Orphaned keys from a closed tab should be deleted
      expect(mockObjectStoreDelete).toHaveBeenCalledWith(
        'tab_old_abc__selectedConversation',
      );
      expect(mockObjectStoreDelete).toHaveBeenCalledWith(
        'tab_old_abc__searchTab_conversationHistory',
      );
      // Legacy untagged keys should be deleted
      expect(mockObjectStoreDelete).toHaveBeenCalledWith('legacyKeyWithoutTabId');
    });

    it('preserves keys belonging to other live tabs', async () => {
      const otherLiveTabId = 'tab_live_xyz';
      // Simulate another tab announcing itself
      (global as any).BroadcastChannel = class {
        onmessage: ((e: { data: any }) => void) | null = null;
        postMessage = jest.fn((data: any) => {
          if (data?.type === 'request-presence') {
            // Asynchronously respond as if another tab is alive
            setTimeout(() => {
              this.onmessage?.({
                data: { type: 'announce', tabId: otherLiveTabId },
              });
            }, 0);
          }
        });
        close = jest.fn();
      };

      mockGetAllKeys.mockResolvedValueOnce([
        `${FIXED_TAB_ID}__selectedConversation`,
        `${otherLiveTabId}__conversationHistory`,
        'tab_dead_zzz__selectedConversation',
      ]);

      initConversationSessionLifecycle();

      await new Promise((resolve) => setTimeout(resolve, 350));

      // Live tabs (current + announced peer) preserved
      expect(mockObjectStoreDelete).not.toHaveBeenCalledWith(
        `${FIXED_TAB_ID}__selectedConversation`,
      );
      expect(mockObjectStoreDelete).not.toHaveBeenCalledWith(
        `${otherLiveTabId}__conversationHistory`,
      );
      // Dead tab data swept
      expect(mockObjectStoreDelete).toHaveBeenCalledWith(
        'tab_dead_zzz__selectedConversation',
      );
    });
  });
});
