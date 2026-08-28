// SPDX-License-Identifier: MIT
/**
 * FilterControls component for the alerts system.
 *
 * Header-bar refresh and auto-refresh controls. Query filters and fetch
 * settings live in the left sidebar (AlertsSidebarControls).
 */

import React, { useState } from 'react';
import { IconRefresh, IconRotateClockwise2 } from '@tabler/icons-react';
import { VlmVerdict } from '../types';
import { AutoRefreshControl } from './AutoRefreshControl';

interface FilterControlsProps {
  isDark: boolean;
  loading: boolean;
  autoRefreshEnabled: boolean;
  autoRefreshInterval: number; // in milliseconds
  onRefresh: () => void;
  onAutoRefreshToggle: () => void;
  onAutoRefreshIntervalChange: (milliseconds: number) => void;
  // Keep this prop for compatibility with existing callsites/tests.
  vlmVerdict?: VlmVerdict;
}

export const FilterControls: React.FC<FilterControlsProps> = ({
  isDark,
  loading,
  autoRefreshEnabled,
  autoRefreshInterval,
  onRefresh,
  onAutoRefreshToggle,
  onAutoRefreshIntervalChange,
}) => {
  const [showAutoRefreshControl, setShowAutoRefreshControl] = useState(false);

  return (
    <div className="flex items-center justify-end gap-2 my-1 w-full">
      <div className="relative flex items-center gap-2 flex-shrink-0">
        <button
          type="button"
          onClick={() => {
            setShowAutoRefreshControl((prev) => !prev);
          }}
          className={`p-2 rounded transition-colors relative ${
            isDark
              ? 'text-gray-300 hover:bg-neutral-700 hover:text-white'
              : 'text-gray-600 hover:bg-gray-200 hover:text-gray-900'
          }`}
          title={
            autoRefreshEnabled
              ? `Auto-refresh every ${autoRefreshInterval >= 1000 ? `${autoRefreshInterval / 1000}s` : `${autoRefreshInterval}ms`}`
              : 'Auto-refresh is off'
          }
        >
          <IconRotateClockwise2 className="w-4 h-4" />
          {autoRefreshEnabled && (
            <span data-testid="auto-refresh-indicator" className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          )}
        </button>

        <button
          type="button"
          onClick={onRefresh}
          className={`p-2 rounded transition-colors ${
            isDark
              ? 'text-gray-300 hover:bg-neutral-700 hover:text-white'
              : 'text-gray-600 hover:bg-gray-200 hover:text-gray-900'
          }`}
          title="Refresh alerts now"
        >
          <IconRefresh className={`w-4 h-4 ${loading ? 'animate-spin [animation-direction:reverse]' : ''}`} />
        </button>

        <AutoRefreshControl
          isOpen={showAutoRefreshControl}
          isEnabled={autoRefreshEnabled}
          interval={autoRefreshInterval}
          isDark={isDark}
          controlsDisabled={false}
          onToggle={onAutoRefreshToggle}
          onIntervalChange={onAutoRefreshIntervalChange}
          onClose={() => setShowAutoRefreshControl(false)}
        />
      </div>
    </div>
  );
};
