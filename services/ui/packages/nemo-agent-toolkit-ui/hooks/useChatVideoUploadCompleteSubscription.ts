import { useEffect } from 'react';
import type { ChatVideoUploadCompletePayload } from '@/types/chatVideoUpload';

export type RegisterChatVideoUploadComplete = (
  listener: (payload: ChatVideoUploadCompletePayload) => void,
) => void | (() => void);

/**
 * Subscribe to chat video upload completion using a registrar from the parent app.
 *
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
