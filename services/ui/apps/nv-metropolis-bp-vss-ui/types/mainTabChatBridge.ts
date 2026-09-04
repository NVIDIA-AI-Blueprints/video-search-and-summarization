// SPDX-License-Identifier: MIT
/**
 * Adding chat video-upload completion to another main tab (3 steps):
 *
 * 1. Home.tsx — pass the tab registrar (one line):
 *    componentProps.registerChatVideoUploadComplete =
 *      registerMainTabChatVideoUploadComplete['your-tab-id'];
 *
 * 2. Tab props type — extend with RegisterChatVideoUploadComplete (optional shared type below).
 *
 * 3. Tab component — subscribe (one hook):
 *    useChatVideoUploadCompleteSubscription(registerChatVideoUploadComplete, () => {
 *      refetch();
 *    });
 *
 * Upload completion is wired only on the floating sidebar chat instance in Home
 * (onChatVideoUploadComplete on the sidebar ChatPanel), not the full-page Chat tab.
 * The parent implements that single callback and fans out (e.g. VSS tab registry emit).
 */
import type { ChatVideoUploadCompletePayload } from '@nv-metropolis-bp-vss-ui/chat';

/**
 * Subscribe to upload-batch completions; returns an unsubscribe when it has one.
 *
 * Defined here rather than imported: this is the app's own fan-out contract
 * between Home and its tabs, and it outlived the toolkit that first declared it.
 */
export type RegisterChatVideoUploadComplete = (
  listener: (payload: ChatVideoUploadCompletePayload) => void,
) => void | (() => void);

export type MainTabChatVideoUploadBridgeProps = {
  registerChatVideoUploadComplete?: RegisterChatVideoUploadComplete;
};
