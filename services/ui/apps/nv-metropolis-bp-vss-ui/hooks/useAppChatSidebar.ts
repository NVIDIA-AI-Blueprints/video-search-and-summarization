// SPDX-License-Identifier: MIT
import React from 'react';
import {
  getChatSidebarOpenDefault,
  getChatSidebarOpenFromSession,
  setChatSidebarOpenInSession,
} from '../utils/tabChatSidebarConfig';

export type AppChatSidebarApi = {
  collapsed: boolean;
  setCollapsed: (value: boolean) => void;
  effectiveWidth: number;
  handleResizeStart: (e: React.MouseEvent, startWidthOverride?: number) => void;
  contentAreaCallbackRef: (el: HTMLDivElement | null) => void;
};

/** @deprecated Use AppChatSidebarApi */
export type TabChatSidebarApi = AppChatSidebarApi;

/**
 * Single app-wide Chat sidebar: open/collapsed state, width, resize, and content-area measurement.
 */
export function useAppChatSidebar(): AppChatSidebarApi {
  // Initialize with env default only so SSR and first client render agree.
  // Persisted sessionStorage is applied in the effect below (two-phase init) to
  // avoid a hydration mismatch that can leave the sidebar stuck after refresh.
  const [sidebarState, setSidebarState] = React.useState(() => {
    const open = getChatSidebarOpenDefault();
    return {
      collapsed: !open,
      width: 380,
    };
  });

  React.useEffect(() => {
    setSidebarState((prev) => {
      const sessionOpen = getChatSidebarOpenFromSession();
      if (sessionOpen === null) return prev;
      const desiredCollapsed = !sessionOpen;
      if (prev.collapsed === desiredCollapsed) return prev;
      return { ...prev, collapsed: desiredCollapsed };
    });
  }, []);

  const [contentAreaWidth, setContentAreaWidth] = React.useState(0);

  const contentAreaRef = React.useRef<HTMLDivElement | null>(null);
  const observerRef = React.useRef<ResizeObserver | null>(null);
  const resizeRef = React.useRef<{
    startX: number;
    startWidth: number;
  } | null>(null);

  const setContentAreaWidthRef = React.useRef(setContentAreaWidth);
  const setSidebarStateRef = React.useRef(setSidebarState);
  setContentAreaWidthRef.current = setContentAreaWidth;
  setSidebarStateRef.current = setSidebarState;

  const handleResizeMove = React.useCallback((e: MouseEvent) => {
    const ref = resizeRef.current;
    if (!ref) return;
    const contentWidth = contentAreaRef.current?.clientWidth ?? 0;
    const minW = contentWidth > 0 ? contentWidth / 3 : 320;
    const maxW = contentWidth > 0 ? (contentWidth * 2) / 3 : 600;
    const deltaX = e.clientX - ref.startX;
    const newWidth = Math.min(maxW, Math.max(minW, ref.startWidth - deltaX));
    setSidebarState((prev) => ({
      ...prev,
      width: newWidth,
    }));
  }, []);

  const handleResizeEnd = React.useCallback(() => {
    resizeRef.current = null;
    window.removeEventListener('mousemove', handleResizeMove);
    window.removeEventListener('mouseup', handleResizeEnd);
  }, [handleResizeMove]);

  const handleResizeStart = React.useCallback(
    (e: React.MouseEvent, startWidthOverride?: number) => {
      e.preventDefault();
      const startWidth = startWidthOverride ?? sidebarState.width ?? 380;
      resizeRef.current = { startX: e.clientX, startWidth };
      window.addEventListener('mousemove', handleResizeMove);
      window.addEventListener('mouseup', handleResizeEnd);
    },
    [sidebarState.width, handleResizeMove, handleResizeEnd],
  );

  const contentAreaCallbackRef = React.useCallback(
    (el: HTMLDivElement | null) => {
      const obs = observerRef.current;
      if (obs) {
        obs.disconnect();
        observerRef.current = null;
      }
      contentAreaRef.current = el;
      if (el) {
        setContentAreaWidthRef.current(el.clientWidth);
        const ro = new ResizeObserver(() => {
          const w = contentAreaRef.current?.clientWidth ?? 0;
          setContentAreaWidthRef.current(w);
          if (w > 0) {
            setSidebarStateRef.current((prev) => {
              const clamped = Math.min(
                (w * 2) / 3,
                Math.max(w / 3, prev.width),
              );
              return { ...prev, width: clamped };
            });
          }
        });
        ro.observe(el);
        observerRef.current = ro;
      } else {
        setContentAreaWidthRef.current(0);
      }
    },
    [],
  );

  const minW = contentAreaWidth > 0 ? contentAreaWidth / 3 : 320;
  const maxW = contentAreaWidth > 0 ? (contentAreaWidth * 2) / 3 : 600;
  const effectiveWidth =
    contentAreaWidth > 0
      ? Math.min(maxW, Math.max(minW, sidebarState.width))
      : sidebarState.width;

  return React.useMemo(
    () => ({
      collapsed: sidebarState.collapsed,
      setCollapsed: (value: boolean) => {
        setChatSidebarOpenInSession(!value);
        setSidebarState((prev) => ({
          ...prev,
          collapsed: value,
        }));
      },
      effectiveWidth,
      handleResizeStart,
      contentAreaCallbackRef,
    }),
    [
      sidebarState.collapsed,
      sidebarState.width,
      effectiveWidth,
      handleResizeStart,
      contentAreaCallbackRef,
    ],
  );
}
