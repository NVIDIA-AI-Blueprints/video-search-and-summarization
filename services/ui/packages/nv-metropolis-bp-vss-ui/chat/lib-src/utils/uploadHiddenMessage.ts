// SPDX-License-Identifier: MIT
/**
 * Upload auto-prompt gating.
 *
 * When a video upload finishes, chat sends a hidden message on the user's
 * behalf ("Let's show the videos just uploaded ...") so the agent picks the new
 * media up. Two things can go wrong, and both are what this module prevents:
 *
 *  - The user switches conversations mid-upload, and the prompt lands in the
 *    wrong thread. Each batch records the conversation it started in, and the
 *    prompt is dropped if that no longer matches.
 *  - The user types while an upload dialog is open, racing the auto-prompt.
 *    Typed messages are blocked for the duration; the auto-prompt is not.
 */

/** Message shape these rules care about. */
export type UploadScopedMessage = {
  hidden?: boolean;
  /** Conversation active when the upload batch began. */
  uploadConversationId?: string;
  [key: string]: unknown;
};

export type ChatMessageSendCheck = {
  /** True for the generated upload prompt, false for anything the user typed. */
  hidden: boolean;
  uploadConversationId?: string;
  /** True while an upload dialog or in-flight batch owns the composer. */
  uploadFlowActive: boolean;
  activeConversationId?: string;
};

/** One completed upload batch, as observed at completion time. */
export type UploadBatchCompletion = {
  uploadConversationId: string;
  activeConversationIdAtComplete?: string;
  uploadFlowActiveAtComplete?: boolean;
};

/**
 * True when a hidden upload prompt still belongs in the active conversation.
 *
 * A prompt with no recorded scope predates batch tracking and is always sent;
 * a scoped one only survives while the user is still in that conversation.
 */
export function shouldSendUploadHiddenMessage(
  uploadConversationId: string | undefined,
  activeConversationId: string | undefined,
): boolean {
  if (uploadConversationId === undefined) return true;
  return uploadConversationId === activeConversationId;
}

/**
 * True when a message may be sent right now.
 *
 * Typed messages are blocked while the upload flow holds the composer; the
 * auto-prompt is exempt, since blocking it is what the flow is waiting on.
 */
export function shouldAllowChatMessageSend({
  hidden,
  uploadConversationId,
  uploadFlowActive,
  activeConversationId,
}: ChatMessageSendCheck): boolean {
  if (!hidden) return !uploadFlowActive;

  return shouldSendUploadHiddenMessage(uploadConversationId, activeConversationId);
}

/**
 * Drops `uploadConversationId` before a message is persisted. It is routing
 * state for a single send, and a stored copy would misgate a later replay.
 */
export function stripUploadConversationScope<T extends UploadScopedMessage>(
  message: T,
): Omit<T, 'uploadConversationId'> {
  const { uploadConversationId: _scope, ...rest } = message;
  return rest;
}

/**
 * How many of `completions` would actually send their prompt.
 *
 * Sequential uploads into the same conversation each get one — an earlier batch
 * completing does not suppress a later one, and an agent still streaming is not
 * a blocker.
 */
export function countAllowedUploadHiddenPrompts(
  completions: UploadBatchCompletion[],
): number {
  return completions.filter((completion) =>
    shouldAllowChatMessageSend({
      hidden: true,
      uploadConversationId: completion.uploadConversationId,
      uploadFlowActive: completion.uploadFlowActiveAtComplete ?? false,
      activeConversationId: completion.activeConversationIdAtComplete,
    }),
  ).length;
}
