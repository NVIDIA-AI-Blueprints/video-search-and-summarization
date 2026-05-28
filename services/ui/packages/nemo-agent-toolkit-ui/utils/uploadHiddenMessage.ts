import type { Message } from '@/types/chat';

/**
 * Returns whether an upload auto-prompt should be sent to the agent.
 * When uploadConversationId is set, it must match the currently active conversation.
 */
export function shouldSendUploadHiddenMessage(
  uploadConversationId: string | undefined,
  activeConversationId: string | undefined,
): boolean {
  if (!uploadConversationId) {
    return true;
  }
  if (!activeConversationId) {
    return false;
  }
  return activeConversationId === uploadConversationId;
}

export type ChatMessageSendGateInput = {
  hidden?: boolean;
  uploadConversationId?: string;
  uploadFlowActive: boolean;
  activeConversationId?: string;
};

/**
 * Mirrors handleSend early returns for upload-related chat gating.
 */
export function shouldAllowChatMessageSend(input: ChatMessageSendGateInput): boolean {
  if (input.uploadFlowActive && !input.hidden) {
    return false;
  }
  if (
    input.hidden &&
    !shouldSendUploadHiddenMessage(
      input.uploadConversationId,
      input.activeConversationId,
    )
  ) {
    return false;
  }
  return true;
}

/** Removes upload-scoped fields before persisting a message. */
export function stripUploadConversationScope(message: Message): Message {
  const { uploadConversationId: _uploadConversationId, ...rest } = message;
  return rest;
}

export type UploadBatchCompletion = {
  /** Conversation id captured when the user confirmed the upload batch. */
  uploadConversationId: string;
  /** Active conversation id when the batch finished and the hidden prompt would fire. */
  activeConversationIdAtComplete: string | undefined;
  /** Whether any upload dialog was still open (blocks normal chat only). */
  uploadFlowActiveAtComplete?: boolean;
};

/**
 * Simulates dispatching hidden upload prompts across one or more completed batches.
 * Returns how many prompts would reach handleSend / the agent.
 */
export function countAllowedUploadHiddenPrompts(
  batches: UploadBatchCompletion[],
): number {
  let sent = 0;
  for (const batch of batches) {
    const allowed = shouldAllowChatMessageSend({
      hidden: true,
      uploadConversationId: batch.uploadConversationId,
      uploadFlowActive: batch.uploadFlowActiveAtComplete ?? false,
      activeConversationId: batch.activeConversationIdAtComplete,
    });
    if (allowed) {
      sent += 1;
    }
  }
  return sent;
}
