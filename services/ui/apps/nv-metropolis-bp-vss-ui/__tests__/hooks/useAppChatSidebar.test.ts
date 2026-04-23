// SPDX-License-Identifier: MIT
import { act, renderHook } from '@testing-library/react';

import { useAppChatSidebar } from '../../hooks/useAppChatSidebar';
import * as sidebarConfig from '../../utils/tabChatSidebarConfig';

jest.mock('../../utils/tabChatSidebarConfig', () => ({
  getChatSidebarOpenDefault: jest.fn(),
  getChatSidebarOpenFromSession: jest.fn(),
  setChatSidebarOpenInSession: jest.fn(),
}));

const getChatSidebarOpenDefaultMock = jest.mocked(sidebarConfig.getChatSidebarOpenDefault);
const getChatSidebarOpenFromSessionMock = jest.mocked(sidebarConfig.getChatSidebarOpenFromSession);
const setChatSidebarOpenInSessionMock = jest.mocked(sidebarConfig.setChatSidebarOpenInSession);

describe('useAppChatSidebar', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('initializes from env default before applying session value', () => {
    getChatSidebarOpenDefaultMock.mockReturnValue(true);
    getChatSidebarOpenFromSessionMock.mockReturnValue(false);

    const { result } = renderHook(() => useAppChatSidebar());

    expect(getChatSidebarOpenDefaultMock).toHaveBeenCalledTimes(1);
    expect(getChatSidebarOpenFromSessionMock).toHaveBeenCalledTimes(1);
    expect(getChatSidebarOpenDefaultMock.mock.invocationCallOrder[0]).toBeLessThan(
      getChatSidebarOpenFromSessionMock.mock.invocationCallOrder[0],
    );
    expect(result.current.collapsed).toBe(true);
  });

  it('uses env default when session state is not available', () => {
    getChatSidebarOpenDefaultMock.mockReturnValue(false);
    getChatSidebarOpenFromSessionMock.mockReturnValue(null);

    const { result } = renderHook(() => useAppChatSidebar());

    expect(result.current.collapsed).toBe(true);
  });

  it('persists open state when collapsed changes', () => {
    getChatSidebarOpenDefaultMock.mockReturnValue(true);
    getChatSidebarOpenFromSessionMock.mockReturnValue(null);

    const { result } = renderHook(() => useAppChatSidebar());

    act(() => {
      result.current.setCollapsed(true);
    });

    expect(setChatSidebarOpenInSessionMock).toHaveBeenCalledWith(false);
    expect(result.current.collapsed).toBe(true);
  });
});
