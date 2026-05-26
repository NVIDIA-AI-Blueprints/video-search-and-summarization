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
 * (onChatVideoUploadComplete on SidebarNemoAgentToolkitApp), not the full-page Chat tab.
 * The parent implements that single callback and fans out (e.g. VSS tab registry emit).
 */
import type { RegisterChatVideoUploadComplete } from '@nemo-agent-toolkit/ui';

export type { RegisterChatVideoUploadComplete };

export type MainTabChatVideoUploadBridgeProps = {
  registerChatVideoUploadComplete?: RegisterChatVideoUploadComplete;
};
