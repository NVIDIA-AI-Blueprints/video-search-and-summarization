import toast from 'react-hot-toast';

import { Conversation, Role } from '@/types/chat';

const key = (base: string, prefix?: string | null) =>
  prefix ? `${prefix}_${base}` : base;

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
  try {
    sessionStorage.setItem(
      key('selectedConversation', storageKeyPrefix),
      JSON.stringify(conversation),
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === 'QuotaExceededError') {
      console.log('Storage quota exceeded, cannot save conversation.');
      toast.error('Storage quota exceeded, cannot save conversation.');
    }
  }
};

export const saveConversations = (
  conversations: Conversation[],
  storageKeyPrefix?: string | null,
) => {
  try {
    sessionStorage.setItem(
      key('conversationHistory', storageKeyPrefix),
      JSON.stringify(conversations),
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === 'QuotaExceededError') {
      console.log('Storage quota exceeded, cannot save conversations.');
      toast.error('Storage quota exceeded, cannot save conversation.');
    }
  }
};
