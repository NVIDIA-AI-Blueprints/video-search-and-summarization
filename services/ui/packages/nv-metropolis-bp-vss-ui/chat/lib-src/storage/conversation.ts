// SPDX-License-Identifier: MIT
/**
 * Conversation writes.
 *
 * Persistence is fire-and-forget: these run on every streamed frame and on
 * every keystroke that edits a title, so a failed write logs and is retried by
 * the next one rather than interrupting the conversation. Callers that need the
 * write to have landed use the storage layer directly and await it.
 */
import type { Conversation } from '../types/chat';
import { saveConversationToDb, saveConversationsToDb } from './conversationDb';

export const saveConversation = (
  conversation: Conversation,
  storageKeyPrefix?: string | null,
) => {
  saveConversationToDb(conversation, storageKeyPrefix).catch((error) => {
    console.warn('Failed to persist conversation:', error);
  });
};

export const saveConversations = (
  conversations: Conversation[],
  storageKeyPrefix?: string | null,
) => {
  saveConversationsToDb(conversations, storageKeyPrefix).catch((error) => {
    console.warn('Failed to persist conversations:', error);
  });
};

/**
 * Replaces one conversation in the list and persists both the selection and
 * the list, returning the new values for the caller to put into state.
 */
export const updateConversation = (
  updatedConversation: Conversation,
  allConversations: Conversation[],
  storageKeyPrefix?: string | null,
) => {
  const updatedConversations = allConversations.map((conversation) =>
    conversation.id === updatedConversation.id ? updatedConversation : conversation,
  );

  saveConversation(updatedConversation, storageKeyPrefix);
  saveConversations(updatedConversations, storageKeyPrefix);

  return { single: updatedConversation, all: updatedConversations };
};
