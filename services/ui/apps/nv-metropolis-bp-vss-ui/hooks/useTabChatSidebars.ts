// SPDX-License-Identifier: MIT
import React from 'react';
import {
  getTabChatSidebarOpenDefault,
  getTabChatSidebarOpenFromSession,
  setTabChatSidebarOpenInSession,
} from '../utils/tabChatSidebarConfig';

export type TabChatSidebarState = {
  collapsed: boolean;
  width: number;
};

export type TabChatSidebarApi = {
  collapsed: boolean;
  setCollapsed: (value: boolean) => void;
  effectiveWidth: number;
  handleResizeStart: (e: React.MouseEvent, startWidthOverride?: number) => void;
  contentAreaCallbackRef: (el: HTMLDivElement | null) => void;
};

/**
 * Hook that holds sidebar state and resize logic for multiple tabs.
 * Returns getTabChatSidebar(tabId) so any non-Chat tab can render the same layout.
 */
export function useTabChatSidebars(
  tabIds: readonly string[],
): (tabId: string) => TabChatSidebarApi {
  const [sidebarState, setSidebarState] = React.useState<
    Record<string, TabChatSidebarState>
  >(() => {
    const o: Record<string, TabChatSidebarState> = {};
    tabIds.forEach((id) => {
      // Same-tab refresh: use last user-selected state from session storage
      const sessionOpen = getTabChatSidebarOpenFromSession(id);
      const open =
        sessionOpen !== null ? sessionOpen : getTabChatSidebarOpenDefault(id);
      o[id] = {
        collapsed: !open,
        width: 380,
      };
    });
    return o;
  });

  const [contentAreaWidths, setContentAreaWidths] = React.useState<
    Record<string, number>
  >(() => {
    const o: Record<string, number> = {};
    tabIds.forEach((id) => {
      o[id] = 0;
    });
    return o;
  });

  const contentAreaRefs = React.useRef<Record<string, HTMLDivElement | null>>(
    {},
  );
  const observerRefs = React.useRef<Record<string, ResizeObserver | null>>({});
  const resizeRef = React.useRef<{
    tabId: string;
    startX: number;
    startWidth: number;
  } | null>(null);

  // Refs to latest setters so stable ref callbacks can update state without changing callback identity
  const setContentAreaWidthsRef = React.useRef(setContentAreaWidths);
  const setSidebarStateRef = React.useRef(setSidebarState);
  setContentAreaWidthsRef.current = setContentAreaWidths;
  setSidebarStateRef.current = setSidebarState;

  const handleResizeMove = React.useCallback((e: MouseEvent) => {
    const ref = resizeRef.current;
    if (!ref) return;
    const contentWidth =
      contentAreaRefs.current[ref.tabId]?.clientWidth ?? 0;
    const minW = contentWidth > 0 ? contentWidth / 3 : 320;
    const maxW = contentWidth > 0 ? (contentWidth * 2) / 3 : 600;
    const deltaX = e.clientX - ref.startX;
    const newWidth = Math.min(maxW, Math.max(minW, ref.startWidth - deltaX));
    setSidebarState((prev) => ({
      ...prev,
      [ref.tabId]: { ...prev[ref.tabId], width: newWidth },
    }));
  }, []);

  const handleResizeEnd = React.useCallback(() => {
    resizeRef.current = null;
    window.removeEventListener('mousemove', handleResizeMove);
    window.removeEventListener('mouseup', handleResizeEnd);
  }, [handleResizeMove]);

  const handleResizeStart = React.useCallback(
    (tabId: string) =>
      (e: React.MouseEvent, startWidthOverride?: number) => {
        e.preventDefault();
        const state = sidebarState[tabId];
        const startWidth = startWidthOverride ?? state?.width ?? 380;
        resizeRef.current = { tabId, startX: e.clientX, startWidth };
        window.addEventListener('mousemove', handleResizeMove);
        window.addEventListener('mouseup', handleResizeEnd);
      },
    [sidebarState, handleResizeMove, handleResizeEnd],
  );

  // Stable ref callbacks per tabId so React doesn't re-run ref(el) after state updates (avoids error #185 / infinite loop)
  const contentAreaCallbackRefsRef = React.useRef<
    Record<string, (el: HTMLDivElement | null) => void>
  >({});

  const getContentAreaRefCallback = React.useCallback((tabId: string) => {
    if (!contentAreaCallbackRefsRef.current[tabId]) {
      contentAreaCallbackRefsRef.current[tabId] = (
        el: HTMLDivElement | null,
      ) => {
        const obs = observerRefs.current[tabId];
        if (obs) {
          obs.disconnect();
          observerRefs.current[tabId] = null;
        }
        contentAreaRefs.current[tabId] = el;
        if (el) {
          setContentAreaWidthsRef.current((prev) => ({
            ...prev,
            [tabId]: el.clientWidth,
          }));
          const ro = new ResizeObserver(() => {
            const w = contentAreaRefs.current[tabId]?.clientWidth ?? 0;
            setContentAreaWidthsRef.current((prev) => ({ ...prev, [tabId]: w }));
            if (w > 0) {
              setSidebarStateRef.current((prev) => {
                const cur = prev[tabId];
                if (!cur) return prev;
                const clamped = Math.min(
                  (w * 2) / 3,
                  Math.max(w / 3, cur.width),
                );
                return { ...prev, [tabId]: { ...cur, width: clamped } };
              });
            }
          });
          ro.observe(el);
          observerRefs.current[tabId] = ro;
        } else {
          setContentAreaWidthsRef.current((prev) => ({ ...prev, [tabId]: 0 }));
        }
      };
    }
    return contentAreaCallbackRefsRef.current[tabId];
  }, []);

  return React.useCallback(
    (tabId: string): TabChatSidebarApi => {
      const state = sidebarState[tabId] ?? {
        collapsed: true,
        width: 380,
      };
      const contentW = contentAreaWidths[tabId] ?? 0;
      const minW = contentW > 0 ? contentW / 3 : 320;
      const maxW = contentW > 0 ? (contentW * 2) / 3 : 600;
      const effectiveWidth =
        contentW > 0
          ? Math.min(maxW, Math.max(minW, state.width))
          : state.width;

      return {
        collapsed: state.collapsed,
        setCollapsed: (value: boolean) => {
          setTabChatSidebarOpenInSession(tabId, !value);
          setSidebarState((prev) => ({
            ...prev,
            [tabId]: { ...(prev[tabId] ?? { width: 380 }), collapsed: value },
          }));
        },
        effectiveWidth,
        handleResizeStart: (e, w?) => handleResizeStart(tabId)(e, w),
        contentAreaCallbackRef: getContentAreaRefCallback(tabId),
      };
    },
    [
      sidebarState,
      contentAreaWidths,
      handleResizeStart,
      getContentAreaRefCallback,
    ],
  );
}
