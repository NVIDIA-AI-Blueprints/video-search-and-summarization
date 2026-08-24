/**
 * Tests for the async IndexedDB migration of importData and exportData.
 *
 * Validates that import/export functions now use IndexedDB (via conversationDb)
 * instead of sessionStorage for conversation history persistence.
 */

import {
  loadConversationsFromDb,
  saveConversationsToDb,
  saveConversationToDb,
  removeConversationFromDb,
} from '@/utils/app/conversationDb';

jest.mock('@/utils/app/conversationDb', () => ({
  loadConversationsFromDb: jest.fn().mockResolvedValue([]),
  saveConversationsToDb: jest.fn().mockResolvedValue(undefined),
  saveConversationToDb: jest.fn().mockResolvedValue(undefined),
  removeConversationFromDb: jest.fn().mockResolvedValue(undefined),
}));

// The source imports from @/contexts/RuntimeConfigContext which the tsconfig
// maps to lib-src/contexts/, but jest only has @/* → ./* so we mock the
// fallback path that next/jest resolves.
jest.mock(
  require.resolve('../../../lib-src/contexts/RuntimeConfigContext'),
  () => ({
    getStorageKey: (base: string, prefix?: string | null) =>
      prefix ? `${prefix}_${base}` : base,
  }),
);

import { ConversationPersistenceError, importData } from '@/utils/app/importExport';

describe('importData – async IndexedDB migration', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    sessionStorage.clear();
  });

  it('returns a Promise (is async)', () => {
    const data = { version: 4, history: [], folders: [], prompts: [] };
    const result = importData(data as any);
    expect(result).toBeInstanceOf(Promise);
  });

  it('loads existing conversations from IndexedDB, not sessionStorage', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    const data = { version: 4, history: [], folders: [], prompts: [] };
    await importData(data as any);
    expect(loadConversationsFromDb).toHaveBeenCalled();
  });

  it('merges imported history with existing IndexedDB conversations', async () => {
    const existing = [
      { id: 'old-1', name: 'Old Chat', messages: [], folderId: null },
    ];
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce(existing);

    const data = {
      version: 4,
      history: [{ id: 'new-1', name: 'New Chat', messages: [], folderId: null }],
      folders: [],
      prompts: [],
    };

    const result = await importData(data as any);
    expect(result.history).toHaveLength(2);
    expect(result.history.map((c: any) => c.id)).toEqual(['old-1', 'new-1']);
  });

  it('deduplicates conversations by id during import', async () => {
    const existing = [
      { id: 'dup-1', name: 'Existing', messages: [], folderId: null },
    ];
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce(existing);

    const data = {
      version: 4,
      history: [{ id: 'dup-1', name: 'Duplicate', messages: [], folderId: null }],
      folders: [],
      prompts: [],
    };

    const result = await importData(data as any);
    expect(result.history).toHaveLength(1);
    expect(result.history[0].name).toBe('Existing');
  });

  it('saves merged history to IndexedDB via saveConversationsToDb', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    const conv = { id: 'c1', name: 'Chat', messages: [], folderId: null };
    const data = { version: 4, history: [conv], folders: [], prompts: [] };

    await importData(data as any);
    expect(saveConversationsToDb).toHaveBeenCalledWith([conv], undefined);
  });

  it('sets selected conversation to last imported when history is non-empty', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    const conv1 = { id: 'c1', name: 'First', messages: [], folderId: null };
    const conv2 = { id: 'c2', name: 'Last', messages: [], folderId: null };
    const data = { version: 4, history: [conv1, conv2], folders: [], prompts: [] };

    await importData(data as any);
    expect(saveConversationToDb).toHaveBeenCalledWith(conv2, undefined);
  });

  it('removes selected conversation when imported history is empty and no existing', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    const data = { version: 4, history: [], folders: [], prompts: [] };

    await importData(data as any);
    expect(removeConversationFromDb).toHaveBeenCalled();
  });

  it('passes storageKeyPrefix through to IndexedDB functions', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    const conv = { id: 'c1', name: 'Chat', messages: [], folderId: null };
    const data = { version: 4, history: [conv], folders: [], prompts: [] };

    await importData(data as any, 'searchTab');
    expect(loadConversationsFromDb).toHaveBeenCalledWith('searchTab');
    expect(saveConversationsToDb).toHaveBeenCalledWith([conv], 'searchTab');
    expect(saveConversationToDb).toHaveBeenCalledWith(conv, 'searchTab');
  });

  it('throws ConversationPersistenceError when loading existing history fails', async () => {
    const cause = new Error('IndexedDB read failed');
    (loadConversationsFromDb as jest.Mock).mockRejectedValueOnce(cause);
    const data = { version: 4, history: [], folders: [], prompts: [] };

    await expect(importData(data as any)).rejects.toBeInstanceOf(
      ConversationPersistenceError,
    );
    // Persistence should not be attempted if the load failed
    expect(saveConversationsToDb).not.toHaveBeenCalled();
    expect(saveConversationToDb).not.toHaveBeenCalled();
  });

  it('throws ConversationPersistenceError when saving merged history fails', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    const cause = new Error('IndexedDB write failed');
    (saveConversationsToDb as jest.Mock).mockRejectedValueOnce(cause);

    const conv = { id: 'c1', name: 'Chat', messages: [], folderId: null };
    const data = { version: 4, history: [conv], folders: [], prompts: [] };

    const promise = importData(data as any);
    await expect(promise).rejects.toBeInstanceOf(ConversationPersistenceError);
    await expect(promise).rejects.toMatchObject({ cause });
  });

  it('throws ConversationPersistenceError when saving selected conversation fails', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    (saveConversationsToDb as jest.Mock).mockResolvedValueOnce(undefined);
    (saveConversationToDb as jest.Mock).mockRejectedValueOnce(
      new Error('IndexedDB selected write failed'),
    );

    const conv = { id: 'c1', name: 'Chat', messages: [], folderId: null };
    const data = { version: 4, history: [conv], folders: [], prompts: [] };

    await expect(importData(data as any)).rejects.toBeInstanceOf(
      ConversationPersistenceError,
    );
  });

  it('still stores folders in sessionStorage (not migrated to IndexedDB)', async () => {
    (loadConversationsFromDb as jest.Mock).mockResolvedValueOnce([]);
    const folder = { id: 'f1', name: 'My Folder', type: 'chat' };
    const data = { version: 4, history: [], folders: [folder], prompts: [] };

    await importData(data as any);
    // sessionStorage.setItem is called with the key from getStorageKey
    expect(sessionStorage.setItem).toHaveBeenCalled();
    const foldersCalls = (sessionStorage.setItem as jest.Mock).mock.calls.filter(
      ([key]: string[]) => key.includes('folders'),
    );
    expect(foldersCalls.length).toBeGreaterThan(0);
    expect(JSON.parse(foldersCalls[0][1])).toEqual([folder]);
  });
});
