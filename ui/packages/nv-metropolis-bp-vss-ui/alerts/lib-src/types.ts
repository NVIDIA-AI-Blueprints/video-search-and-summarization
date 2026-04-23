// SPDX-License-Identifier: MIT
/**
 * Type definitions for the Alerts component system
 * 
 * This file contains all TypeScript interfaces and types used throughout
 * the alerts management system, including alert data structures, component
 * props, and state management types.
 */

/**
 * Represents a single alert/incident record from the monitoring system
 */
export interface AlertData {
  id: string;
  timestamp?: string;
  end?: string;
  sensor: string;
  alertType: string;
  alertTriggered: string;
  alertDescription: string;
  metadata: Record<string, any>;
}

/**
 * Control handlers interface for external rendering
 */
export interface AlertsSidebarControlHandlers {
  isDark: boolean;
  vlmVerified: boolean;
  timeWindow: number;
  autoRefreshEnabled: boolean;
  autoRefreshInterval: number;
  onVlmVerifiedChange: (value: boolean) => void;
  onTimeWindowChange: (value: number) => void;
  onRefresh: () => void;
  onAutoRefreshToggle: () => void;
  controlsComponent: React.ReactNode;
}

/**
 * Props interface for the main AlertsComponent
 */
export interface AlertsComponentProps {
  theme?: 'light' | 'dark';
  onThemeChange?: (theme: 'light' | 'dark') => void;
  isActive?: boolean; // Whether the tab is currently active/visible
  alertsData?: {
    systemStatus: string;
    apiUrl?: string;
    vstApiUrl?: string;
    defaultTimeWindow?: number;
    defaultAutoRefreshInterval?: number; // in milliseconds
    defaultVlmVerified?: boolean;
    maxResults?: number;
    alertReportPromptTemplate?: string;
    maxSearchTimeLimit?: string; // Format: "0" (unlimited), "10m", "2h", "3d", "1w", "2M", "1y"
    mediaWithObjectsBbox?: boolean; // Enable overlay bounding boxes on thumbnails and videos
  } | null;
  serverRenderTime?: string;
  // External controls rendering
  renderControlsInLeftSidebar?: boolean; // Default: false - set true to render controls in external left sidebar
  onControlsReady?: (handlers: AlertsSidebarControlHandlers) => void; // Callback to provide control handlers externally
  submitChatMessage?: (message: string) => void;
  registerChatAnswerHandler?: (handler: (answer: string) => boolean | void) => void | (() => void);
  registerSidebarChatEventSubscriber?: (
    handler: (event: { type: 'messageSubmitted' } | { type: 'answerComplete' }) => void
  ) => void | (() => void);
}

/**
 * State interface for managing active filters across different alert categories
 */
export interface FilterState {
  sensors: Set<string>;
  
  alertTypes: Set<string>;
  
  alertTriggered: Set<string>;
}

/**
 * Union type representing all possible filter categories
 */
export type FilterType = keyof FilterState;

/**
 * VLM Verdict values returned from the API
 */
export const VLM_VERDICT = {
  ALL: 'all',
  CONFIRMED: 'confirmed',
  REJECTED: 'rejected',
  VERIFICATION_FAILED: 'verification-failed',
  NOT_CONFIRMED: 'not-confirmed'
} as const;

/**
 * Type for VLM Verdict values
 */
export type VlmVerdict = typeof VLM_VERDICT[keyof typeof VLM_VERDICT];

/**
 * Helper to check if a string is a valid VLM verdict
 */
export const isValidVlmVerdict = (value: string): value is VlmVerdict => {
  return Object.values(VLM_VERDICT).includes(value as VlmVerdict);
};

