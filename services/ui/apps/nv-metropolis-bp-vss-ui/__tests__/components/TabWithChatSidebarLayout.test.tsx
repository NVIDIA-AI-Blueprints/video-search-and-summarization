// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';

import { TabWithChatSidebarLayout } from '../../components/TabWithChatSidebarLayout';
import type { AppChatSidebarApi } from '../../hooks/useAppChatSidebar';

function renderLayout({
  collapsed = true,
  highlightIcon = false,
  setCollapsed = jest.fn(),
  onOpenSidebar = jest.fn(),
}: {
  collapsed?: boolean;
  highlightIcon?: boolean;
  setCollapsed?: jest.Mock;
  onOpenSidebar?: jest.Mock;
} = {}) {
  const sidebarApi: AppChatSidebarApi = {
    collapsed,
    setCollapsed,
    effectiveWidth: 380,
    handleResizeStart: jest.fn(),
    contentAreaCallbackRef: jest.fn(),
  };

  render(
    <TabWithChatSidebarLayout
      tabId="side-bar"
      tabLabel="App"
      mainContent={<div>main</div>}
      sidebarEnabled
      sidebarApi={sidebarApi}
      highlightIcon={highlightIcon}
      onOpenSidebar={onOpenSidebar}
      renderSidebarChat={() => <div>chat</div>}
      contentAreaRef={jest.fn()}
      isActive
    />,
  );

  return { setCollapsed, onOpenSidebar };
}

// The orange collapsed Chat icon's "Mark as seen" button was removed once and
// had to be restored. Before deleting these cases, or the button in
// TabWithChatSidebarLayout, confirm with the author that the dismiss control
// should go.
describe('TabWithChatSidebarLayout', () => {
  it('hides mark-as-seen until the collapsed Chat icon is highlighted', () => {
    renderLayout();

    expect(screen.getByTestId('chat-sidebar-open')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark as seen' })).not.toBeInTheDocument();
  });

  it('clears the orange highlight without opening the sidebar', () => {
    const { setCollapsed, onOpenSidebar } = renderLayout({ highlightIcon: true });

    fireEvent.click(screen.getByRole('button', { name: 'Mark as seen' }));

    expect(onOpenSidebar).toHaveBeenCalledTimes(1);
    expect(setCollapsed).not.toHaveBeenCalled();
  });

  it('opens the sidebar from the highlighted Chat icon', () => {
    const { setCollapsed, onOpenSidebar } = renderLayout({ highlightIcon: true });

    fireEvent.click(screen.getByTestId('chat-sidebar-open'));

    expect(onOpenSidebar).toHaveBeenCalledTimes(1);
    expect(setCollapsed).toHaveBeenCalledWith(false);
  });

  it('hides the floating indicator while the sidebar is open', () => {
    renderLayout({ collapsed: false, highlightIcon: true });

    expect(screen.queryByTestId('chat-sidebar-open')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark as seen' })).not.toBeInTheDocument();
    expect(screen.getByTestId('chat-sidebar-close')).toBeInTheDocument();
  });
});
