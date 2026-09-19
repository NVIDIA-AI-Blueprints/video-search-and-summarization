// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import type { ChatVideoUploadCompleteListener } from './chatVideoUploadCompleteRegistry';
import { SIDEBAR_MAIN_TAB_IDS, type SidebarMainTabId } from './sidebarMainTabChatSubscribers';
import type { VssMainTabChatVideoUploadRegistry } from './chatVideoUploadCompleteRegistry';

export type MainTabChatVideoUploadRegistrar = (
  listener: ChatVideoUploadCompleteListener,
) => void | (() => void);

/** One registrar per main tab — pass the matching entry into that tab's props. */
export type MainTabChatVideoUploadRegistrars = Record<
  SidebarMainTabId,
  MainTabChatVideoUploadRegistrar
>;

export function createMainTabChatVideoUploadRegistrars(
  registry: VssMainTabChatVideoUploadRegistry,
): MainTabChatVideoUploadRegistrars {
  return Object.fromEntries(
    SIDEBAR_MAIN_TAB_IDS.map((tabId) => [
      tabId,
      registry.createTabRegistrar(tabId),
    ]),
  ) as MainTabChatVideoUploadRegistrars;
}
