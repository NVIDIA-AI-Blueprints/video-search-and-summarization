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
 * Sub-view selection within the Alerts tab
 */
export type AlertsView = 'view' | 'create';

/**
 * Kind of alert exposed by the Create Alert Rules editor. Only `real-time`
 * has a working implementation today; `verification` is reserved for the
 * disabled placeholder tab and is wired up in a follow-up.
 */
export type AlertRulesType = 'real-time' | 'verification';

/**
 * Server-managed real-time alert rule, mirrors the response shape returned by
 * `GET /realtime` from the vss-alert-bridge microservice (path relative to
 * the configured alerts API base URL, which includes the version prefix).
 *
 * Only the user-facing fields (`live_stream_url`, `alert_type`, `prompt`) and
 * server-generated metadata are required for the UI. Other tunables documented
 * in the API spec (model, chunk_duration, …) are accepted but currently
 * ignored by the UI; they fall back to server-side defaults.
 */
export interface RealtimeAlertRule {
  id: string; // UUID v4 from the server
  live_stream_url: string;
  alert_type: string;
  prompt: string;
  /**
   * Friendly label for the sensor behind `live_stream_url`. Added in the
   * updated vss-alert-bridge spec; optional because the API docs currently
   * only show it in request examples, not in the Input Parameters table.
   */
  sensor_name?: string;
  status?: string; // typically "active"
  created_at?: string; // ISO-8601 UTC
  // The API also returns the following fields; we keep them as optional for
  // forward-compat but do not surface them in the UI.
  system_prompt?: string;
  model?: string;
  chunk_duration?: number;
  chunk_overlap_duration?: number;
  num_frames_per_second_or_fixed_frames_chunk?: number;
  use_fps_for_chunking?: boolean;
  vlm_input_width?: number;
  vlm_input_height?: number;
  enable_reasoning?: boolean;
}

/**
 * Local draft used by the Create Alert Rules editor before a real-time rule has
 * been saved to the server. Once persisted via `POST /realtime` it is
 * replaced by a {@link RealtimeAlertRule} carrying a server-assigned `id`.
 */
export interface RealtimeAlertRuleDraft {
  draftId: string; // local-only id; not sent to the server
  live_stream_url: string;
  alert_type: string;
  prompt: string;
  saving?: boolean;
  error?: string;
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
  /** When true, manual refresh and auto-refresh UI should be disabled (e.g. table on page 2+). */
  refreshControlsSuspended: boolean;
  alertsView: AlertsView;
  onVlmVerifiedChange: (value: boolean) => void;
  onTimeWindowChange: (value: number) => void;
  onRefresh: () => void;
  onAutoRefreshToggle: () => void;
  onAlertsViewChange: (value: AlertsView) => void;
  onAddNewAlertRule: () => void;
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
    alertsApiUrl?: string;
    defaultTimeWindow?: number;
    defaultAutoRefreshInterval?: number; // in milliseconds
    defaultVlmVerified?: boolean;
    maxResults?: number;
    /** Rows per page for the alerts table (client-side pagination). Default 100 from server when unset. */
    pageSize?: number;
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

