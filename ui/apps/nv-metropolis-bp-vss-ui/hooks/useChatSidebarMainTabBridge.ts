// SPDX-License-Identifier: MIT
import React from 'react';
import type { CallerInfo } from '@nemo-agent-toolkit/ui';
import {
  createSidebarMainTabChatSubscriberRegistry,
  parseSidebarMainTabId,
  type SidebarMainTabId,
  type SidebarMainTabChatEvent,
} from '../utils/sidebarMainTabChatSubscribers';
import { hrefForCallerInfoMainTab } from '../utils/callerInfoMainTabHash';

type UseChatSidebarMainTabBridgeParams = {
  activeTab: string;
  sidebarCollapsed: boolean;
};

/**
 * Bridges floating sidebar chat lifecycle to non-chat tabs via app-wide subscriptions.
 */
export function useChatSidebarMainTabBridge({
  activeTab,
  sidebarCollapsed,
}: UseChatSidebarMainTabBridgeParams) {
  const tabLabelById: Record<SidebarMainTabId, string> = {
    search: 'Search',
    alerts: 'Alerts',
    dashboard: 'Dashboard',
    map: 'Map',
    'video-management': 'Video Management',
  };

  const activeTabRef = React.useRef(activeTab);
  activeTabRef.current = activeTab;

  // Tracks tab context for in-flight sidebar turns.
  const pendingSidebarContextTabRef = React.useRef<'search' | 'alerts' | null>(null);
  const sidebarAnswerTargetTabRef = React.useRef<string | null>(null);
  const sidebarSubmitMessageRef = React.useRef<(message: string) => void>();

  const [chatSidebarHighlight, setChatSidebarHighlight] = React.useState(false);
  const [chatSidebarQueryExecuting, setChatSidebarQueryExecuting] = React.useState(false);
  const [searchTabChatSidebarBusy, setSearchTabChatSidebarBusy] = React.useState(false);

  const sidebarMainTabChatRegistry = React.useMemo(
    () => createSidebarMainTabChatSubscriberRegistry(),
    [],
  );

  // Stable registration callbacks per tab (answer text + sidebar lifecycle events).
  const registerSearchTabChatAnswer = React.useCallback(
    (handler: (answer: string) => boolean | void) =>
      sidebarMainTabChatRegistry.registerAnswerSubscriber('search', handler),
    [sidebarMainTabChatRegistry],
  );

  const registerSearchTabSidebarChatEvents = React.useCallback(
    (handler: (event: SidebarMainTabChatEvent) => void) =>
      sidebarMainTabChatRegistry.registerEventSubscriber('search', handler),
    [sidebarMainTabChatRegistry],
  );

  const registerAlertsTabChatAnswer = React.useCallback(
    (handler: (answer: string) => boolean | void) =>
      sidebarMainTabChatRegistry.registerAnswerSubscriber('alerts', handler),
    [sidebarMainTabChatRegistry],
  );

  const registerAlertsTabSidebarChatEvents = React.useCallback(
    (handler: (event: SidebarMainTabChatEvent) => void) =>
      sidebarMainTabChatRegistry.registerEventSubscriber('alerts', handler),
    [sidebarMainTabChatRegistry],
  );

  const registerDashboardTabChatAnswer = React.useCallback(
    (_handler: (answer: string) => boolean | void) =>
      sidebarMainTabChatRegistry.registerAnswerSubscriber('dashboard', () => false),
    [sidebarMainTabChatRegistry],
  );

  const registerDashboardTabSidebarChatEvents = React.useCallback(
    (handler: (event: SidebarMainTabChatEvent) => void) =>
      sidebarMainTabChatRegistry.registerEventSubscriber('dashboard', handler),
    [sidebarMainTabChatRegistry],
  );

  const registerMapTabChatAnswer = React.useCallback(
    (_handler: (answer: string) => boolean | void) =>
      sidebarMainTabChatRegistry.registerAnswerSubscriber('map', () => false),
    [sidebarMainTabChatRegistry],
  );

  const registerMapTabSidebarChatEvents = React.useCallback(
    (handler: (event: SidebarMainTabChatEvent) => void) =>
      sidebarMainTabChatRegistry.registerEventSubscriber('map', handler),
    [sidebarMainTabChatRegistry],
  );

  const registerVideoManagementTabChatAnswer = React.useCallback(
    (_handler: (answer: string) => boolean | void) =>
      sidebarMainTabChatRegistry.registerAnswerSubscriber('video-management', () => false),
    [sidebarMainTabChatRegistry],
  );

  const registerVideoManagementTabSidebarChatEvents = React.useCallback(
    (handler: (event: SidebarMainTabChatEvent) => void) =>
      sidebarMainTabChatRegistry.registerEventSubscriber('video-management', handler),
    [sidebarMainTabChatRegistry],
  );

  // Chat calls onAnswerComplete before onAnswerCompleteWithContent.
  const handleSidebarAnswerComplete = React.useCallback(() => {
    const tabId = parseSidebarMainTabId(sidebarAnswerTargetTabRef.current ?? activeTabRef.current);
    if (tabId) {
      sidebarMainTabChatRegistry.emitEventToTab(tabId, { type: 'answerComplete' });
    }
    if (pendingSidebarContextTabRef.current === 'search') {
      setSearchTabChatSidebarBusy(false);
    }
    pendingSidebarContextTabRef.current = null;
    setChatSidebarQueryExecuting(false);
    setChatSidebarHighlight(sidebarCollapsed);
  }, [sidebarCollapsed, sidebarMainTabChatRegistry]);

  const handleSidebarAnswerCompleteWithContent = React.useCallback(
    (answer: string): CallerInfo | void => {
      const updatedTabIds = sidebarMainTabChatRegistry.emitAnswerToAllAnswerSubscribers(answer);
      sidebarAnswerTargetTabRef.current = null;
      if (!updatedTabIds.length) return;
      const listItemsHtml = updatedTabIds
        .map((tabId) => {
          const label = `${tabLabelById[tabId]} Tab`;
          const href = hrefForCallerInfoMainTab(tabId);
          return `<li><a class="vss-caller-info-tab-link" href="${href}">${label}</a></li>`;
        })
        .join('');
      return `<div class="vss-caller-info-panel"><span class="vss-caller-info-icon" aria-hidden="true"></span><div class="caller-info-body"><div class="font-semibold caller-info-title">See all results in:</div><ul>${listItemsHtml}</ul></div></div>`;
    },
    [sidebarMainTabChatRegistry],
  );

  const handleSidebarSubmitMessageReady = React.useCallback(
    (submitMessage: (message: string) => void) => {
      sidebarSubmitMessageRef.current = submitMessage;
    },
    [],
  );

  const handleSidebarMessageSubmitted = React.useCallback(() => {
    const currentTab = activeTabRef.current;
    sidebarAnswerTargetTabRef.current = currentTab;
    // Search must clear stale agent results on every sidebar send, even when another main tab is focused.
    sidebarMainTabChatRegistry.emitEventToTab('search', { type: 'messageSubmitted' });
    const tabId = parseSidebarMainTabId(currentTab);
    if (tabId && tabId !== 'search') {
      sidebarMainTabChatRegistry.emitEventToTab(tabId, { type: 'messageSubmitted' });
    }
    if (currentTab === 'search') {
      setSearchTabChatSidebarBusy(true);
      pendingSidebarContextTabRef.current = 'search';
    } else if (currentTab === 'alerts') {
      pendingSidebarContextTabRef.current = 'alerts';
    } else {
      pendingSidebarContextTabRef.current = null;
    }
    setChatSidebarQueryExecuting(true);
    setChatSidebarHighlight(sidebarCollapsed);
  }, [sidebarCollapsed, sidebarMainTabChatRegistry]);

  const submitSidebarMessage = React.useCallback((message: string) => {
    sidebarSubmitMessageRef.current?.(message);
  }, []);

  const clearChatSidebarHighlight = React.useCallback(() => {
    setChatSidebarHighlight(false);
  }, []);

  return {
    chatSidebarHighlight,
    chatSidebarQueryExecuting,
    searchTabChatSidebarBusy,
    clearChatSidebarHighlight,
    submitSidebarMessage,
    registerSearchTabChatAnswer,
    registerSearchTabSidebarChatEvents,
    registerAlertsTabChatAnswer,
    registerAlertsTabSidebarChatEvents,
    registerDashboardTabChatAnswer,
    registerDashboardTabSidebarChatEvents,
    registerMapTabChatAnswer,
    registerMapTabSidebarChatEvents,
    registerVideoManagementTabChatAnswer,
    registerVideoManagementTabSidebarChatEvents,
    handleSidebarAnswerComplete,
    handleSidebarAnswerCompleteWithContent,
    handleSidebarSubmitMessageReady,
    handleSidebarMessageSubmitted,
  };
}
