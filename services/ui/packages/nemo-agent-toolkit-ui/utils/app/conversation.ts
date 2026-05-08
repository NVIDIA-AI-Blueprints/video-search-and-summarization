import { Conversation } from '@/types/chat';

import {
  saveConversationToDb,
  saveConversationsToDb,
} from './conversationDb';

export const updateConversation = (
  updatedConversation: Conversation,
  allConversations: Conversation[],
  storageKeyPrefix?: string | null,
) => {
  const updatedConversations = allConversations.map((c) => {
    if (c.id === updatedConversation.id) {
      return updatedConversation;
    }

    return c;
  });

  saveConversation(updatedConversation, storageKeyPrefix);
  saveConversations(updatedConversations, storageKeyPrefix);

  return {
    single: updatedConversation,
    all: updatedConversations,
  };
};

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
