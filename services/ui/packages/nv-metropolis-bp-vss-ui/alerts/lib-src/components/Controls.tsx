// SPDX-License-Identifier: MIT
/**
 * Alerts controls — sub-view tablist plus the "Create alert rule" action.
 *
 * Rendering location is the parent app's choice (e.g., a left sidebar, a
 * toolbar, a popover). The component is layout-agnostic beyond the vertical
 * stack it composes internally.
 */

import React from 'react';
import { IconEye, IconPencilPlus, IconPlus } from '@tabler/icons-react';
import { AlertsView } from '../types';

interface ControlsProps {
  isDark: boolean;
  alertsView: AlertsView;
  onAlertsViewChange: (view: AlertsView) => void;
  onAddNewAlertRule: () => void;
}

const ALERTS_VIEW_OPTIONS: Array<{
  id: AlertsView;
  label: string;
  icon: React.ReactNode;
}> = [
  { id: 'view', label: 'View Alerts', icon: <IconEye size={16} /> },
  { id: 'create', label: 'Manage Alerts', icon: <IconPencilPlus size={16} /> },
];

/** Stable id of the panel each tab controls. Matched in AlertsComponent. */
export const ALERTS_VIEW_PANEL_ID: Record<AlertsView, string> = {
  view: 'alerts-panel-view',
  create: 'alerts-panel-create',
};

const tabId = (view: AlertsView) => `alerts-tab-${view}`;

export const Controls: React.FC<ControlsProps> = ({
  isDark,
  alertsView,
  onAlertsViewChange,
  onAddNewAlertRule,
}) => {
  const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([]);

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    let nextIndex: number | null = null;
    // Vertical tablist — Up/Down move focus, Home/End jump to ends. Left/Right
    // are also accepted for users who expect the horizontal tab pattern.
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      nextIndex = (currentIndex + 1) % ALERTS_VIEW_OPTIONS.length;
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      nextIndex = (currentIndex - 1 + ALERTS_VIEW_OPTIONS.length) % ALERTS_VIEW_OPTIONS.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = ALERTS_VIEW_OPTIONS.length - 1;
    }

    if (nextIndex == null) return;
    event.preventDefault();
    const nextOption = ALERTS_VIEW_OPTIONS[nextIndex];
    onAlertsViewChange(nextOption.id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div
      data-testid="alerts-controls"
      className="flex flex-col gap-4 px-4 py-4"
    >
      {/* View / Create sub-view toggle */}
      <div
        role="tablist"
        aria-label="Alerts sub-views"
        aria-orientation="vertical"
        className={`flex flex-col gap-1 rounded-lg p-1 ${
          isDark ? 'bg-neutral-950' : 'bg-gray-100'
        }`}
      >
        {ALERTS_VIEW_OPTIONS.map((option, index) => {
          const isSelected = alertsView === option.id;
          return (
            <button
              key={option.id}
              ref={(el) => {
                tabRefs.current[index] = el;
              }}
              id={tabId(option.id)}
              role="tab"
              aria-selected={isSelected}
              aria-controls={ALERTS_VIEW_PANEL_ID[option.id]}
              tabIndex={isSelected ? 0 : -1}
              data-testid={`alerts-view-${option.id}`}
              onClick={() => onAlertsViewChange(option.id)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded text-sm text-left transition-colors ${
                isSelected
                  ? 'bg-neutral-300 dark:bg-neutral-700 text-neutral-900 dark:text-white font-medium ring-1 ring-[#76b900]'
                  : isDark
                  ? 'text-neutral-300 hover:bg-neutral-800'
                  : 'text-neutral-700 hover:bg-neutral-200'
              }`}
            >
              <span className={`flex-shrink-0 ${isSelected ? 'text-[#76b900]' : ''}`}>
                {option.icon}
              </span>
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>

      {/* Create alert rule action (Create mode only). Extra top margin gives
          the button breathing room from the View/Manage tab toggle above. */}
      {alertsView === 'create' && (
        <button
          type="button"
          onClick={onAddNewAlertRule}
          data-testid="alerts-controls-add-new"
          className={`mt-4 flex items-center justify-center gap-2 px-3 py-2 rounded-md border text-sm font-medium transition-colors ${
            isDark
              ? 'border-neutral-700 bg-neutral-900 text-neutral-100 hover:bg-neutral-800 hover:border-[#76b900]'
              : 'border-gray-300 bg-white text-gray-800 hover:bg-gray-100 hover:border-green-500'
          }`}
        >
          <IconPlus size={16} />
          Create alert rule
        </button>
      )}
    </div>
  );
};
