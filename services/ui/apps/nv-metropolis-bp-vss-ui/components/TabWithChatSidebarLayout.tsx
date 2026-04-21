// SPDX-License-Identifier: MIT
import React, { useEffect } from 'react';
import { IconMessageCircle, IconChevronLeft, IconChevronRight, IconX } from '@tabler/icons-react';
import type { TabChatSidebarApi } from '../hooks/useTabChatSidebars';

export type TabWithChatSidebarLayoutProps = {
  tabId: string;
  tabLabel: string;
  mainContent: React.ReactNode;
  sidebarEnabled: boolean;
  sidebarApi: TabChatSidebarApi;
  /** When true and collapsed, the floating Chat icon shows a highlight (e.g. new answer). */
  highlightIcon?: boolean;
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
          {/* When collapsed: same vertical "Chat" title as open state; full attention signalling (highlight + dismiss) */}
          {collapsed && (
            <div
              className={`relative flex w-10 flex-shrink-0 flex-col items-center justify-center gap-2 border-l border-gray-200 dark:border-gray-600 bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400 ${
                highlightIcon
                  ? 'ring-2 ring-amber-400/70 dark:ring-amber-300/60 ring-offset-1 ring-offset-gray-100 dark:ring-offset-gray-900'
                  : ''
              } ${highlightIcon ? 'animate-pulse' : ''}`}
              style={{ opacity: highlightIcon ? 1 : 0.9 }}
            >
              <button
                type="button"
                className={`flex w-full flex-1 min-h-0 flex-col items-center justify-center gap-2 hover:bg-gray-200 dark:hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-gray-400 ${
                  highlightIcon
                    ? 'border-amber-500 dark:border-amber-400 rounded border-2'
                    : ''
                }`}
                onClick={handleOpenSidebar}
                aria-label={`Open Chat sidebar (${tabLabel} tab)`}
                title={highlightIcon ? `Chat – new message (${tabLabel} Tab)` : `Chat – ${tabLabel} Tab`}
              >
                <IconChevronLeft className="h-5 w-5 shrink-0" aria-hidden />
                <IconMessageCircle className="h-6 w-6 shrink-0" aria-hidden />
                <span
                  className="text-sm font-semibold tracking-wide text-gray-700 dark:text-gray-300"
                  style={{
                    writingMode: 'vertical-rl',
                    textOrientation: 'mixed',
                    letterSpacing: '0.15em',
                  }}
                >
                  Chat
                </span>
              </button>
              {highlightIcon && (
                <button
                  type="button"
                  className="absolute left-0 top-1/2 z-10 flex h-5 w-5 items-center justify-center rounded-full border-2 border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-700 dark:hover:text-gray-200 focus:outline-none focus:ring-2 focus:ring-amber-400 shadow-lg"
                  style={{ transform: 'translate(-50%, -50%)' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    onOpenSidebar?.();
                  }}
                  aria-label="Mark as seen"
                  title="Mark as seen"
                >
                  <IconX className="h-3.5 w-3.5 shrink-0" aria-hidden />
                </button>
              )}
            </div>
          )}
          {/* Sidebar panel: takes fixed width; in DOM when enabled, display:none when collapsed to avoid chat re-mount */}
          <div
            className="flex flex-shrink-0 flex-row overflow-hidden border-l border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800"
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
              className="flex w-1.5 flex-shrink-0 cursor-col-resize touch-none border-r border-gray-200 dark:border-gray-600 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 active:bg-gray-300 dark:active:bg-gray-500"
              onMouseDown={(e) => handleResizeStart(e, effectiveWidth)}
              title="Drag to resize"
            />
            <div className="relative flex-1 min-h-0 min-w-0 overflow-hidden [transform:translateZ(0)] [&>main]:!h-full [&>main]:!w-full">
              {renderSidebarChat()}
            </div>
            <button
              type="button"
              className="flex w-10 flex-shrink-0 flex-col items-center justify-center gap-2 border-l border-gray-200 dark:border-gray-600 bg-gray-100 dark:bg-gray-900 hover:bg-gray-200 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-gray-400"
              onClick={() => setCollapsed(true)}
              aria-label={`Close Chat sidebar (${tabLabel} tab)`}
              title={`Chat – ${tabLabel} Tab (Click to minimize)`}
            >
              <IconChevronRight className="w-5 h-5 shrink-0" aria-hidden />
              <IconMessageCircle className="w-5 h-5 shrink-0" aria-hidden />
              <span
                className="text-sm font-semibold tracking-wide text-gray-700 dark:text-gray-300"
                style={{
                  writingMode: 'vertical-rl',
                  textOrientation: 'mixed',
                  letterSpacing: '0.15em',
                }}
              >
                Chat
              </span>
            </button>
          </div>
        </>
      )}
    </div>
  );
}
