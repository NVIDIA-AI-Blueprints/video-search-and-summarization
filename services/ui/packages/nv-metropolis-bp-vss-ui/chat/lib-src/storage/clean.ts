// SPDX-License-Identifier: MIT
/**
 * Repairs conversations read from storage or an import file.
 *
 * Both sources predate current invariants or were hand-edited, so fields the
 * renderer requires may be missing. A conversation that cannot be repaired is
 * dropped rather than allowed to crash the list it appears in.
 */
import type { Conversation } from '../types/chat';

export function cleanConversationHistory(history: any[]): Conversation[] {
  if (!Array.isArray(history)) {
    console.warn('history is not an array. Returning an empty array.');
    return [];
  }

  return history.reduce((accumulated: Conversation[], conversation) => {
    try {
      if (!conversation.folderId) conversation.folderId = null;
      if (!conversation.messages) conversation.messages = [];

      accumulated.push(conversation);
    } catch (error) {
      console.warn("error while cleaning conversations' history. Removing culprit", error);
    }

    return accumulated;
  }, []);
}
