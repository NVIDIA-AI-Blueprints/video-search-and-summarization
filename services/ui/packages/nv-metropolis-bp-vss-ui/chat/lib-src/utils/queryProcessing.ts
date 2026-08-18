// SPDX-License-Identifier: MIT
/**
 * Predicates describing when a query is in flight.
 *
 * These gate destructive UI affordances — switching conversations, deleting a
 * folder — so a running query is never silently discarded. Kept as pure
 * functions so the rules are testable without mounting the chat.
 */

/** A conversation, reduced to only what these predicates need. */
export type ProcessingScopedConversation = {
  id: string;
  folderId: string | null;
  /**
   * Set when a query is running for this conversation while a different one is
   * selected. The global loading/streaming flags only describe the selected
   * conversation, so background work needs its own marker.
   */
  isQueryInFlight?: boolean;
};

/** True while the agent is working, over either transport. */
export function isQueryProcessing(
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  return loading || messageIsStreaming;
}

/**
 * True when `conversationId` is the selected conversation *and* a query is
 * running. The global flags say nothing about unselected conversations, so a
 * mismatched id is always false.
 */
export function isActiveConversationProcessing(
  conversationId: string,
  selectedConversationId: string | undefined,
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  const isSelected =
    selectedConversationId !== undefined &&
    conversationId === selectedConversationId;

  return isSelected && isQueryProcessing(loading, messageIsStreaming);
}

/**
 * True when this conversation has a query in flight, whether it is the selected
 * one or is running in the background.
 */
export function isConversationQueryInFlight(
  conversation: ProcessingScopedConversation,
  selectedConversationId: string | undefined,
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  return (
    conversation.isQueryInFlight === true ||
    isActiveConversationProcessing(
      conversation.id,
      selectedConversationId,
      loading,
      messageIsStreaming,
    )
  );
}

/**
 * True when deleting `folderId` must be blocked because at least one
 * conversation inside it still has a query in flight.
 */
export function isFolderDeleteBlocked(
  folderId: string,
  conversations: ProcessingScopedConversation[],
  selectedConversationId: string | undefined,
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  return conversations.some(
    (conversation) =>
      conversation.folderId === folderId &&
      isConversationQueryInFlight(
        conversation,
        selectedConversationId,
        loading,
        messageIsStreaming,
      ),
  );
}
