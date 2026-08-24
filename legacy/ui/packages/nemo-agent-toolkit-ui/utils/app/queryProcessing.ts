/**
 * True while the agent is processing a user query (HTTP stream or WebSocket).
 */
export function isQueryProcessing(
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  return loading || messageIsStreaming;
}

/**
 * True when the given conversation is the selected one and has an in-flight query.
 */
export function isActiveConversationProcessing(
  conversationId: string,
  selectedConversationId: string | undefined,
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  if (!selectedConversationId || conversationId !== selectedConversationId) {
    return false;
  }
  return isQueryProcessing(loading, messageIsStreaming);
}

type ConversationProcessingCheck = {
  id: string;
  folderId: string | null;
  /** Set when a query runs in the background for this conversation (future / multi-flight). */
  isQueryInFlight?: boolean;
};

/**
 * True when this conversation has an in-flight query (selected + global flags, or explicit flag).
 */
export function isConversationQueryInFlight(
  conversation: ConversationProcessingCheck,
  selectedConversationId: string | undefined,
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  if (conversation.isQueryInFlight === true) {
    return true;
  }
  return isActiveConversationProcessing(
    conversation.id,
    selectedConversationId,
    loading,
    messageIsStreaming,
  );
}

/**
 * True when folder delete must be blocked because any conversation in the folder is processing.
 */
export function isFolderDeleteBlocked(
  folderId: string,
  conversations: ConversationProcessingCheck[],
  selectedConversationId: string | undefined,
  loading: boolean,
  messageIsStreaming: boolean,
): boolean {
  return conversations
    .filter((conversation) => conversation.folderId === folderId)
    .some((conversation) =>
      isConversationQueryInFlight(
        conversation,
        selectedConversationId,
        loading,
        messageIsStreaming,
      ),
    );
}
