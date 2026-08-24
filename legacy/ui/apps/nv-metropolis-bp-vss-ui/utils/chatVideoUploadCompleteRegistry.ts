// SPDX-License-Identifier: MIT
/**
 * VSS-specific routing: multiple main tabs subscribe by tab id.
 * Generic parent/listener API lives in @nemo-agent-toolkit/ui.
 */

import type { ChatVideoUploadCompletePayload } from '../../../packages/nemo-agent-toolkit-ui/types/chatVideoUpload';
import type { SidebarMainTabId } from './sidebarMainTabChatSubscribers';

export type { ChatVideoUploadCompletePayload };

export type ChatVideoUploadCompleteListener = (
  payload: ChatVideoUploadCompletePayload,
) => void;

export type VssMainTabChatVideoUploadRegistry = {
  registerSubscriber: (
    tabId: SidebarMainTabId,
    listener: ChatVideoUploadCompleteListener,
  ) => () => void;
  emit: (payload: ChatVideoUploadCompletePayload) => void;
  createTabRegistrar: (
    tabId: SidebarMainTabId,
  ) => (listener: ChatVideoUploadCompleteListener) => () => void;
};

export function createVssMainTabChatVideoUploadRegistry(): VssMainTabChatVideoUploadRegistry {
  const subscribersByTab = new Map<
    SidebarMainTabId,
    Set<ChatVideoUploadCompleteListener>
  >();

  function getOrCreateSet(tabId: SidebarMainTabId): Set<ChatVideoUploadCompleteListener> {
    let set = subscribersByTab.get(tabId);
    if (!set) {
      set = new Set();
      subscribersByTab.set(tabId, set);
    }
    return set;
  }

  function registerSubscriber(
    tabId: SidebarMainTabId,
    listener: ChatVideoUploadCompleteListener,
  ) {
    getOrCreateSet(tabId).add(listener);
    return () => {
      getOrCreateSet(tabId).delete(listener);
    };
  }

  function emit(payload: ChatVideoUploadCompletePayload) {
    subscribersByTab.forEach((listeners) => {
      listeners.forEach((listener) => listener(payload));
    });
  }

  function createTabRegistrar(tabId: SidebarMainTabId) {
    return (listener: ChatVideoUploadCompleteListener) =>
      registerSubscriber(tabId, listener);
  }

  return { registerSubscriber, emit, createTabRegistrar };
}
