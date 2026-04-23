// SPDX-License-Identifier: MIT
import {
  createSidebarMainTabChatSubscriberRegistry,
  parseSidebarMainTabId,
} from '../../utils/sidebarMainTabChatSubscribers';

describe('parseSidebarMainTabId', () => {
  it('returns known main tab ids', () => {
    expect(parseSidebarMainTabId('search')).toBe('search');
    expect(parseSidebarMainTabId('video-management')).toBe('video-management');
  });

  it('returns null for chat and unknown ids', () => {
    expect(parseSidebarMainTabId('chat')).toBeNull();
    expect(parseSidebarMainTabId('')).toBeNull();
  });
});

describe('createSidebarMainTabChatSubscriberRegistry', () => {
  it('queues answers and returns [] when no answer subscribers exist', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();

    expect(registry.emitAnswerToAllAnswerSubscribers('first')).toEqual([]);
    expect(registry.emitAnswerToAllAnswerSubscribers('second')).toEqual([]);
  });

  it('replays pending answers to a newly registered subscriber', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();
    registry.emitAnswerToAllAnswerSubscribers('queued');

    const received: string[] = [];
    registry.registerAnswerSubscriber('search', (answer) => {
      received.push(answer);
    });

    expect(received).toEqual(['queued']);
  });

  it('aggregates tab ids when subscribers return true', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();

    registry.registerAnswerSubscriber('search', () => true);
    registry.registerAnswerSubscriber('alerts', () => false as boolean | void);

    expect(registry.emitAnswerToAllAnswerSubscribers('answer')).toEqual(['search']);
  });

  it('includes multiple tabs when each has a subscriber that returns true', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();

    registry.registerAnswerSubscriber('search', () => true);
    registry.registerAnswerSubscriber('alerts', () => true);

    const updated = registry.emitAnswerToAllAnswerSubscribers('answer');
    expect(updated).toHaveLength(2);
    expect(updated).toContain('search');
    expect(updated).toContain('alerts');
  });

  it('dedupes tab id when multiple subscribers on the same tab return true', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();

    registry.registerAnswerSubscriber('search', () => true);
    registry.registerAnswerSubscriber('search', () => true);

    expect(registry.emitAnswerToAllAnswerSubscribers('answer')).toEqual(['search']);
  });

  it('returns empty array when all subscribers return void or false', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();

    registry.registerAnswerSubscriber('search', () => false);
    registry.registerAnswerSubscriber('alerts', () => undefined);

    expect(registry.emitAnswerToAllAnswerSubscribers('answer')).toEqual([]);
  });

  it('unsubscribes answer handler so it no longer receives emits', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();
    const calls: string[] = [];
    const unsub = registry.registerAnswerSubscriber('search', (a) => {
      calls.push(a);
      return true;
    });

    expect(registry.emitAnswerToAllAnswerSubscribers('one')).toEqual(['search']);
    unsub();
    expect(registry.emitAnswerToAllAnswerSubscribers('two')).toEqual([]);
  });

  it('delivers emitEventToTab only to subscribers for that tab', () => {
    const registry = createSidebarMainTabChatSubscriberRegistry();
    const searchEvents: string[] = [];
    const alertsEvents: string[] = [];

    registry.registerEventSubscriber('search', (e) => {
      searchEvents.push(e.type);
    });
    registry.registerEventSubscriber('alerts', (e) => {
      alertsEvents.push(e.type);
    });

    registry.emitEventToTab('search', { type: 'messageSubmitted' });

    expect(searchEvents).toEqual(['messageSubmitted']);
    expect(alertsEvents).toEqual([]);
  });
});
