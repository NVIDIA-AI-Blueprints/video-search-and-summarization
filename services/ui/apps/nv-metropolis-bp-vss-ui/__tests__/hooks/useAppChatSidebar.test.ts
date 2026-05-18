// SPDX-License-Identifier: MIT
import React from 'react';
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

/** jsdom often lacks PointerEvent; the hook only reads clientX / pointerId on native listeners. */
function dispatchPointerCompat(
  target: HTMLElement,
  type: 'pointermove' | 'pointerup',
  clientX: number,
  pointerId: number,
) {
  const ev = new MouseEvent(type, { bubbles: true, clientX });
  Object.defineProperty(ev, 'pointerId', { value: pointerId, enumerable: true });
  target.dispatchEvent(ev);
}

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

  it('resizes sidebar width using pointer capture (pointermove over iframe-safe path)', () => {
    getChatSidebarOpenDefaultMock.mockReturnValue(true);
    getChatSidebarOpenFromSessionMock.mockReturnValue(null);

    const { result } = renderHook(() => useAppChatSidebar());

    const container = document.createElement('div');
    Object.defineProperty(container, 'clientWidth', { configurable: true, value: 900 });

    act(() => {
      result.current.contentAreaCallbackRef(container);
    });

    const separator = document.createElement('div');
    separator.setPointerCapture = jest.fn();
    separator.releasePointerCapture = jest.fn();
    separator.hasPointerCapture = jest.fn().mockReturnValue(true);

    const down = {
      pointerType: 'mouse',
      button: 0,
      clientX: 1000,
      pointerId: 1,
      preventDefault: jest.fn(),
      currentTarget: separator,
    } as unknown as React.PointerEvent<HTMLElement>;

    act(() => {
      result.current.handleResizeStart(down, 400);
    });

    expect(separator.setPointerCapture).toHaveBeenCalledWith(1);

    act(() => {
      dispatchPointerCompat(separator, 'pointermove', 900, 1);
    });

    expect(result.current.effectiveWidth).toBe(500);

    act(() => {
      dispatchPointerCompat(separator, 'pointerup', 900, 1);
    });

    expect(separator.releasePointerCapture).toHaveBeenCalledWith(1);
  });

  it('does not start resize drag on non-primary mouse button', () => {
    getChatSidebarOpenDefaultMock.mockReturnValue(true);
    getChatSidebarOpenFromSessionMock.mockReturnValue(null);

    const { result } = renderHook(() => useAppChatSidebar());

    const separator = document.createElement('div');
    separator.setPointerCapture = jest.fn();

    const down = {
      pointerType: 'mouse',
      button: 1,
      clientX: 1000,
      pointerId: 1,
      preventDefault: jest.fn(),
      currentTarget: separator,
    } as unknown as React.PointerEvent<HTMLElement>;

    act(() => {
      result.current.handleResizeStart(down, 400);
    });

    expect(separator.setPointerCapture).not.toHaveBeenCalled();
  });
});
