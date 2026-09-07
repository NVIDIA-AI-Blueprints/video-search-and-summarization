// SPDX-License-Identifier: MIT
import React, { useState, useMemo, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { env } from 'next-runtime-env';
import type { ChatSidebarControlHandlers } from '@nv-metropolis-bp-vss-ui/chat';
import type { 
  AlertsSidebarControlHandlers,
  SearchSidebarControlHandlers,
  DashboardSidebarControlHandlers,
  MapSidebarControlHandlers,
  VideoManagementSidebarControlHandlers,
  QueryDataContext
} from '@nv-metropolis-bp-vss-ui/all';
import { 
  IconMessageCircle, 
  IconSearch, 
  IconAlertTriangle, 
  IconLayoutDashboard, 
  IconMapPin,
  IconVideo,
  IconSun,
  IconMoon,
  IconMenu2
} from '@tabler/icons-react';
import { getTabChatInitialStateOverride, getTabChatWorkflow } from '../utils/tabChatEnv';
import {
  CHAT_SIDEBAR_INSTANCE_STORAGE_PREFIX,
  SIDEBAR_CHAT_ENV_TAB_KEY,
  getChatSidebarEnabled,
} from '../utils/tabChatSidebarConfig';

import { useTheme } from '../hooks/useTheme';
import { useAppChatSidebar } from '../hooks/useAppChatSidebar';
import { useChatSidebarMainTabBridge } from '../hooks/useChatSidebarMainTabBridge';
import { parseMainTabIdFromCallerInfoHash } from '../utils/callerInfoMainTabHash';
import { parseSidebarMainTabId } from '../utils/sidebarMainTabChatSubscribers';
import { TabWithChatSidebarLayout } from './TabWithChatSidebarLayout';
import packageJson from '../package.json';
import { APPLICATION_TITLE, APPLICATION_SUBTITLE } from '../constants/constants';

import { ModeControlsSection } from './ModeControlsSection';


// Type definitions for SSR data
interface AlertsData {
  systemStatus: string;
  apiUrl?: string;
  vstApiUrl?: string;
  defaultTimeWindow?: number;
}

interface SearchData {
  systemStatus: string;
  apiUrl?: string;
}

interface DashboardData {
  systemStatus: string;
  dashboardUrl: string;
}

interface MapData {
  systemStatus: string;
  mapUrl: string;
}

interface VideoManagementData {
  systemStatus: string;
  vstApiUrl?: string | null;
}

interface HomeProps {
  children?: React.ReactNode;
  // SSR data props (optional - fetched from server)
  alertsData?: AlertsData | null;
  searchData?: SearchData | null;
  dashboardData?: DashboardData | null;
  mapData?: MapData | null;
  videoManagementData?: VideoManagementData | null;
  serverRenderTime?: string;
}

interface TabConfig {
  id: string;
  label: string;
  icon: React.ReactNode;
  alt: string;
  enabled: boolean;
  component?: string; // Component name to import from library
}

// Dynamic component imports based on configuration
// These are loaded at runtime only if the corresponding tab is enabled
// The chat surface. Speaks the BYO agent contract directly; the NeMo Agent
// Toolkit UI it replaced has been removed from the repo.
const VssChatPanel = dynamic(
  () => import('@nv-metropolis-bp-vss-ui/chat').then((mod) => mod.ChatPanel),
  { ssr: false },
);

/** Point an absolute VSS media URL at this app's same-origin proxy. */
const proxyMediaUrl = (value: unknown): unknown => {
  if (typeof value !== 'string' || !/^https?:\/\//.test(value)) return value;
  try {
    const { pathname, search } = new URL(value);
    return `/api/proxy${pathname}${search}`;
  } catch {
    return value;
  }
};

/** Rewrite every *_url field on each legacy search hit. */
const withProxiedMedia = (hits: Array<Record<string, unknown>>) =>
  hits.map((hit) => {
    const next: Record<string, unknown> = { ...hit };
    for (const key of Object.keys(next)) {
      if (key.endsWith('_url')) next[key] = proxyMediaUrl(next[key]);
    }
    return next;
  });

const readEnv = (key: string) => env(key) || process.env[key] || '';

/**
 * Chat endpoint + title for a surface.
 *
 * The browser uses #1980's same-origin run/event API. Its adapter, backend
 * address, and credential remain in the existing Next.js server process.
 */
type ChatSurface = 'main' | 'sidebar';

/**
 * Read a chat setting for one surface.
 *
 * The two surfaces are configured independently: the sidebar overrides a main
 * variable by re-declaring it under `NEXT_PUBLIC_SIDEBAR_CHAT_`, falling back
 * to the main value when it does not. This deployment relies on it — the
 * sidebar chat has its own agent-parameter fields (search source type, critic
 * toggle) that the chat tab does not, and reading only the main variables
 * silently drops them.
 *
 * Same resolution order as `utils/tabChatEnv.ts`, which the toolkit path uses.
 */
const surfaceEnv = (surface: ChatSurface, mainKey: string): string => {
  const suffix = mainKey.replace(/^NEXT_PUBLIC_/, '');
  if (surface === 'sidebar') {
    const scoped = readEnv(`NEXT_PUBLIC_SIDEBAR_CHAT_${suffix}`);
    if (scoped) return scoped;
  }
  return readEnv(mainKey);
};

const surfaceFlag = (surface: ChatSurface, mainKey: string, fallback: boolean) => {
  const value = surfaceEnv(surface, mainKey);
  return value === '' ? fallback : value === 'true';
};

const vssChatConfig = (surface: ChatSurface) => {
  const adapterEnabled = surfaceFlag(surface, 'NEXT_PUBLIC_AGENT_ADAPTER_ENABLED', false);
  return {
    url: adapterEnabled ? '/api/agent' : `/api/vss-chat?surface=${surface}`,
    transport: adapterEnabled ? ('agent-api' as const) : ('chat-sse' as const),
    surface: `vss-ui-${surface}`,
    mediaProxyUrl: '/api/proxy',
    title: surfaceEnv(surface, 'NEXT_PUBLIC_WORKFLOW') || 'Chat',
    // Upload is a chunked, multi-minute transfer to VST, not an agent run.
    uploadUrlBase: surfaceEnv(surface, 'NEXT_PUBLIC_VST_API_URL'),
  };
};

/**
 * Feature switches for the replacement chat, read from the same
 * NEXT_PUBLIC_CHAT_* variables the toolkit chat bar used.
 *
 * Reading the toolkit's own variables is the point: a deployment that already
 * turned message copy off keeps it off after the swap, with nothing to migrate.
 */
const vssChatFeatures = (surface: ChatSurface) => ({
  chatHistory: surfaceFlag(surface, 'NEXT_PUBLIC_CHAT_HISTORY_DEFAULT_ON', true),
  intermediateSteps: surfaceFlag(surface, 'NEXT_PUBLIC_ENABLE_INTERMEDIATE_STEPS', true),
  messageCopy: surfaceFlag(surface, 'NEXT_PUBLIC_CHAT_MESSAGE_COPY_ENABLED', false),
  messageEdit: surfaceFlag(surface, 'NEXT_PUBLIC_CHAT_MESSAGE_EDIT_ENABLED', false),
  messageSpeaker: surfaceFlag(surface, 'NEXT_PUBLIC_CHAT_MESSAGE_SPEAKER_ENABLED', false),
  inputMic: surfaceFlag(surface, 'NEXT_PUBLIC_CHAT_INPUT_MIC_ENABLED', false),
  uploadFile: surfaceFlag(surface, 'NEXT_PUBLIC_CHAT_UPLOAD_FILE_ENABLE', true),
  uploadFileMetadata: surfaceFlag(surface, 'NEXT_PUBLIC_CHAT_UPLOAD_FILE_METADATA_ENABLED', false),
  themeToggle: surfaceFlag(surface, 'NEXT_PUBLIC_SHOW_THEME_TOGGLE_BUTTON', false),
});

const vssChatUploadConfig = (surface: ChatSurface) => ({
  customAgentParamsJson: surfaceEnv(surface, 'NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON'),
  uploadConfigTemplateJson: surfaceEnv(
    surface,
    'NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON',
  ),
  uploadHiddenMessageTemplate: surfaceEnv(
    surface,
    'NEXT_PUBLIC_CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE',
  ),
});

const dynamicComponents = {
  AlertsComponent: dynamic(() => 
    import('@nv-metropolis-bp-vss-ui/all').then(mod => mod.AlertsComponent).catch((error) => {
      console.error('[DynamicImport] Failed to load AlertsComponent:', error);
      return () => (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Alerts</h2>
          <p className="text-gray-600 dark:text-gray-400">
            Alerts component library not available. Please install @nv-metropolis-bp-vss-ui/all package.
          </p>
        </div>
      );
    }),
    { 
      ssr: true,
      loading: () => (
        <div className="flex-1 p-6 overflow-auto">
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 dark:text-gray-400">Loading Alerts...</p>
          </div>
        </div>
      )
    }
  ),
  SearchComponent: dynamic(() => 
    import('@nv-metropolis-bp-vss-ui/all').then(mod => mod.SearchComponent).catch((error) => {
      console.error('[DynamicImport] Failed to load SearchComponent:', error);
      return () => (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Search</h2>
          <p className="text-gray-600 dark:text-gray-400">
            Search component library not available. Please install @nv-metropolis-bp-vss-ui/all package.
          </p>
        </div>
      );
    }),
    { 
      ssr: true,
      loading: () => (
        <div className="flex-1 p-6 overflow-auto">
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 dark:text-gray-400">Loading Search...</p>
          </div>
        </div>
      )
    }
  ),
  DashboardComponent: dynamic(() => 
    import('@nv-metropolis-bp-vss-ui/all').then(mod => mod.DashboardComponent).catch((error) => {
      console.error('[DynamicImport] Failed to load DashboardComponent:', error);
      return () => (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Dashboard</h2>
          <p className="text-gray-600 dark:text-gray-400">
            Dashboard component library not available. Please install @nv-metropolis-bp-vss-ui/all package.
          </p>
        </div>
      );
    }),
    { 
      ssr: true,
      loading: () => (
        <div className="flex-1 p-6 overflow-auto">
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 dark:text-gray-400">Loading Dashboard...</p>
          </div>
        </div>
      )
    }
  ),
  MapComponent: dynamic(() => 
    import('@nv-metropolis-bp-vss-ui/all').then(mod => mod.MapComponent).catch((error) => {
      console.error('[DynamicImport] Failed to load MapComponent:', error);
      return () => (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Map</h2>
          <p className="text-gray-600 dark:text-gray-400">
            Map component library not available. Please install @nv-metropolis-bp-vss-ui/all package.
          </p>
        </div>
      );
    }),
    { 
      ssr: true,
      loading: () => (
        <div className="flex-1 p-6 overflow-auto">
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 dark:text-gray-400">Loading Map...</p>
          </div>
        </div>
      )
    }
  ),
  VideoManagementComponent: dynamic(() => 
    import('@nv-metropolis-bp-vss-ui/all').then(mod => mod.VideoManagementComponent).catch((error) => {
      console.error('[DynamicImport] Failed to load VideoManagementComponent:', error);
      return () => (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Video Management</h2>
          <p className="text-gray-600 dark:text-gray-400">
            Video Management component library not available.
          </p>
        </div>
      );
    }),
    { 
      ssr: true,
      loading: () => (
        <div className="flex-1 p-6 overflow-auto">
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 dark:text-gray-400">Loading Video Management...</p>
          </div>
        </div>
      )
    }
  ),
};

export default function Home({ alertsData, searchData, dashboardData, mapData, videoManagementData, serverRenderTime }: HomeProps) {
  // Get deployment configuration from environment variables - memoize to prevent recreation
  const deploymentConfig = useMemo(() => {
    return {
      enableChatTab: (env('NEXT_PUBLIC_ENABLE_CHAT_TAB') || process.env.NEXT_PUBLIC_ENABLE_CHAT_TAB) !== 'false',
      enableAlertsTab: (env('NEXT_PUBLIC_ENABLE_ALERTS_TAB') || process.env.NEXT_PUBLIC_ENABLE_ALERTS_TAB) !== 'false',
      enableSearchTab: (env('NEXT_PUBLIC_ENABLE_SEARCH_TAB') || process.env.NEXT_PUBLIC_ENABLE_SEARCH_TAB) !== 'false',
      enableDashboardTab: (env('NEXT_PUBLIC_ENABLE_DASHBOARD_TAB') || process.env.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB) !== 'false',
      enableMapTab: (env('NEXT_PUBLIC_ENABLE_MAP_TAB') || process.env.NEXT_PUBLIC_ENABLE_MAP_TAB) !== 'false',
      enableVideoManagementTab: (env('NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB') || process.env.NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB) !== 'false',
      chatSidebarEnabled: getChatSidebarEnabled(),
    };
  }, []); // Empty deps - env vars don't change during runtime

  // Define all possible tabs with their configuration - memoize to prevent recreation
  const allTabs: TabConfig[] = useMemo(() => [
    { 
      id: 'chat', 
      label: 'Chat', 
      icon: <IconMessageCircle size={16} />, 
      alt: 'Chat with Agent',
      enabled: deploymentConfig.enableChatTab,
      component: 'ChatComponent'
    },
    { 
      id: 'search', 
      label: 'Search', 
      icon: <IconSearch size={16} />, 
      alt: 'Search',
      enabled: deploymentConfig.enableSearchTab,
      component: 'SearchComponent'
    },
    { 
      id: 'alerts', 
      label: 'Alerts', 
      icon: <IconAlertTriangle size={16} />, 
      alt: 'Alerts List',
      enabled: deploymentConfig.enableAlertsTab,
      component: 'AlertsComponent'
    },
    { 
      id: 'dashboard', 
      label: 'Dashboard', 
      icon: <IconLayoutDashboard size={16} />, 
      alt: 'Dashboard',
      enabled: deploymentConfig.enableDashboardTab,
      component: 'DashboardComponent'
    },
    { 
      id: 'map', 
      label: 'Map', 
      icon: <IconMapPin size={16} />, 
      alt: 'Map',
      enabled: deploymentConfig.enableMapTab,
      component: 'MapComponent'
    },
    { 
      id: 'video-management', 
      label: 'Video Management', 
      icon: <IconVideo size={16} />, 
      alt: 'Video Management',
      enabled: deploymentConfig.enableVideoManagementTab,
      component: 'VideoManagementComponent'
    },
  ], [deploymentConfig]);

  // Filter tabs based on deployment configuration
  const visibleTabs = useMemo(() => 
    allTabs.filter(tab => tab.enabled), 
    [allTabs]
  );

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Set initial active tab - start with first visible tab for SSR compatibility
  const [activeTab, setActiveTabInternal] = useState(() => {
    // For SSR, return first visible tab or 'chat' as fallback
    return visibleTabs.length > 0 ? visibleTabs[0].id : 'chat';
  });
  
  const setActiveTab = React.useCallback((newTab: string) => {
    setActiveTabInternal(newTab);
  }, []);

  // State for holding mode-specific control handlers
  const [chatControlHandlers, setChatControlHandlers] = useState<ChatSidebarControlHandlers | null>(null);
  const [alertsControlHandlers, setAlertsControlHandlers] = useState<AlertsSidebarControlHandlers | null>(null);
  const [searchControlHandlers, setSearchControlHandlers] = useState<SearchSidebarControlHandlers | null>(null);
  const [dashboardControlHandlers, setDashboardControlHandlers] = useState<DashboardSidebarControlHandlers | null>(null);
  const [mapControlHandlers, setMapControlHandlers] = useState<MapSidebarControlHandlers | null>(null);
  const [videoManagementControlHandlers, setVideoManagementControlHandlers] = useState<VideoManagementSidebarControlHandlers | null>(null);
  
  // Refs to track if handlers have been set (to prevent re-setting the same handlers)
  const chatHandlersSetRef = React.useRef(false);
  const alertsHandlersSetRef = React.useRef(false);
  const searchHandlersSetRef = React.useRef(false);
  const dashboardHandlersSetRef = React.useRef(false);
  const mapHandlersSetRef = React.useRef(false);
  const videoManagementHandlersSetRef = React.useRef(false);

  // Load saved tab from sessionStorage after mount (client-side only)
  const [hasLoadedFromStorage, setHasLoadedFromStorage] = React.useState(false);

  const sidebarApi = useAppChatSidebar();

  // Read once. These come from env, which cannot change at runtime, and a fresh
  // object each render would give ChatPanel a new `features` identity — which
  // is the prop its per-message memo compares on, so every message in the
  // thread would re-render on every Home render.
  const vssMainChatFeatures = useMemo(() => vssChatFeatures('main'), []);
  const vssSidebarChatFeatures = useMemo(() => vssChatFeatures('sidebar'), []);
  const vssMainChatExtraConfig = useMemo(() => vssChatUploadConfig('main'), []);
  const vssSidebarChatExtraConfig = useMemo(() => vssChatUploadConfig('sidebar'), []);
  const vssMainChatEndpoint = useMemo(() => {
    const { url, transport, surface, mediaProxyUrl, uploadUrlBase } = vssChatConfig('main');
    return { url, transport, surface, mediaProxyUrl, uploadUrlBase };
  }, []);
  const vssMainChatTitle = useMemo(() => vssChatConfig('main').title, []);
  const vssSidebarChatEndpoint = useMemo(() => {
    const { url, transport, surface, mediaProxyUrl, uploadUrlBase } = vssChatConfig('sidebar');
    return { url, transport, surface, mediaProxyUrl, uploadUrlBase };
  }, []);
  const vssSidebarChatTitle = useMemo(() => vssChatConfig('sidebar').title, []);

  const {
    chatSidebarHighlight,
    chatSidebarQueryExecuting,
    searchTabChatSidebarBusy,
    clearChatSidebarHighlight,
    highlightSidebarWhenCollapsed,
    submitSidebarMessage,
    registerSearchTabChatAnswer,
    registerSearchTabSidebarChatEvents,
    registerAlertsTabChatAnswer,
    registerAlertsTabSidebarChatEvents,
    registerDashboardTabChatAnswer,
    registerDashboardTabSidebarChatEvents,
    registerMapTabChatAnswer,
    registerMapTabSidebarChatEvents,
    registerVideoManagementTabChatAnswer,
    registerVideoManagementTabSidebarChatEvents,
    registerMainTabChatVideoUploadComplete,
    handleSidebarChatVideoUploadComplete,
    handleSidebarAnswerComplete,
    handleSidebarAnswerCompleteWithContent,
    handleMainChatAnswerCompleteWithContent,
    handleSidebarSubmitMessageReady,
    handleSidebarMessageSubmitted,
  } = useChatSidebarMainTabBridge({
    activeTab,
    sidebarCollapsed: sidebarApi.collapsed,
  });

  const appSidebarAddQueryContextRef = React.useRef<
    ((item: QueryDataContext) => void) | undefined
  >(undefined);

  /** Passed to tabs with "+ Chat" / add-context controls; only when the floating sidebar is enabled. */
  const sidebarAddChatQueryContext = deploymentConfig.chatSidebarEnabled
    ? (item: QueryDataContext) => {
        appSidebarAddQueryContextRef.current?.(item);
        highlightSidebarWhenCollapsed();
      }
    : undefined;

  /** Sidebar chat submit (e.g. Alerts "Generate Report", Search agent queries); only when sidebar is enabled. */
  const sidebarSubmitChatMessage = deploymentConfig.chatSidebarEnabled
    ? (message: string) => {
        submitSidebarMessage(message);
        highlightSidebarWhenCollapsed();
      }
    : undefined;

  React.useEffect(() => {
    // Only run once on mount to load from sessionStorage
    if (!hasLoadedFromStorage && typeof window !== 'undefined') {
      try {
        const stored = sessionStorage.getItem('activeTab');
        
        if (stored !== null) {
          // Validate that the stored tab is visible
          const isValid = visibleTabs.some(tab => tab.id === stored);
          if (isValid) {
            setActiveTab(stored);
          }
        }
      } catch (error) {
        console.warn('[Home] Failed to load activeTab from sessionStorage:', error);
      }
      setHasLoadedFromStorage(true);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only run once on mount

  // Validate and update activeTab when visibleTabs changes
  React.useEffect(() => {
    if (visibleTabs.length > 0 && hasLoadedFromStorage) {
      const isValid = visibleTabs.some(tab => tab.id === activeTab);
      if (!isValid) {
        // If current activeTab is not valid, switch to first visible tab
        setActiveTab(visibleTabs[0].id);
      }
    }
  }, [visibleTabs, activeTab, hasLoadedFromStorage]);

  // Save activeTab to sessionStorage whenever it changes (only after initial load)
  React.useEffect(() => {
    if (hasLoadedFromStorage && typeof window !== 'undefined') {
      try {
        sessionStorage.setItem('activeTab', activeTab);
      } catch (error) {
        console.warn('[Home] Failed to save activeTab to sessionStorage:', error);
      }
    }
  }, [activeTab, hasLoadedFromStorage]);

  const { theme, toggleTheme, isDark, setTheme } = useTheme();

  // Set document title - override any embedded component titles
  useEffect(() => {
    document.title = APPLICATION_TITLE;
    
    // Create a MutationObserver to watch for title changes and override them
    const observer = new MutationObserver(() => {
      if (document.title !== APPLICATION_TITLE) {
        document.title = APPLICATION_TITLE;
      }
    });
    
    // Observe the document title element
    const titleElement = document.querySelector('title');
    if (titleElement) {
      observer.observe(titleElement, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    }
    
    return () => {
      observer.disconnect();
    };
  }, []);

  // Handle theme changes from the embedded component - useCallback to prevent recreation
  const handleThemeChange = React.useCallback((newTheme: string) => {
    const validTheme = newTheme === 'light' || newTheme === 'dark' ? newTheme : 'dark';
    if (validTheme !== theme) {
      setTheme(validTheme);
    }
  }, [theme, setTheme]);

  // Caller-info links in embedded chat use `#vss-mt-<tabId>`; switch main tab without toolkit hooks.
  React.useEffect(() => {
    const syncMainTabFromCallerInfoHash = () => {
      const raw = parseMainTabIdFromCallerInfoHash(window.location.hash);
      if (!raw) return;
      const parsed = parseSidebarMainTabId(raw);
      if (!parsed) return;
      if (!visibleTabs.some((tab) => tab.id === parsed)) return;
      setActiveTab(parsed);
      window.history.replaceState(
        null,
        '',
        `${window.location.pathname}${window.location.search}`,
      );
    };
    syncMainTabFromCallerInfoHash();
    window.addEventListener('hashchange', syncMainTabFromCallerInfoHash);
    return () =>
      window.removeEventListener('hashchange', syncMainTabFromCallerInfoHash);
  }, [visibleTabs, setActiveTab]);

  const renderAppSidebarChat = React.useCallback(
    () => (
      // The bridge callbacks are what feed answers to the search/alerts tabs
      // and clear stale results on submit, so they are preserved verbatim.
        <VssChatPanel
          endpoint={vssSidebarChatEndpoint}
          title={vssSidebarChatTitle}
          theme={theme === 'dark' ? 'dark' : 'light'}
          onThemeChange={handleThemeChange}
          isActive={activeTab !== 'chat'}
          features={vssSidebarChatFeatures}
          {...vssSidebarChatExtraConfig}
          // Separates this panel's conversations from the chat tab's, the same
          // job the toolkit's storageKeyPrefix did.
          storageKeyPrefix={CHAT_SIDEBAR_INSTANCE_STORAGE_PREFIX}
          onAnswerComplete={handleSidebarAnswerComplete}
          onSubmitMessageReady={handleSidebarSubmitMessageReady}
          onMessageSubmitted={handleSidebarMessageSubmitted}
          onAddQueryContextReady={(addItem: (item: QueryDataContext) => void) => {
            appSidebarAddQueryContextRef.current = addItem;
          }}
          onChatVideoUploadComplete={handleSidebarChatVideoUploadComplete}
          // Structured artifact events are appended to this callback payload
          // by the chat transport, without leaking transport markup into the
          // visible assistant message.
          onAnswer={(answer: string, conversationId: string) => {
            handleSidebarAnswerCompleteWithContent(answer);
            if (vssSidebarChatEndpoint.transport === 'agent-api') return;

            // Legacy chat-SSE adapters expose search results through a
            // conversation-scoped follow-up read rather than artifact events.
            void (async () => {
              try {
                const response = await fetch(
                  `/api/vss-chat?surface=sidebar&conversation=${encodeURIComponent(conversationId)}`,
                );
                if (!response.ok) return;
                const last = await response.json();
                if (!last?.data?.length) return;
                handleSidebarAnswerCompleteWithContent(
                  JSON.stringify({ data: withProxiedMedia(last.data) }),
                );
              } catch {
                // Non-fatal: the chat answer has already been delivered.
              }
            })();
          }}
          onSubmit={() => handleSidebarMessageSubmitted()}
        />
    ),
    [
      theme,
      handleThemeChange,
      activeTab,
      handleSidebarAnswerComplete,
      handleSidebarAnswerCompleteWithContent,
      handleSidebarSubmitMessageReady,
      handleSidebarMessageSubmitted,
      handleSidebarChatVideoUploadComplete,
      vssSidebarChatFeatures,
      vssSidebarChatExtraConfig,
      vssSidebarChatEndpoint,
      vssSidebarChatTitle,
    ],
  );

  const chatControlsReadyCallback = React.useCallback((handlers: ChatSidebarControlHandlers) => {
    chatHandlersSetRef.current = true;
    setChatControlHandlers(handlers);
  }, []);

  const alertsControlsReadyCallback = React.useCallback((handlers: AlertsSidebarControlHandlers) => {
    alertsHandlersSetRef.current = true;
    setAlertsControlHandlers((prev: AlertsSidebarControlHandlers | null) => {
      // AlertsComponent's onControlsReady effect has 16 deps and refires often
      // (auto-refresh toggle, time-window change, etc.). ModeControlsSection
      // only reads `controlsComponent` from this object, and that field is
      // useMemo'd downstream — so skip the state update unless the rendered
      // JSX actually changed. Avoids cascading Home re-renders into every tab.
      if (prev && prev.controlsComponent === handlers.controlsComponent) return prev;
      return handlers;
    });
  }, []);

  const searchControlsReadyCallback = React.useCallback((handlers: SearchSidebarControlHandlers) => {
    searchHandlersSetRef.current = true;
    setSearchControlHandlers((prev: SearchSidebarControlHandlers | null) => {
      if (prev && prev.controlsComponent === handlers.controlsComponent) return prev;
      return handlers;
    });
  }, []);

  const dashboardControlsReadyCallback = React.useCallback((handlers: DashboardSidebarControlHandlers) => {
    if (!dashboardHandlersSetRef.current) {
      dashboardHandlersSetRef.current = true;
      setDashboardControlHandlers(handlers);
    }
  }, []);

  const mapControlsReadyCallback = React.useCallback((handlers: MapSidebarControlHandlers) => {
    if (!mapHandlersSetRef.current) {
      mapHandlersSetRef.current = true;
      setMapControlHandlers(handlers);
    }
  }, []);

  const videoManagementControlsReadyCallback = React.useCallback((handlers: VideoManagementSidebarControlHandlers) => {
    videoManagementHandlersSetRef.current = true;
    setVideoManagementControlHandlers((prev: VideoManagementSidebarControlHandlers | null) => {
      if (prev && prev.controlsComponent === handlers.controlsComponent) return prev;
      return handlers;
    });
  }, []);

  // Clear mode controls when switching tabs
  React.useEffect(() => {
    if (activeTab !== 'chat') {
      // The panel stays mounted when the tab is hidden, so it never stops
      // reporting its controls; without this they leak into the next tab's
      // MODE CONTROLS section.
      setChatControlHandlers(null);
      chatHandlersSetRef.current = false;
    }
    if (activeTab !== 'alerts') {
      setAlertsControlHandlers(null);
      alertsHandlersSetRef.current = false;
    }
    if (activeTab !== 'search') {
      setSearchControlHandlers(null);
      searchHandlersSetRef.current = false;
    }
    if (activeTab !== 'dashboard') {
      setDashboardControlHandlers(null);
      dashboardHandlersSetRef.current = false;
    }
    if (activeTab !== 'map') {
      setMapControlHandlers(null);
      mapHandlersSetRef.current = false;
    }
    if (activeTab !== 'video-management') {
      setVideoManagementControlHandlers(null);
      videoManagementHandlersSetRef.current = false;
    }
  }, [activeTab]);

  // Render a single tab component with visibility control
  const renderTabComponent = (tabConfig: TabConfig) => {
    const isActive = activeTab === tabConfig.id;

    // Chat is checked before the registry lookup: the panel is its own
    // top-level dynamic import, not an entry in `dynamicComponents`.
    if (tabConfig.component === 'ChatComponent') {
      return (
        <div
          key={tabConfig.id}
          className="absolute inset-0 flex flex-col overflow-hidden"
          style={{ display: isActive ? 'flex' : 'none' }}
        >
          <VssChatPanel
            endpoint={vssMainChatEndpoint}
            title={vssMainChatTitle}
            theme={theme === 'dark' ? 'dark' : 'light'}
            onThemeChange={handleThemeChange}
            isActive={isActive}
            features={vssMainChatFeatures}
            {...vssMainChatExtraConfig}
            onAnswer={handleMainChatAnswerCompleteWithContent}
            // The chat tab renders its conversation list in the app's left
            // sidebar, which is what renderControlsInLeftSidebar did before.
            onControlsReady={isActive ? chatControlsReadyCallback : undefined}
          />
        </div>
      );
    }

    const componentName = tabConfig.component as keyof typeof dynamicComponents;
    const DynamicComponent = dynamicComponents[componentName];

    if (!DynamicComponent) {
      return (
        <div 
          key={tabConfig.id}
          className="absolute inset-0 flex flex-col p-6 overflow-auto"
          style={{ display: isActive ? 'flex' : 'none' }}
        >
          <div>
            <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Unknown Component</h2>
            <p className="text-gray-600 dark:text-gray-400">Component "{tabConfig.component}" not found.</p>
          </div>
        </div>
      );
    }

    // Non-Chat tabs: build componentProps for all
    const componentProps: any = {
      theme,
      onThemeChange: handleThemeChange,
      isActive,
    };
    if (componentName === 'SearchComponent') {
      componentProps.searchData = searchData ?? undefined;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? searchControlsReadyCallback : undefined;
      componentProps.registerChatAnswerHandler = registerSearchTabChatAnswer;
      componentProps.registerSidebarChatEventSubscriber = registerSearchTabSidebarChatEvents;
      componentProps.submitChatMessage = sidebarSubmitChatMessage;
      componentProps.addChatQueryContext = sidebarAddChatQueryContext;
      componentProps.chatSidebarCollapsed = deploymentConfig.chatSidebarEnabled
        ? sidebarApi.collapsed
        : true;
      componentProps.chatSidebarBusy = searchTabChatSidebarBusy;
    } else if (componentName === 'AlertsComponent') {
      componentProps.alertsData = alertsData ?? undefined;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? alertsControlsReadyCallback : undefined;
      componentProps.registerChatAnswerHandler = registerAlertsTabChatAnswer;
      componentProps.registerSidebarChatEventSubscriber = registerAlertsTabSidebarChatEvents;
      componentProps.submitChatMessage = sidebarSubmitChatMessage;
    } else if (componentName === 'DashboardComponent' && dashboardData) {
      componentProps.dashboardData = dashboardData;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? dashboardControlsReadyCallback : undefined;
      componentProps.registerChatAnswerHandler = registerDashboardTabChatAnswer;
      componentProps.registerSidebarChatEventSubscriber = registerDashboardTabSidebarChatEvents;
    } else if (componentName === 'MapComponent' && mapData) {
      componentProps.mapData = mapData;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? mapControlsReadyCallback : undefined;
      componentProps.registerChatAnswerHandler = registerMapTabChatAnswer;
      componentProps.registerSidebarChatEventSubscriber = registerMapTabSidebarChatEvents;
    } else if (componentName === 'VideoManagementComponent' && videoManagementData) {
      componentProps.videoManagementData = videoManagementData;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? videoManagementControlsReadyCallback : undefined;
      componentProps.registerChatAnswerHandler = registerVideoManagementTabChatAnswer;
      componentProps.registerSidebarChatEventSubscriber = registerVideoManagementTabSidebarChatEvents;
      componentProps.registerChatVideoUploadComplete =
        registerMainTabChatVideoUploadComplete['video-management'];
      componentProps.addChatQueryContext = sidebarAddChatQueryContext;
    }

    return (
      <div
        key={tabConfig.id}
        className="absolute inset-0 flex min-h-0 min-w-0 flex-col overflow-hidden"
        style={{ display: isActive ? 'flex' : 'none' }}
      >
        <DynamicComponent {...componentProps} />
      </div>
    );
  };

  const renderMainAreaComponent = () => {
    if (visibleTabs.length === 0) {
      return (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">No Content Available</h2>
          <p className="text-gray-600 dark:text-gray-400">No tabs are enabled in the current deployment configuration.</p>
        </div>
      );
    }

    const tabStack = (
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {visibleTabs.map((tab) => renderTabComponent(tab))}
      </div>
    );

    const showFloatingChatSidebar =
      deploymentConfig.chatSidebarEnabled && activeTab !== 'chat';

    // Keep one layout root while changing tabs. Switching between the bare
    // stack and this wrapper remounts every tab, which discards a structured
    // search artifact delivered while the full-page Chat tab is active.
    return (
      <TabWithChatSidebarLayout
        tabId="side-bar"
        tabLabel="App"
        mainContent={tabStack}
        sidebarEnabled={showFloatingChatSidebar}
        sidebarApi={sidebarApi}
        highlightIcon={chatSidebarHighlight}
        queryExecuting={chatSidebarQueryExecuting}
        onOpenSidebar={clearChatSidebarHighlight}
        renderSidebarChat={renderAppSidebarChat}
        contentAreaRef={sidebarApi.contentAreaCallbackRef}
        isActive
      />
    );
  };

  return (
    <div className="h-screen flex flex-col bg-gray-50 dark:bg-black">
      {/* Top Header */}
      <header 
        className="relative z-30 shrink-0 bg-white dark:bg-black border-b border-gray-200 dark:border-gray-700" 
        style={{ 
          height: '75px',
          borderBottom: isDark 
            ? '1px solid rgba(75, 85, 99, 0.8)'
            : '1px solid rgba(156, 163, 175, 0.6)',
        }}
      >
        {/* Header content */}
        <div className="h-full px-6 flex items-center justify-between relative">
          <div className="flex items-center space-x-2 flex-1 min-w-0">
            {visibleTabs.length > 0 && (
              <button
                onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
                className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors flex-shrink-0"
                title={sidebarCollapsed ? 'Show sidebar' : 'Hide sidebar'}
              >
                <IconMenu2 size={20} />
              </button>
            )}
            <div className="flex items-center gap-2 p-2 flex-shrink-0 relative">
              {/* Render both logos, toggle visibility via CSS for instant switching */}
              <img 
                src="/NV-logo-white.svg"
                alt="NVIDIA Logo" 
                className={`h-5 w-auto transition-opacity duration-150 ${isDark ? 'opacity-100' : 'opacity-0 absolute'}`}
              />
              <img 
                src="/NV-logo-black.svg"
                alt="NVIDIA Logo" 
                className={`h-5 w-auto transition-opacity duration-150 ${isDark ? 'opacity-0 absolute' : 'opacity-100'}`}
              />
            </div>
            <div className="flex-shrink-0 w-[2px] h-[19px] bg-black dark:bg-white" />
            <h4
              className="font-bold text-gray-900 dark:text-gray-100 truncate text-xl font-sans"
              title={APPLICATION_TITLE}
            >
              {APPLICATION_TITLE}
            </h4>
            <span
              className="self-end flex-shrink-0 pb-0.5 text-xs text-gray-500 dark:text-gray-400"
              title={`ver: ${packageJson.version}`}
            >
              ver: {packageJson.version}
            </span>
            <div className="flex-shrink-0 w-[2px] h-[19px] bg-black dark:bg-white" />
            {APPLICATION_SUBTITLE && (
              <div className="flex items-center">
                <span className="text-sm text-black dark:text-white">
                  {APPLICATION_SUBTITLE}
                </span>
              </div>
            )}
          </div>
          
          <div className="flex items-center space-x-4 flex-shrink-0">
            {/* Theme toggle button */}
            <button 
              onClick={toggleTheme}
              className="p-2 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
              title={`Switch to ${isDark ? 'light' : 'dark'} theme`}
            >
              {isDark ? <IconSun size={24} /> : <IconMoon size={24} />}
            </button>
          </div>
        </div>
      </header>

      <div className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden">
        {/* Left Sidebar with Tabs - Only show if there are visible tabs */}
        {visibleTabs.length > 0 && (
          <aside 
            className={`bg-white dark:bg-neutral-900 border-r border-gray-300 dark:border-gray-600 flex flex-col shrink-0 overflow-hidden transition-all duration-300 ease-in-out ${sidebarCollapsed ? 'w-0 min-w-0 border-r-0' : ''}`}
            style={sidebarCollapsed ? { width: 0, minWidth: 0, maxWidth: 0 } : {
              width: '260px',
              minWidth: '260px', 
              maxWidth: '260px'
            }}
          >
            {/* Tab Navigation */}
            <nav data-testid="sidebar-nav" className="border-b border-gray-300 dark:border-gray-600 flex flex-col flex-shrink-0">
              <div className="px-2 pt-3 pb-2 flex-shrink-0">
              </div>
              <div 
                className="space-y-1 px-2 pb-4"
              >
                {visibleTabs.map((tab) => {
                  const isActive = activeTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      data-testid={`sidebar-tab-${tab.id}`}
                      onClick={() => setActiveTab(tab.id)}
                      title={tab.alt}
                      className={`
                        w-full flex items-center px-3 py-1.5 text-sm rounded
                        transition-all duration-200 ease-in-out
                        ${isActive
                          ? 'bg-neutral-300 dark:bg-neutral-700 text-neutral-900 dark:text-white hover:bg-neutral-400 dark:hover:bg-neutral-600 font-medium ring-1 ring-[#76b900]'
                          : 'text-neutral-700 dark:text-neutral-300 bg-neutral-100 dark:bg-neutral-900 hover:bg-neutral-300 dark:hover:bg-neutral-800'
                        }
                      `}
                    >
                      <span className={`mr-2 flex-shrink-0 ${isActive ? 'text-[#76b900]' : ''}`}>
                        {tab.icon}
                      </span>
                      <span className="text-left break-words hyphens-auto leading-tight">
                        {tab.label}
                      </span>
                    </button>
                  );
                })}
              </div>
            </nav>

            {/* Mode-Specific Controls Section */}
            <ModeControlsSection 
              chatHandlers={chatControlHandlers}
              alertsHandlers={alertsControlHandlers}
              searchHandlers={searchControlHandlers}
              dashboardHandlers={dashboardControlHandlers}
              mapHandlers={mapControlHandlers}
              videoManagementHandlers={videoManagementControlHandlers}
              activeTabLabel={visibleTabs.find(tab => tab.id === activeTab)?.label || ''}
            />
          </aside>
        )}

        {/* Main Content Area */}
        <main 
          className="relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden"
        >
          {renderMainAreaComponent()}
        </main>
      </div>
    </div>
  );
}
