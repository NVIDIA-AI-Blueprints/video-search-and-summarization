// SPDX-License-Identifier: MIT
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
});
