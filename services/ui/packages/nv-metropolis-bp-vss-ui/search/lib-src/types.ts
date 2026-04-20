// SPDX-License-Identifier: MIT
/**
 * Type definitions for the Search component system
 * 
 * This file contains all TypeScript interfaces and types used throughout
 * the search management system, including search data structures, component
 * props, and state management types.
 */

/**
 * Represents a single search result record from the monitoring system
 */
export interface SearchData {
  video_name: string;
  description: string;
  start_time: string;
  end_time: string;
  sensor_id: string;
  similarity: number;
  screenshot_url: string;
  object_ids: string[];
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
  /** Registers a handler that receives the full agent answer string when the Chat sidebar completes a response. Used to extract Search API–shaped JSON and update the Search tab main content. */
  registerChatAnswerHandler?: (handler: (answer: string) => void) => void;
  /** When false, the Chat sidebar is open; used to disable search content when sidebar is open or query is running. */
  chatSidebarCollapsed?: boolean;
  /** When true, a message was submitted in the Chat sidebar and the response has not yet finished; keeps search content disabled. */
  chatSidebarBusy?: boolean;
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