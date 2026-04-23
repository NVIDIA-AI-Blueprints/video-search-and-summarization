// SPDX-License-Identifier: MIT
/**
 * App-wide subscriber registry for the floating Chat sidebar when a main tab (not Chat) is active.
 * Chat tab is excluded: it uses the full-page chat, not this sidebar.
 */

/** Main app tabs that can register for sidebar broadcasts (excludes Chat). */
export type SidebarMainTabId = 'search' | 'alerts' | 'dashboard' | 'map' | 'video-management';

/** Lifecycle events other than receiving the full assistant answer text. */
export type SidebarMainTabChatEvent =
  | { type: 'messageSubmitted' }
  | { type: 'answerComplete' };

export function parseSidebarMainTabId(tab: string): SidebarMainTabId | null {
  switch (tab) {
    case 'search':
    case 'alerts':
    case 'dashboard':
    case 'map':
    case 'video-management':
      return tab;
    default:
      return null;
  }
}

type AnswerSubscriber = (answer: string) => boolean | void;
type EventSubscriber = (event: SidebarMainTabChatEvent) => void;

function emptyTabSets<T>(): Record<SidebarMainTabId, Set<T>> {
  return {
    search: new Set(),
    alerts: new Set(),
    dashboard: new Set(),
    map: new Set(),
    'video-management': new Set(),
  };
}

export function createSidebarMainTabChatSubscriberRegistry() {
  const answerSubscribersByTab = emptyTabSets<AnswerSubscriber>();
  const eventSubscribersByTab = emptyTabSets<EventSubscriber>();
  const pendingAnswers: string[] = [];

  function registerAnswerSubscriber(tabId: SidebarMainTabId, subscriber: AnswerSubscriber) {
    const subscribers = answerSubscribersByTab[tabId];
    subscribers.add(subscriber);
    if (pendingAnswers.length) {
      pendingAnswers.forEach((answer) => subscriber(answer));
    }
    return () => {
      subscribers.delete(subscriber);
    };
  }

  function registerEventSubscriber(tabId: SidebarMainTabId, subscriber: EventSubscriber) {
    const subscribers = eventSubscribersByTab[tabId];
    subscribers.add(subscriber);
    return () => {
      subscribers.delete(subscriber);
    };
  }

  function emitAnswerToAllAnswerSubscribers(answer: string): SidebarMainTabId[] {
    const updatedTabs = new Set<SidebarMainTabId>();
    const tabEntries = Object.entries(answerSubscribersByTab) as Array<
      [SidebarMainTabId, Set<AnswerSubscriber>]
    >;
    const hasSubscribers = tabEntries.some(([, subscribers]) => subscribers.size > 0);
    if (!hasSubscribers) {
      pendingAnswers.push(answer);
      return [];
    } else {
      tabEntries.forEach(([tabId, subscribers]) => {
        subscribers.forEach((subscriber) => {
          if (subscriber(answer) === true) {
            updatedTabs.add(tabId);
          }
        });
      });
      pendingAnswers.length = 0;
      return Array.from(updatedTabs);
    }
  }

  function emitEventToTab(tabId: SidebarMainTabId, event: SidebarMainTabChatEvent) {
    eventSubscribersByTab[tabId].forEach((fn) => fn(event));
  }

  return {
    registerAnswerSubscriber,
    registerEventSubscriber,
    emitAnswerToAllAnswerSubscribers,
    emitEventToTab,
  };
}

export type SidebarMainTabChatSubscriberRegistry = ReturnType<typeof createSidebarMainTabChatSubscriberRegistry>;
