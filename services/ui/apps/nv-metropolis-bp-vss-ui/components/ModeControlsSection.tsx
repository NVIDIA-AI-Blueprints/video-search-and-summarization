// SPDX-License-Identifier: MIT
import React from 'react';
import { ChatSidebarContent, type ChatSidebarControlHandlers } from '@nemo-agent-toolkit/ui';
import type { 
  AlertsSidebarControlHandlers,
  SearchSidebarControlHandlers,
  DashboardSidebarControlHandlers,
  MapSidebarControlHandlers,
  VideoManagementSidebarControlHandlers
} from '@nv-metropolis-bp-vss-ui/all';

import { hasComponentContentArray } from '../utils';

interface ModeControlsSectionProps {
  chatHandlers: ChatSidebarControlHandlers | null;
  alertsHandlers: AlertsSidebarControlHandlers | null;
  searchHandlers: SearchSidebarControlHandlers | null;
  dashboardHandlers: DashboardSidebarControlHandlers | null;
  mapHandlers: MapSidebarControlHandlers | null;
  videoManagementHandlers: VideoManagementSidebarControlHandlers | null;
  activeTabLabel: string;
}

/**
 * MODE CONTROLS section for the VSS UI main sidebar.
 * Renders mode-specific controls based on which handlers are provided.
 * - Chat handlers: Renders complete chat sidebar (conversations, search, settings)
 * - Alerts handlers: Renders alerts filter controls
 * - Dashboard handlers: Renders dashboard controls
 * - Map handlers: Renders map controls
 */
export const ModeControlsSection: React.FC<ModeControlsSectionProps> = ({ 
  chatHandlers, 
  alertsHandlers,
  searchHandlers,
  dashboardHandlers,
  mapHandlers,
  videoManagementHandlers,
  activeTabLabel
}) => {
  const [hasSearchModeControls, hasDashboardModeControls, hasAlertsModeControls, hasMapModeControls, hasVideoManagementModeControls] = hasComponentContentArray([searchHandlers?.controlsComponent, dashboardHandlers?.controlsComponent, alertsHandlers?.controlsComponent, mapHandlers?.controlsComponent, videoManagementHandlers?.controlsComponent]) as [boolean, boolean, boolean, boolean, boolean];

  // Determine if we have actual controls content to render
  const hasActualControlsContent = (
    chatHandlers ||
    (alertsHandlers && hasAlertsModeControls) ||
    (searchHandlers && hasSearchModeControls) ||
    (dashboardHandlers && hasDashboardModeControls) ||
    (mapHandlers && hasMapModeControls) ||
    (videoManagementHandlers && hasVideoManagementModeControls)
  );

  return (
    <div 
      className="flex flex-col flex-1 overflow-hidden border-b border-gray-300 dark:border-gray-600"
      style={{
        boxShadow: 'inset 0 8px 12px -2px rgba(0, 0, 0, 0.3)'
      }}
    >
      {/* Section Header */}
      <div className="px-4 pt-3 pb-2 flex-shrink-0" title={activeTabLabel ? `${activeTabLabel} Tab Controls` : undefined}>
        <h2 className="text-base font-normal text-gray-500 dark:text-gray-400 uppercase tracking-wider text-left">
        {activeTabLabel}
        </h2>
      </div>
      
      {/* Content Area */}
      {hasActualControlsContent ? (
        <div className="flex-1 overflow-y-auto overflow-x-auto flex flex-col bg-white dark:bg-gray-800">
          {chatHandlers && <ChatSidebarContent {...chatHandlers} />}
          {alertsHandlers && alertsHandlers.controlsComponent}
          {searchHandlers && searchHandlers.controlsComponent}
          {dashboardHandlers && dashboardHandlers.controlsComponent}
          {mapHandlers && mapHandlers.controlsComponent}
          {videoManagementHandlers && videoManagementHandlers.controlsComponent}
        </div>
      ) : (
        <div className="flex-1 flex items-center justify-center p-8 overflow-y-auto bg-white dark:bg-gray-800">
          <span className="text-gray-500 dark:text-gray-400 text-sm italic">
            No Controls
          </span>
        </div>
      )}
    </div>
  );
};

