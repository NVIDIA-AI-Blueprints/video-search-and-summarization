import { Conversation } from '@/types/chat';
import {
  ExportFormatV1,
  ExportFormatV2,
  ExportFormatV3,
  ExportFormatV4,
  LatestExportFormat,
  SupportedExportFormats,
} from '@/types/export';
import { FolderInterface } from '@/types/folder';
import { Prompt } from '@/types/prompt';

import { getStorageKey } from '@/contexts/RuntimeConfigContext';

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
      folders: (data.folders || []).map((chatFolder) => ({
        id: chatFolder.id.toString(),
        name: chatFolder.name,
        type: 'chat',
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

function currentDate() {
  const date = new Date();
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return `${month}-${day}`;
}

export const exportData = async (storageKeyPrefix?: string | null) => {
  const key = (base: string) => getStorageKey(base, storageKeyPrefix);

  const history = await loadConversationsFromDb(storageKeyPrefix);

  let folders: FolderInterface[] = [];
  const foldersRaw = sessionStorage.getItem(key('folders'));
  if (foldersRaw) {
    folders = JSON.parse(foldersRaw);
  }

  let prompts: Prompt[] = [];
  const promptsRaw = sessionStorage.getItem(key('prompts'));
  if (promptsRaw) {
    prompts = JSON.parse(promptsRaw);
  }

  const data = {
    version: 4,
    history: history || [],
    folders: folders || [],
    prompts: prompts || [],
  } as LatestExportFormat;

  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: 'application/json',
  });
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
 * Error thrown when importData fails to persist conversations to IndexedDB.
 * The original IndexedDB error is preserved on the `cause` property so callers
 * can surface a meaningful message to the user.
 */
export class ConversationPersistenceError extends Error {
  override readonly cause: unknown;
  constructor(message: string, cause: unknown) {
    super(message);
    this.name = 'ConversationPersistenceError';
    this.cause = cause;
  }
}

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

  const newHistory: Conversation[] = [
    ...oldConversations,
    ...history,
  ].filter(
    (conversation, index, self) =>
      index === self.findIndex((c) => c.id === conversation.id),
  );

  try {
    await saveConversationsToDb(newHistory, storageKeyPrefix);
    if (newHistory.length > 0) {
      await saveConversationToDb(
        newHistory[newHistory.length - 1],
        storageKeyPrefix,
      );
    } else {
      await removeConversationFromDb(storageKeyPrefix);
    }
  } catch (error) {
    throw new ConversationPersistenceError(
      'Failed to persist imported conversations to IndexedDB',
      error,
    );
  }

  const oldFolders = sessionStorage.getItem(key('folders'));
  const oldFoldersParsed = oldFolders ? JSON.parse(oldFolders) : [];
  const newFolders: FolderInterface[] = [
    ...oldFoldersParsed,
    ...folders,
  ].filter(
    (folder, index, self) =>
      index === self.findIndex((f) => f.id === folder.id),
  );
  sessionStorage.setItem(key('folders'), JSON.stringify(newFolders));

  const oldPrompts = sessionStorage.getItem(key('prompts'));
  const oldPromptsParsed = oldPrompts ? JSON.parse(oldPrompts) : [];
  const newPrompts: Prompt[] = [...oldPromptsParsed, ...prompts].filter(
    (prompt, index, self) =>
      index === self.findIndex((p) => p.id === prompt.id),
  );
  sessionStorage.setItem(key('prompts'), JSON.stringify(newPrompts));

  return {
    version: 4,
    history: newHistory,
    folders: newFolders,
    prompts: newPrompts,
  };
};
