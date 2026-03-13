// SPDX-License-Identifier: MIT
import React, { useState, useMemo, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { env } from 'next-runtime-env';
import type { ChatSidebarControlHandlers } from '@nemo-agent-toolkit/ui';
import { RuntimeConfigProvider } from '@nemo-agent-toolkit/ui';
import type { 
  AlertsSidebarControlHandlers,
  SearchSidebarControlHandlers,
  DashboardSidebarControlHandlers,
  MapSidebarControlHandlers,
  VideoManagementSidebarControlHandlers
} from '@nv-metropolis-bp-vss-ui/all';
import { 
  IconMessageCircle, 
  IconSearch, 
  IconAlertTriangle, 
  IconLayoutDashboard, 
  IconMapPin,
  IconVideo,
  IconSun,
  IconMoon
} from '@tabler/icons-react';
import { getTabChatInitialStateOverride, getTabChatWorkflow } from '../utils/tabChatEnv';
import {
  TAB_IDS_WITH_CHAT_SIDEBAR,
  getTabChatSidebarEnabled,
  getTabEnvKey,
  getTabStorageKeyPrefix,
} from '../utils/tabChatSidebarConfig';

import { useTheme } from '../hooks/useTheme';
import { useTabChatSidebars } from '../hooks/useTabChatSidebars';
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
const dynamicComponents = {
  NemoAgentToolkitApp: dynamic(() => 
    import('@nemo-agent-toolkit/ui').then(mod => mod.NemoAgentToolkitApp).catch((error) => {
      console.error('[DynamicImport] Failed to load NemoAgentToolkitApp:', error);
      return () => (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">Chat</h2>
          <p className="text-gray-600 dark:text-gray-400">
            NemoAgentToolkit component library not available. Please install @nemo-agent-toolkit/ui package.
          </p>
        </div>
      );
    }),
    { 
      ssr: true,
      loading: () => (
        <div className="flex-1 p-6 overflow-auto">
          <div className="flex items-center justify-center h-full">
            <p className="text-gray-600 dark:text-gray-400">Loading Chat...</p>
          </div>
        </div>
      )
    }
  ),
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
    const tabChatSidebarEnabled: Record<string, boolean> = {};
    TAB_IDS_WITH_CHAT_SIDEBAR.forEach((id) => {
      tabChatSidebarEnabled[id] = getTabChatSidebarEnabled(id);
    });
    return {
      enableChatTab: (env('NEXT_PUBLIC_ENABLE_CHAT_TAB') || process.env.NEXT_PUBLIC_ENABLE_CHAT_TAB) !== 'false',
      enableAlertsTab: (env('NEXT_PUBLIC_ENABLE_ALERTS_TAB') || process.env.NEXT_PUBLIC_ENABLE_ALERTS_TAB) !== 'false',
      enableSearchTab: (env('NEXT_PUBLIC_ENABLE_SEARCH_TAB') || process.env.NEXT_PUBLIC_ENABLE_SEARCH_TAB) !== 'false',
      enableDashboardTab: (env('NEXT_PUBLIC_ENABLE_DASHBOARD_TAB') || process.env.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB) !== 'false',
      enableMapTab: (env('NEXT_PUBLIC_ENABLE_MAP_TAB') || process.env.NEXT_PUBLIC_ENABLE_MAP_TAB) !== 'false',
      enableVideoManagementTab: (env('NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB') || process.env.NEXT_PUBLIC_ENABLE_VIDEO_MANAGEMENT_TAB) !== 'false',
      tabChatSidebarEnabled,
    };
  }, []); // Empty deps - env vars don't change during runtime

  // Define all possible tabs with their configuration - memoize to prevent recreation
  const allTabs: TabConfig[] = useMemo(() => [
    { 
      id: 'chat', 
      label: 'Chat', 
      icon: <IconMessageCircle size={18} />, 
      alt: 'Chat with Agent',
      enabled: deploymentConfig.enableChatTab,
      component: 'NemoAgentToolkitApp'
    },
    { 
      id: 'search', 
      label: 'Search', 
      icon: <IconSearch size={18} />, 
      alt: 'Search',
      enabled: deploymentConfig.enableSearchTab,
      component: 'SearchComponent'
    },
    { 
      id: 'alerts', 
      label: 'Alerts', 
      icon: <IconAlertTriangle size={18} />, 
      alt: 'Alerts List',
      enabled: deploymentConfig.enableAlertsTab,
      component: 'AlertsComponent'
    },
    { 
      id: 'dashboard', 
      label: 'Dashboard', 
      icon: <IconLayoutDashboard size={18} />, 
      alt: 'Dashboard',
      enabled: deploymentConfig.enableDashboardTab,
      component: 'DashboardComponent'
    },
    { 
      id: 'map', 
      label: 'Map', 
      icon: <IconMapPin size={18} />, 
      alt: 'Map',
      enabled: deploymentConfig.enableMapTab,
      component: 'MapComponent'
    },
    { 
      id: 'video-management', 
      label: 'Video Management', 
      icon: <IconVideo size={18} />, 
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

  // Shared sidebar state and resize logic for all non-Chat tabs (Search, Alerts, Dashboard, Map, Video Management)
  const getTabChatSidebar = useTabChatSidebars(TAB_IDS_WITH_CHAT_SIDEBAR);

  // When a new answer finishes in a tab's minimized chat, we set highlight so the floating icon pulses
  const [chatSidebarHighlight, setChatSidebarHighlight] = React.useState<
    Record<string, boolean>
  >({});

  // Search tab: Chat sidebar has submitted a message and response not yet complete (keeps search content disabled)
  const [searchTabChatSidebarBusy, setSearchTabChatSidebarBusy] = React.useState(false);

  // Per-tab chat sidebar: tab code can register to receive agent answers and can submit messages programmatically
  const chatSidebarHandlersRef = React.useRef<
    Record<string, { onAnswer?: (answer: string) => void; submitMessage?: (message: string) => void }>
  >({});

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

  // Update chat handlers when called - memoized handlers in Chatbar.tsx prevent excessive calls
  const chatControlsReadyCallback = React.useCallback((handlers: ChatSidebarControlHandlers) => {
    chatHandlersSetRef.current = true;
    setChatControlHandlers(handlers);
  }, []);

  const alertsControlsReadyCallback = React.useCallback((handlers: AlertsSidebarControlHandlers) => {
    if (!alertsHandlersSetRef.current) {
      alertsHandlersSetRef.current = true;
      setAlertsControlHandlers(handlers);
    }
  }, []);

  const searchControlsReadyCallback = React.useCallback((handlers: SearchSidebarControlHandlers) => {
    if (!searchHandlersSetRef.current) {
      searchHandlersSetRef.current = true;
      setSearchControlHandlers(handlers);
    }
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
    if (!videoManagementHandlersSetRef.current) {
      videoManagementHandlersSetRef.current = true;
      setVideoManagementControlHandlers(handlers);
    }
  }, []);

  // Clear mode controls when switching tabs
  React.useEffect(() => {
    if (activeTab !== 'chat') {
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

    // Main Chat tab: use default env (no RuntimeConfigProvider = getWorkflowName() reads NEXT_PUBLIC_WORKFLOW)
    if (componentName === 'NemoAgentToolkitApp') {
      return (
        <div 
          key={tabConfig.id}
          className="absolute inset-0 flex flex-col overflow-hidden"
          style={{ display: isActive ? 'flex' : 'none' }}
        >
          <div className="h-full w-full [&>main]:!h-full [&>main]:!w-full">
            <DynamicComponent 
              theme={theme}
              onThemeChange={handleThemeChange}
              isActive={isActive}
              renderControlsInLeftSidebar={true}
              renderApplicationHead={false}
              onControlsReady={(isActive ? chatControlsReadyCallback : undefined) as any}
            />
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
      componentProps.registerChatAnswerHandler = (handler: (answer: string) => void) => {
        if (!chatSidebarHandlersRef.current[tabConfig.id]) chatSidebarHandlersRef.current[tabConfig.id] = {};
        chatSidebarHandlersRef.current[tabConfig.id].onAnswer = handler;
      };
      componentProps.submitChatMessage = (message: string) => {
        chatSidebarHandlersRef.current[tabConfig.id]?.submitMessage?.(message);
      };
      componentProps.chatSidebarCollapsed = getTabChatSidebar(tabConfig.id).collapsed;
      componentProps.chatSidebarBusy = searchTabChatSidebarBusy;
    } else if (componentName === 'AlertsComponent') {
      componentProps.alertsData = alertsData ?? undefined;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? alertsControlsReadyCallback : undefined;
      componentProps.registerChatAnswerHandler = (handler: (answer: string) => void) => {
        if (!chatSidebarHandlersRef.current[tabConfig.id]) chatSidebarHandlersRef.current[tabConfig.id] = {};
        chatSidebarHandlersRef.current[tabConfig.id].onAnswer = handler;
      };
      componentProps.submitChatMessage = (message: string) => {
        chatSidebarHandlersRef.current[tabConfig.id]?.submitMessage?.(message);
      };
    } else if (componentName === 'DashboardComponent' && dashboardData) {
      componentProps.dashboardData = dashboardData;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? dashboardControlsReadyCallback : undefined;
    } else if (componentName === 'MapComponent' && mapData) {
      componentProps.mapData = mapData;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? mapControlsReadyCallback : undefined;
    } else if (componentName === 'VideoManagementComponent' && videoManagementData) {
      componentProps.videoManagementData = videoManagementData;
      componentProps.serverRenderTime = serverRenderTime;
      componentProps.renderControlsInLeftSidebar = true;
      componentProps.onControlsReady = isActive ? videoManagementControlsReadyCallback : undefined;
    }

    const hasChatSidebar = (TAB_IDS_WITH_CHAT_SIDEBAR as readonly string[]).includes(
      tabConfig.id,
    );
    if (hasChatSidebar) {
      const sidebarApi = getTabChatSidebar(tabConfig.id);
      const tabEnvKey = getTabEnvKey(tabConfig.id);
      const tabRuntimeConfig = {
        workflow: getTabChatWorkflow(tabEnvKey, `${tabConfig.label} Chat`),
        storageKeyPrefix: getTabStorageKeyPrefix(tabConfig.id),
      };
      const tabChatInitialStateOverride = getTabChatInitialStateOverride(tabEnvKey);
      const ChatApp = dynamicComponents.NemoAgentToolkitApp;
      return (
        <TabWithChatSidebarLayout
          tabId={tabConfig.id}
          tabLabel={tabConfig.label}
          mainContent={<DynamicComponent {...componentProps} />}
          sidebarEnabled={deploymentConfig.tabChatSidebarEnabled[tabConfig.id] ?? false}
          sidebarApi={sidebarApi}
          highlightIcon={chatSidebarHighlight[tabConfig.id] ?? false}
          onOpenSidebar={() =>
            setChatSidebarHighlight((prev) => ({ ...prev, [tabConfig.id]: false }))
          }
          renderSidebarChat={() => (
            <RuntimeConfigProvider value={tabRuntimeConfig}>
              <ChatApp
                theme={theme}
                onThemeChange={handleThemeChange}
                isActive={isActive}
                initialStateOverride={tabChatInitialStateOverride}
                storageKeyPrefix={tabRuntimeConfig.storageKeyPrefix}
                renderControlsInLeftSidebar={false}
                renderApplicationHead={false}
                onAnswerComplete={() => {
                  if (tabConfig.id === 'search') setSearchTabChatSidebarBusy(false);
                  const collapsed = getTabChatSidebar(tabConfig.id).collapsed;
                  setChatSidebarHighlight((prev) => ({ ...prev, [tabConfig.id]: collapsed }));
                }}
                onAnswerCompleteWithContent={(answer: string) => {
                  chatSidebarHandlersRef.current[tabConfig.id]?.onAnswer?.(answer);
                }}
                onSubmitMessageReady={(submitMessage: (message: string) => void) => {
                  if (!chatSidebarHandlersRef.current[tabConfig.id]) chatSidebarHandlersRef.current[tabConfig.id] = {};
                  chatSidebarHandlersRef.current[tabConfig.id].submitMessage = submitMessage;
                }}
                onMessageSubmitted={() => {
                  if (tabConfig.id === 'search') setSearchTabChatSidebarBusy(true);
                  const collapsed = getTabChatSidebar(tabConfig.id).collapsed;
                  setChatSidebarHighlight((prev) => ({ ...prev, [tabConfig.id]: collapsed }));
                }}
              />
            </RuntimeConfigProvider>
          )}
          contentAreaRef={sidebarApi.contentAreaCallbackRef}
          isActive={isActive}
        />
      );
    }

    return (
      <div
        key={tabConfig.id}
        className="absolute inset-0 flex flex-col overflow-hidden"
        style={{ display: isActive ? 'flex' : 'none' }}
      >
        <DynamicComponent {...componentProps} />
      </div>
    );
  };

  // Render all tab components (hidden or visible based on activeTab)
  const renderMainAreaComponent = () => {
    if (visibleTabs.length === 0) {
      return (
        <div className="flex-1 p-6 overflow-auto">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 mb-4">No Content Available</h2>
          <p className="text-gray-600 dark:text-gray-400">No tabs are enabled in the current deployment configuration.</p>
        </div>
      );
    }

    // Render all visible tabs in a relative container, but only show the active one
    // Using absolute positioning ensures they stack on top of each other
    return (
      <div className="relative flex-1 overflow-hidden">
        {visibleTabs.map(tab => renderTabComponent(tab))}
      </div>
    );
  };

  return (
    <div className="h-screen flex flex-col bg-gray-50 dark:bg-gray-900">
      {/* Top Header */}
      <header 
        className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 shadow-sm relative" 
        style={{ 
          height: '75px',
          boxShadow: '0 2px 4px rgba(0, 0, 0, 0.1)',
          borderBottom: isDark 
            ? '6px solid rgba(75, 85, 99, 0.6)'
            : '6px solid rgba(156, 163, 175, 0.4)',
        }}
      >
        {/* Blur effect pseudo-element */}
        <div 
          className="absolute inset-0 pointer-events-none"
          style={{
            boxShadow: isDark 
              ? 'inset 0 -6px 20px rgba(0, 0, 0, 0.4), inset 0 -6px 20px rgba(0, 0, 0, 0.3)'
              : 'inset 0 -6px 20px rgba(0, 0, 0, 0.15), inset 0 -6px 20px rgba(0, 0, 0, 0.1)',
            zIndex: 1
          }}
        />
        
        {/* Header content */}
        <div className="h-full px-6 flex items-center justify-between relative z-10">
          <div className="flex items-center space-x-2 flex-1 min-w-0">
            <div className="flex items-center gap-2 p-2 flex-shrink-0 relative">
              {/* Render both logos, toggle visibility via CSS for instant switching */}
              <img 
                src="/NV-logo-white.svg"
                alt="NVIDIA Logo" 
                className={`h-9 w-auto transition-opacity duration-150 ${isDark ? 'opacity-100' : 'opacity-0 absolute'}`}
              />
              <img 
                src="/NV-logo-black.svg"
                alt="NVIDIA Logo" 
                className={`h-9 w-auto transition-opacity duration-150 ${isDark ? 'opacity-0 absolute' : 'opacity-100'}`}
              />
            </div>
            <div className="flex-shrink-0 w-[2px] h-[19px] bg-black dark:bg-white" />
            <h4
              className="font-bold text-gray-900 dark:text-gray-100 truncate text-xl font-sans"
              title={APPLICATION_TITLE}
            >
              {APPLICATION_TITLE}
            </h4>
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

      <div className="flex flex-1 overflow-hidden relative">
        {/* Left Sidebar with Tabs - Only show if there are visible tabs */}
        {visibleTabs.length > 0 && (
          <aside 
            className="bg-white dark:bg-gray-800 border-r border-gray-300 dark:border-gray-600 flex flex-col shrink-0"
            style={{
              width: '260px',
              minWidth: '260px', 
              maxWidth: '260px'
            }}
          >
            {/* Tab Navigation */}
            <nav className="border-b border-gray-300 dark:border-gray-600 flex flex-col flex-shrink-0">
              <div className="px-4 pt-3 pb-2 flex-shrink-0">
                <h2 className="text-base font-normal text-gray-500 dark:text-gray-400 uppercase tracking-wider text-left">
                  
                </h2>
              </div>
              <div 
                className="space-y-1 px-2 pb-4"
              >
                {visibleTabs.map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id)}
                    title={tab.alt}
                    className={`
                      w-full flex items-center px-3 py-2 text-[14px] font-medium rounded-md
                      transition-all duration-200 ease-in-out border-r-4
                      ${activeTab === tab.id
                        ? 'bg-gray-300 dark:bg-gray-600 text-gray-900 dark:text-white border-gray-400 dark:border-gray-800 shadow-lg hover:bg-gray-400 dark:hover:bg-gray-700'
                        : 'text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 hover:shadow-md hover:scale-[1.02] border-transparent'
                      }
                    `}
                  >
                    <span className="mr-3 flex-shrink-0">
                      {tab.icon}
                    </span>
                    <span className="text-left break-words hyphens-auto leading-tight">
                      {tab.label}
                    </span>
                  </button>
                ))}
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
            
            {/* Version Display */}
            <div 
              className="px-4 border-t border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 flex items-end justify-center"
              style={{
                boxShadow: 'inset 0 8px 12px -2px rgba(0, 0, 0, 0.3)',
                height: '32px',
                paddingBottom: '4px'
              }}
            >
              <div className="text-xs text-gray-500 dark:text-gray-400 text-center">
                Version {packageJson.version}
              </div>
            </div>
          </aside>
        )}

        {/* Main Content Area */}
        <main 
          className="flex-1 flex flex-col overflow-hidden"
          style={{
            boxShadow: isDark 
              ? '-6px -6px 20px rgba(0, 0, 0, 0.4), 0 -6px 20px rgba(0, 0, 0, 0.3)'
              : '-6px -6px 20px rgba(0, 0, 0, 0.15), 0 -6px 20px rgba(0, 0, 0, 0.1)',
            borderLeft: visibleTabs.length > 0 ? (isDark 
              ? '6px solid rgba(75, 85, 99, 0.6)'
              : '6px solid rgba(156, 163, 175, 0.4)') : 'none',
            borderTop: isDark 
              ? '6px solid rgba(75, 85, 99, 0.6)'
              : '6px solid rgba(156, 163, 175, 0.4)',
            filter: isDark 
              ? 'drop-shadow(-2px -2px 8px rgba(0, 0, 0, 0.3))'
              : 'drop-shadow(-2px -2px 8px rgba(0, 0, 0, 0.1))',
            position: 'relative'
          }}
        >
          {renderMainAreaComponent()}
        </main>
      </div>
    </div>
  );
}
