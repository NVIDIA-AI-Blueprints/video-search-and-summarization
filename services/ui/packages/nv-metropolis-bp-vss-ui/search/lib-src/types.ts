// SPDX-License-Identifier: MIT
/**
 * Type definitions for the Search component system
 * 
 * This file contains all TypeScript interfaces and types used throughout
 * the search management system, including search data structures, component
 * props, and state management types.
 */

export interface QueryDataContext {
  id: string;
  label: string;
  type: string;
  data: Record<string, unknown>;
}

/**
 * Represents a single search result record from the monitoring system
 */
export interface CriticResult {
  result: 'confirmed' | 'rejected' | 'unverified';
  criteria_met: Record<string, boolean>;
}

export interface SearchData {
  video_name: string;
  description: string;
  start_time: string;
  end_time: string;
  sensor_id: string;
  similarity: number;
  screenshot_url: string;
  object_ids: string[];
  critic_result?: CriticResult;
}

/**
 * Control handlers interface for external rendering
 */
export interface SearchSidebarControlHandlers {
  isDark: boolean;
  onRefresh: () => void;
  controlsComponent: React.ReactNode;
}

/**
 * Props interface for the main SearchComponent
 */
export interface SearchComponentProps {
  theme?: 'light' | 'dark';
  onThemeChange?: (theme: 'light' | 'dark') => void;
  isActive?: boolean; // Whether the tab is currently active/visible
  searchData?: {
    systemStatus: string;
    agentApiUrl?: string;
    vstApiUrl?: string;
    mediaWithObjectsBbox?: boolean;
  };
  serverRenderTime?: string;
  // External controls rendering
  renderControlsInLeftSidebar?: boolean; // Default: false - set true to render controls in external left sidebar
  onControlsReady?: (handlers: SearchSidebarControlHandlers) => void; // Callback to provide control handlers externally
  /** When provided, Agent Mode + Search sends the query to the Chat sidebar (programmatic submit). */
  submitChatMessage?: (message: string) => void;
  /** Registers a handler that receives each Chat sidebar answer from the app-wide callback. */
  registerChatAnswerHandler?: (handler: (answer: string) => boolean | void) => void | (() => void);
  /** Subscribe to Chat sidebar lifecycle events for this tab (message submitted, answer complete without body). */
  registerSidebarChatEventSubscriber?: (
    handler: (event: { type: 'messageSubmitted' } | { type: 'answerComplete' }) => void
  ) => void | (() => void);
  /** When false, the Chat sidebar is open; used to disable search content when sidebar is open or query is running. */
  chatSidebarCollapsed?: boolean;
  /** When true, a message was submitted in the Chat sidebar and the response has not yet finished; keeps search content disabled. */
  chatSidebarBusy?: boolean;
  /** Adds a search result query context item to the Chat sidebar input. */
  addChatQueryContext?: (ctx: QueryDataContext) => void;
}

export interface SearchParams {
  query?: string;
  startDate?: Date | null;
  endDate?: Date | null;
  videoSources?: string[];
  similarity?: number;
  agentMode?: boolean;
  topK?: number;
  sourceType?: string;
}

export interface FilterTag {
  key: string;
  title: string;
  value: string;
}

export interface FilterProps {
  vstApiUrl?: string;
}

export interface StreamInfo {
  name: string;
  type: string;
}