// SPDX-License-Identifier: MIT
import React, { useEffect } from 'react';
import { IconMessageCircle, IconLoader2, IconChevronRight } from '@tabler/icons-react';
import type { AppChatSidebarApi } from '../hooks/useAppChatSidebar';

export type TabWithChatSidebarLayoutProps = {
  tabId: string;
  tabLabel: string;
  mainContent: React.ReactNode;
  sidebarEnabled: boolean;
  sidebarApi: AppChatSidebarApi;
  /** When true and collapsed, the floating Chat icon shows a highlight (e.g. new answer). */
  highlightIcon?: boolean;
  /** When true, a spinner is shown on the vertical title bar (e.g. chat query executing). */
  queryExecuting?: boolean;
  /** Called when user opens the sidebar from the floating icon; use to clear highlight. */
  onOpenSidebar?: () => void;
  renderSidebarChat: () => React.ReactNode;
  /** Ref to attach to the outer container so resize logic can measure content area. */
  contentAreaRef: (el: HTMLDivElement | null) => void;
  isActive: boolean;
};

/**
 * Single layout for any tab that supports the Chat sidebar.
 * Main content and sidebar share horizontal space (no overlay): main content is reduced to make room for the sidebar.
 */
export function TabWithChatSidebarLayout({
  tabId,
  tabLabel,
  mainContent,
  sidebarEnabled,
  sidebarApi,
  highlightIcon = false,
  queryExecuting = false,
  onOpenSidebar,
  renderSidebarChat,
  contentAreaRef,
  isActive,
}: TabWithChatSidebarLayoutProps) {
  const { collapsed, setCollapsed, effectiveWidth, handleResizeStart } =
    sidebarApi;

  const handleOpenSidebar = () => {
    onOpenSidebar?.();
    setCollapsed(false);
  };

  // Clear highlight when sidebar is opened (collapsed -> open) so it doesn't stay highlighted after user views the chat
  const prevCollapsedRef = React.useRef(collapsed);
  useEffect(() => {
    if (prevCollapsedRef.current && !collapsed) onOpenSidebar?.();
    prevCollapsedRef.current = collapsed;
  }, [collapsed, onOpenSidebar]);

  return (
    <div
      ref={contentAreaRef}
      key={tabId}
      className="absolute inset-0 flex flex-row overflow-hidden"
      style={{ display: isActive ? 'flex' : 'none' }}
    >
      {/* Main content: takes remaining width (reduced when sidebar is present) */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {mainContent}
      </div>
      {sidebarEnabled && (
        <>
          {/* Floating circular chat button at bottom-right when collapsed */}
          {collapsed && (
            <button
              data-testid="chat-sidebar-open"
              type="button"
              className={`fixed bottom-10 right-10 z-50 flex h-[72px] w-[72px] items-center justify-center rounded-full shadow-lg transition-transform hover:scale-110 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[#76b900] ${
                highlightIcon
                  ? 'bg-amber-500 text-white animate-pulse'
                  : 'bg-[#76b900] text-black'
              }`}
              onClick={handleOpenSidebar}
              aria-label={`Open Chat sidebar (${tabLabel} tab)`}
              title={highlightIcon ? `Chat – new message (${tabLabel} Tab)` : `Chat – ${tabLabel} Tab`}
            >
              {queryExecuting ? (
                <IconLoader2 className="h-9 w-9 shrink-0 animate-spin" stroke={1.5} aria-hidden />
              ) : (
                <IconMessageCircle className="h-9 w-9 shrink-0" stroke={1.5} aria-hidden />
              )}
            </button>
          )}
          {/* Minimize button: positioned at the top-left edge of the sidebar, in the outer container so sidebar can keep overflow-hidden */}
          {!collapsed && (
            <button
              data-testid="chat-sidebar-close"
              type="button"
              onClick={() => setCollapsed(true)}
              className="absolute z-50 flex h-7 w-7 items-center justify-center rounded-full bg-white dark:bg-neutral-800 shadow-md border border-neutral-300 dark:border-neutral-600 hover:bg-neutral-200 dark:hover:bg-neutral-700 transition-colors cursor-pointer"
              style={{ top: '50%', transform: 'translateY(-50%)', right: effectiveWidth - 14 }}
              aria-label="Collapse Chat sidebar"
              title="Collapse Chat sidebar"
            >
              <IconChevronRight size={14} className="text-neutral-600 dark:text-neutral-300" />
            </button>
          )}
          {/* Sidebar panel: takes fixed width; in DOM when enabled, display:none when collapsed to avoid chat re-mount */}
          <div
            className="flex flex-shrink-0 flex-row overflow-hidden border-l border-gray-300 dark:border-gray-600 bg-white dark:bg-black"
            style={{
              width: collapsed ? 0 : effectiveWidth,
              minWidth: collapsed ? 0 : undefined,
              display: collapsed ? 'none' : undefined,
            }}
          >
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize Chat sidebar"
              className="flex w-1.5 flex-shrink-0 cursor-col-resize touch-none select-none border-r border-neutral-200 dark:border-neutral-800 bg-neutral-100 dark:bg-neutral-900 hover:bg-neutral-300 dark:hover:bg-neutral-800 active:bg-neutral-400 dark:active:bg-neutral-700 focus:outline-none"
              onMouseDown={(e) => handleResizeStart(e, effectiveWidth)}
              title="Drag to resize"
            />
            <div className="relative flex-1 min-h-0 min-w-0 overflow-hidden [transform:translateZ(0)] [&>main]:!h-full [&>main]:!w-full">
              {renderSidebarChat()}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
