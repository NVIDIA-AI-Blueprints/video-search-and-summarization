// SPDX-License-Identifier: MIT
/**
 * Conversation import and export.
 *
 * Import accepts every historical export format and upgrades it to the latest,
 * merging into existing data rather than replacing it — a user importing a
 * backup should not lose the conversations they already have.
 */
import { getStorageKey } from '../contexts/RuntimeConfigContext';
import type { Conversation } from '../types/chat';
import type {
  ExportFormatV1,
  ExportFormatV2,
  ExportFormatV3,
  ExportFormatV4,
  FolderInterface,
  LatestExportFormat,
  Prompt,
  SupportedExportFormats,
} from '../types/export';
import { cleanConversationHistory } from './clean';
import {
  loadConversationsFromDb,
  removeConversationFromDb,
  saveConversationsToDb,
  saveConversationToDb,
} from './conversationDb';

export function isExportFormatV1(obj: any): obj is ExportFormatV1 {
  return Array.isArray(obj);
}

export function isExportFormatV2(obj: any): obj is ExportFormatV2 {
  return !('version' in obj) && 'folders' in obj && 'history' in obj;
}

export function isExportFormatV3(obj: any): obj is ExportFormatV3 {
  return obj.version === 3;
}

export function isExportFormatV4(obj: any): obj is ExportFormatV4 {
  return obj.version === 4;
}

export const isLatestExportFormat = isExportFormatV4;

/**
 * Upgrades any supported export to the latest format.
 *
 * @throws when the shape matches no known version.
 */
export function cleanData(data: SupportedExportFormats): LatestExportFormat {
  if (isExportFormatV1(data)) {
    return {
      version: 4,
      history: cleanConversationHistory(data),
      folders: [],
      prompts: [],
    };
  }

  if (isExportFormatV2(data)) {
    return {
      version: 4,
      history: cleanConversationHistory(data.history || []),
      // v2 folders were numerically keyed and had no type.
      folders: (data.folders || []).map((chatFolder) => ({
        id: chatFolder.id.toString(),
        name: chatFolder.name,
        type: 'chat' as const,
      })),
      prompts: [],
    };
  }

  if (isExportFormatV3(data)) {
    return { ...data, version: 4, prompts: [] };
  }

  if (isExportFormatV4(data)) {
    return data;
  }

  throw new Error('Unsupported data format');
}

function currentDate(): string {
  const date = new Date();
  return `${date.getMonth() + 1}-${date.getDate()}`;
}

/** Downloads the current conversations, folders and prompts as a v4 export. */
export const exportData = async (storageKeyPrefix?: string | null) => {
  const key = (base: string) => getStorageKey(base, storageKeyPrefix);

  const history = await loadConversationsFromDb(storageKeyPrefix);

  const foldersRaw = sessionStorage.getItem(key('folders'));
  const folders: FolderInterface[] = foldersRaw ? JSON.parse(foldersRaw) : [];

  const promptsRaw = sessionStorage.getItem(key('prompts'));
  const prompts: Prompt[] = promptsRaw ? JSON.parse(promptsRaw) : [];

  const data: LatestExportFormat = {
    version: 4,
    history: history || [],
    folders: folders || [],
    prompts: prompts || [],
  };

  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.download = `chatbot_ui_history_${currentDate()}.json`;
  link.href = url;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

/**
 * Raised when an import cannot be persisted.
 *
 * Carries the underlying IndexedDB failure on `cause` so the UI can explain
 * what went wrong instead of reporting a generic import failure.
 */
export class ConversationPersistenceError extends Error {
  override readonly cause: unknown;

  constructor(message: string, cause: unknown) {
    super(message);
    this.name = 'ConversationPersistenceError';
    this.cause = cause;
  }
}

/** Keeps the first occurrence of each id. */
function dedupeById<T extends { id: string | number }>(items: T[]): T[] {
  return items.filter(
    (item, index, all) => index === all.findIndex((other) => other.id === item.id),
  );
}

/**
 * Merges an export into stored data and returns the result.
 *
 * Existing entries win on id collision, so re-importing the same file is a
 * no-op rather than a duplicator.
 *
 * @throws ConversationPersistenceError if reading or writing IndexedDB fails.
 */
export const importData = async (
  data: SupportedExportFormats,
  storageKeyPrefix?: string | null,
): Promise<LatestExportFormat> => {
  const { history, folders, prompts } = cleanData(data);
  const key = (base: string) => getStorageKey(base, storageKeyPrefix);

  let oldConversations: Conversation[];
  try {
    oldConversations = await loadConversationsFromDb(storageKeyPrefix);
  } catch (error) {
    throw new ConversationPersistenceError(
      'Failed to read existing conversations from IndexedDB during import',
      error,
    );
  }

  const newHistory = dedupeById<Conversation>([...oldConversations, ...history]);

  try {
    await saveConversationsToDb(newHistory, storageKeyPrefix);

    // Select the most recent conversation so the user lands somewhere real.
    if (newHistory.length > 0) {
      await saveConversationToDb(newHistory[newHistory.length - 1], storageKeyPrefix);
    } else {
      await removeConversationFromDb(storageKeyPrefix);
    }
  } catch (error) {
    throw new ConversationPersistenceError(
      'Failed to persist imported conversations to IndexedDB',
      error,
    );
  }

  // Folders and prompts are small and stay in sessionStorage.
  const oldFolders = sessionStorage.getItem(key('folders'));
  const newFolders = dedupeById<FolderInterface>([
    ...(oldFolders ? JSON.parse(oldFolders) : []),
    ...folders,
  ]);
  sessionStorage.setItem(key('folders'), JSON.stringify(newFolders));

  const oldPrompts = sessionStorage.getItem(key('prompts'));
  const newPrompts = dedupeById<Prompt>([
    ...(oldPrompts ? JSON.parse(oldPrompts) : []),
    ...prompts,
  ]);
  sessionStorage.setItem(key('prompts'), JSON.stringify(newPrompts));

  return { version: 4, history: newHistory, folders: newFolders, prompts: newPrompts };
};
