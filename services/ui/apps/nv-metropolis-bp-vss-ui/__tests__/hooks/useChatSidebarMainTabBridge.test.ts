// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { act, renderHook } from '@testing-library/react';

import { useChatSidebarMainTabBridge } from '../../hooks/useChatSidebarMainTabBridge';

describe('useChatSidebarMainTabBridge', () => {
  it('emits messageSubmitted to Search and active tab when active tab is not Search', () => {
    const { result } = renderHook(() =>
      useChatSidebarMainTabBridge({
        activeTab: 'alerts',
        sidebarCollapsed: true,
      }),
    );

    const searchEvents = [];
    const alertsEvents = [];

    act(() => {
      result.current.registerSearchTabSidebarChatEvents((event) => {
        searchEvents.push(event);
      });
      result.current.registerAlertsTabSidebarChatEvents((event) => {
        alertsEvents.push(event);
      });
    });

    act(() => {
      result.current.handleSidebarMessageSubmitted();
    });

    expect(searchEvents).toEqual([{ type: 'messageSubmitted' }]);
    expect(alertsEvents).toEqual([{ type: 'messageSubmitted' }]);
  });

  it('emits messageSubmitted to Search only once when Search is active', () => {
    const { result } = renderHook(() =>
      useChatSidebarMainTabBridge({
        activeTab: 'search',
        sidebarCollapsed: false,
      }),
    );

    const searchEvents = [];

    act(() => {
      result.current.registerSearchTabSidebarChatEvents((event) => {
        searchEvents.push(event);
      });
    });

    act(() => {
      result.current.handleSidebarMessageSubmitted();
    });

    expect(searchEvents).toEqual([{ type: 'messageSubmitted' }]);
    expect(result.current.searchTabChatSidebarBusy).toBe(true);
  });

  it.each(['alerts', 'search', 'dashboard', 'map', 'video-management'])(
    'notifies Alerts once after a sidebar answer from %s',
    (activeTab) => {
      const { result } = renderHook(() =>
        useChatSidebarMainTabBridge({ activeTab, sidebarCollapsed: true }),
      );
      const handler = jest.fn();
      result.current.registerAlertsTabSidebarChatEvents(handler);
      act(() => result.current.handleSidebarMessageSubmitted());
      handler.mockClear();
      act(() => result.current.handleSidebarAnswerComplete());
      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler).toHaveBeenCalledWith({ type: 'answerComplete' });
    },
  );

  it('notifies Alerts for full-page Chat answers without artifacts', () => {
    const { result } = renderHook(() =>
      useChatSidebarMainTabBridge({ activeTab: 'chat', sidebarCollapsed: true }),
    );
    const handler = jest.fn();
    result.current.registerAlertsTabSidebarChatEvents(handler);
    act(() => {
      result.current.handleMainChatAnswerCompleteWithContent('Alert rule created.');
    });
    expect(handler).toHaveBeenCalledTimes(1);
    expect(handler).toHaveBeenCalledWith({ type: 'answerComplete' });
    expect(result.current.chatSidebarQueryExecuting).toBe(false);
    expect(result.current.chatSidebarHighlight).toBe(false);
  });

  it('highlightSidebarWhenCollapsed sets highlight only when sidebar is collapsed', () => {
    const { result, rerender } = renderHook(
      ({ sidebarCollapsed }) =>
        useChatSidebarMainTabBridge({
          activeTab: 'search',
          sidebarCollapsed,
        }),
      { initialProps: { sidebarCollapsed: true } },
    );

    act(() => {
      result.current.highlightSidebarWhenCollapsed();
    });
    expect(result.current.chatSidebarHighlight).toBe(true);

    act(() => {
      result.current.clearChatSidebarHighlight();
    });
    expect(result.current.chatSidebarHighlight).toBe(false);

    rerender({ sidebarCollapsed: false });
    act(() => {
      result.current.highlightSidebarWhenCollapsed();
    });
    expect(result.current.chatSidebarHighlight).toBe(false);
  });

  it('fans full-page Chat answers out to structured-result tabs', () => {
    const { result } = renderHook(() =>
      useChatSidebarMainTabBridge({
        activeTab: 'chat',
        sidebarCollapsed: true,
      }),
    );
    const received: string[] = [];

    act(() => {
      result.current.registerSearchTabChatAnswer((answer) => {
        received.push(`search:${answer}`);
        return true;
      });
      result.current.registerAlertsTabChatAnswer((answer) => {
        received.push(`alerts:${answer}`);
        return true;
      });
    });

    let callerInfo: string | void;
    act(() => {
      callerInfo = result.current.handleMainChatAnswerCompleteWithContent('artifact');
    });

    expect(received).toEqual(['search:artifact', 'alerts:artifact']);
    expect(callerInfo).toContain('#vss-mt-search');
    expect(callerInfo).toContain('#vss-mt-alerts');
  });
});
