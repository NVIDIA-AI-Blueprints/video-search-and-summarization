// SPDX-License-Identifier: MIT
/**
 * @fileoverview DashboardComponent - Kibana Dashboard Integration and Embedding
 * 
 * This file contains the DashboardComponent which provides a robust, production-ready solution
 * for embedding Kibana dashboards and other analytics platforms into React applications. The
 * component offers comprehensive iframe management, state handling, error recovery, and security
 * features for seamless dashboard integration with enterprise-grade reliability and performance.
 * 
 * **Primary Purpose:**
 * The DashboardComponent serves as a secure, configurable wrapper for embedding external dashboard
 * solutions (primarily Kibana) into the application. It abstracts away the complexity of iframe
 * management, provides consistent user experience through loading and error states, and ensures
 * proper security controls through sandbox attributes and CSP compliance.
 * 
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { DashboardSidebarControls } from './components/DashboardSidebarControls';

export interface SavedDashboard {
  id: string;
  attributes: {
    title: string;
    description?: string;
  };
}

interface DashboardData {
  kibanaBaseUrl?: string | null;
  dashboards?: SavedDashboard[];
}

export interface DashboardSidebarControlHandlers {
  controlsComponent: React.ReactNode;
}

export interface DashboardComponentProps {
  theme?: 'light' | 'dark';
  // Optional SSR data
  dashboardData?: DashboardData | null;
  // Optional props
  className?: string;
  style?: React.CSSProperties;
  // External sidebar rendering
  renderControlsInLeftSidebar?: boolean;
  onControlsReady?: (handlers: DashboardSidebarControlHandlers) => void;
  // Visibility control for lazy loading iframes
  isActive?: boolean;
}

export const DashboardComponent: React.FC<DashboardComponentProps> = ({ 
  theme = 'light', 
  dashboardData,
  className = '',
  style = {},
  renderControlsInLeftSidebar = false,
  onControlsReady,
  isActive = true,
}) => {
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  // Track if iframe has ever been loaded (for lazy loading)
  const [hasLoadedOnce, setHasLoadedOnce] = useState(isActive);
  
  // Key to force iframe refresh when navigating back to dashboard
  const [iframeKey, setIframeKey] = useState(0);
  
  // Track previous isActive state to detect when user comes back (useRef to avoid re-render)
  const wasActiveRef = useRef(isActive);
  
  // State for selected dashboard
  const [selectedDashboardId, setSelectedDashboardId] = useState<string | null>(null);

  // Get data from server-side props
  const kibanaBaseUrl = dashboardData?.kibanaBaseUrl || '';
  const dashboards = dashboardData?.dashboards || [];

  // Auto-select first dashboard when dashboards are loaded
  useEffect(() => {
    if (dashboards.length > 0 && !selectedDashboardId) {
      setSelectedDashboardId(dashboards[0].id);
    }
  }, [dashboards, selectedDashboardId]);

  // Generate the dashboard URL based on selection
  const getDashboardEmbedUrl = useCallback((): string | null => {
    if (!kibanaBaseUrl) return null;

    // Remove trailing slash if present
    const baseUrl = kibanaBaseUrl.replace(/\/$/, '');

    if (dashboards.length === 0) {
      // No dashboards available, show default dashboards page
      return `${baseUrl}/app/dashboards`;
    }

    if (selectedDashboardId) {
      // Embed the selected dashboard
      return `${baseUrl}/app/dashboards#/view/${selectedDashboardId}`;
    }

    // Fallback to default dashboards page
    return `${baseUrl}/app/dashboards`;
  }, [kibanaBaseUrl, dashboards.length, selectedDashboardId]);

  // When component becomes active, load/refresh the iframe
  useEffect(() => {
    const wasActive = wasActiveRef.current;
    
    if (isActive && !hasLoadedOnce) {
      // First time activation - just load
      setHasLoadedOnce(true);
    } else if (isActive && !wasActive && hasLoadedOnce) {
      // Coming back to dashboard - refresh iframe
      setIsLoading(true);
      setError(null);
      setIframeKey(prev => prev + 1);
    }
    
    wasActiveRef.current = isActive;
  }, [isActive, hasLoadedOnce]);

  // Memoize the controls component to prevent unnecessary re-renders
  const controlsComponent = React.useMemo(
    () => <DashboardSidebarControls />,
    []
  );

  // Provide controls to external sidebar if requested
  React.useEffect(() => {
    if (onControlsReady && renderControlsInLeftSidebar) {
      onControlsReady({
        controlsComponent,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onControlsReady, renderControlsInLeftSidebar]);

  // Theme colors
  const bgColor = theme === 'dark' ? 'bg-[#1a1a1a]' : 'bg-white';
  const textColor = theme === 'dark' ? 'text-gray-200' : 'text-gray-800';

  // Sanitize URL by removing quotes and validating format
  const sanitizeUrl = (url: string | undefined): string | null => {
    if (!url) return null;
    
    // Remove leading/trailing quotes and whitespace
    let sanitized = url.trim().replace(/^["']|["']$/g, '');
    
    // Validate URL format
    try {
      const urlObj = new URL(sanitized);
      return urlObj.href;
    } catch {
      // If URL is invalid, return null
      return null;
    }
  };

  // Get the current embed URL
  const currentEmbedUrl = getDashboardEmbedUrl();
  const sanitizedUrl = sanitizeUrl(currentEmbedUrl ?? undefined);

  const handleIframeLoad = () => {
    setIsLoading(false);
  };

  const handleIframeError = () => {
    setError('Failed to load dashboard. Please check the URL and network connection.');
    setIsLoading(false);
  };

  const handleDashboardChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    const newDashboardId = event.target.value;
    setSelectedDashboardId(newDashboardId);
    setIsLoading(true);
    setError(null);
  };

  useEffect(() => {
    // Reset loading state when URL changes
    setIsLoading(true);
    setError(null);
    
    // Check if kibanaBaseUrl is empty or null
    if (!kibanaBaseUrl || kibanaBaseUrl.trim() === '') {
      setError('Kibana base URL is not configured. Please provide a valid Kibana base URL.');
      setIsLoading(false);
      return;
    }
    
    // Validate sanitized URL
    if (!sanitizedUrl) {
      setError('Dashboard URL is invalid. Please check the URL format.');
      setIsLoading(false);
      return;
    }

    // Set a timeout to force show the iframe if onLoad doesn't fire
    // This handles cases where X-Frame-Options blocks the iframe but the content still loads
    const loadTimeout = setTimeout(() => {
      console.warn('Dashboard iframe onLoad event did not fire within 3 seconds. This may indicate X-Frame-Options or CSP blocking. Showing iframe anyway.');
      setIsLoading(false);
    }, 3000);

    return () => clearTimeout(loadTimeout);
  }, [kibanaBaseUrl, sanitizedUrl, selectedDashboardId]);

  return (
    <div 
      className={`h-full w-full flex flex-col overflow-hidden ${bgColor} ${className}`}
      style={style}
    >
      {/* Dashboard Filter Bar - Only show when multiple dashboards available */}
      {dashboards.length > 1 && (
        <div className={`w-full px-4 py-3 border-b flex items-center gap-3 shrink-0 ${
          theme === 'dark' 
            ? 'bg-[#252525] border-gray-700' 
            : 'bg-gray-50 border-gray-200'
        }`}>
          <label className={`text-sm font-medium ${
            theme === 'dark' ? 'text-gray-300' : 'text-gray-600'
          }`}>
            Dashboard
          </label>
          <select
            value={selectedDashboardId || ''}
            onChange={handleDashboardChange}
            className={`px-3 py-1.5 rounded-md border text-sm transition-colors cursor-pointer min-w-[240px] ${
              theme === 'dark'
                ? 'bg-gray-800 border-gray-600 text-gray-200 hover:border-gray-500 focus:border-blue-500'
                : 'bg-white border-gray-300 text-gray-800 hover:border-gray-400 focus:border-blue-500'
            } focus:outline-none focus:ring-2 focus:ring-blue-500/20`}
          >
            {dashboards.map((dashboard) => (
              <option key={dashboard.id} value={dashboard.id}>
                {dashboard.attributes.title}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Content Area */}
      <div className="flex-1 relative overflow-hidden">
        {/* Loading State */}
        {isLoading && (
          <div className={`absolute inset-0 flex items-center justify-center ${bgColor}`} style={{ zIndex: 10 }}>
            <div className="text-center">
              <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500"></div>
              <p className={`mt-4 ${theme === 'dark' ? 'text-gray-400' : 'text-gray-600'}`}>
                Loading dashboard...
              </p>
            </div>
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className={`absolute inset-0 flex items-center justify-center ${bgColor}`} style={{ zIndex: 10 }}>
            <div className="text-center max-w-md px-6">
              <div className="text-6xl mb-4">⚠️</div>
              <h3 className={`text-lg font-semibold mb-2 ${textColor}`}>
                Dashboard Load Error
              </h3>
              <div className="max-h-24 overflow-auto rounded p-3 break-words whitespace-pre-wrap bg-black/5 dark:bg-white/5 mb-4">
                <p className={`${theme === 'dark' ? 'text-gray-400' : 'text-gray-600'}`}>
                  {error}
                </p>
              </div>
              <button
                onClick={() => {
                  setError(null);
                  setIsLoading(true);
                  setIframeKey(prev => prev + 1); // Force iframe to reload
                }}
                className={`px-4 py-2 rounded-lg transition-colors ${
                  theme === 'dark'
                    ? 'bg-blue-600 hover:bg-blue-700 text-white'
                    : 'bg-blue-500 hover:bg-blue-600 text-white'
                }`}
              >
                Retry
              </button>
            </div>
          </div>
        )}

        {/* Kibana Dashboard Iframe */}
        {/* Note: Using sandbox with allow-scripts and allow-same-origin together can allow 
            iframe content to remove the sandbox attribute. This is acceptable ONLY when 
            the iframe src is from a trusted source. Ensure KIBANA_DASHBOARD_URL points 
            to a trusted, secure Kibana instance. */}
        {/* Lazy loading: Only render iframe once the tab has been activated */}
        {!error && sanitizedUrl && hasLoadedOnce && (
          <iframe
            key={iframeKey}
            src={sanitizedUrl}
            title="Kibana Dashboard"
            className="absolute inset-0 w-full h-full border-0"
            onLoad={handleIframeLoad}
            onError={handleIframeError}
            sandbox="allow-same-origin allow-scripts allow-popups allow-forms allow-downloads"
            allow="fullscreen"
            referrerPolicy="no-referrer-when-downgrade"
            style={{
              display: isLoading ? 'none' : 'block'
            }}
          />
        )}
      </div>
    </div>
  );
};
