// SPDX-License-Identifier: MIT
import React from 'react';

/**
 * Reusable hook for a tab's floating Chat sidebar: resize (min 1/3, max 2/3 of content area)
 * and content-area measurement. Use once per tab (e.g. Search, Alerts).
 */
export function useTabChatSidebarResize(
  contentAreaRef: React.MutableRefObject<HTMLDivElement | null>,
  resizeRef: React.MutableRefObject<{ startX: number; startWidth: number } | null>,
  observerRef: React.MutableRefObject<ResizeObserver | null>,
  sidebarWidth: number,
  setContentAreaWidth: React.Dispatch<React.SetStateAction<number>>,
  setSidebarWidth: React.Dispatch<React.SetStateAction<number>>,
) {
  const handleResizeMove = React.useCallback(
    (e: MouseEvent) => {
      const ref = resizeRef.current;
      if (!ref) return;
      const contentWidth = contentAreaRef.current?.clientWidth ?? 0;
      const minW = contentWidth > 0 ? contentWidth / 3 : 320;
      const maxW = contentWidth > 0 ? (contentWidth * 2) / 3 : 600;
      const deltaX = e.clientX - ref.startX;
      const newWidth = Math.min(maxW, Math.max(minW, ref.startWidth - deltaX));
      setSidebarWidth(newWidth);
    },
    [contentAreaRef, resizeRef, setSidebarWidth],
  );

  const handleResizeEnd = React.useCallback(() => {
    resizeRef.current = null;
    window.removeEventListener('mousemove', handleResizeMove);
    window.removeEventListener('mouseup', handleResizeEnd);
  }, [handleResizeMove, resizeRef]);

  const handleResizeStart = React.useCallback(
    (e: React.MouseEvent, startWidthOverride?: number) => {
      e.preventDefault();
      const startWidth = startWidthOverride ?? sidebarWidth;
      resizeRef.current = { startX: e.clientX, startWidth };
      window.addEventListener('mousemove', handleResizeMove);
      window.addEventListener('mouseup', handleResizeEnd);
    },
    [sidebarWidth, handleResizeMove, handleResizeEnd, resizeRef],
  );

  const contentAreaCallbackRef = React.useCallback(
    (el: HTMLDivElement | null) => {
      if (observerRef.current) {
        observerRef.current.disconnect();
        observerRef.current = null;
      }
      contentAreaRef.current = el;
      if (el) {
        setContentAreaWidth(el.clientWidth);
        const ro = new ResizeObserver(() => {
          const w = contentAreaRef.current?.clientWidth ?? 0;
          setContentAreaWidth(w);
          if (w > 0) {
            setSidebarWidth((prev) => Math.min((w * 2) / 3, Math.max(w / 3, prev)));
          }
        });
        ro.observe(el);
        observerRef.current = ro;
      } else {
        setContentAreaWidth(0);
      }
    },
    [contentAreaRef, observerRef, setContentAreaWidth, setSidebarWidth],
  );

  return { handleResizeStart, contentAreaCallbackRef };
}
