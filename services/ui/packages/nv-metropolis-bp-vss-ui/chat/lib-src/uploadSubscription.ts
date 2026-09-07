// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: MIT AND Apache-2.0
/**
 * Fan-out for chat video-upload completions.
 *
 * The chat panel is where an upload finishes, but the tabs that care — Video
 * Management refreshing its stream list, Search reindexing — live elsewhere.
 * The host app owns the registry and hands each tab a registrar; this is the
 * contract between them.
 *
 * Lived in the toolkit until the toolkit was removed. It belongs here because
 * it pairs with `ChatVideoUploadCompletePayload`, which this package emits.
 */
import { useEffect } from 'react';

import type { ChatVideoUploadCompletePayload } from './types';

/** Subscribe a listener; returns an unsubscribe when the host provides one. */
export type RegisterChatVideoUploadComplete = (
  listener: (payload: ChatVideoUploadCompletePayload) => void,
) => void | (() => void);

/**
 * @example
 * useChatVideoUploadCompleteSubscription(registerChatVideoUploadComplete, () => {
 *   refetch();
 * });
 */
export function useChatVideoUploadCompleteSubscription(
  register: RegisterChatVideoUploadComplete | undefined,
  onComplete: (payload: ChatVideoUploadCompletePayload) => void,
): void {
  useEffect(() => {
    if (!register) return;
    return register(onComplete);
  }, [register, onComplete]);
}
