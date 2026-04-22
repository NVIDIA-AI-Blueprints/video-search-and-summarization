// SPDX-License-Identifier: MIT
/**
 * @fileoverview MapComponent - Map Integration and Embedding
 * 
 * This file contains the MapComponent which provides a robust, production-ready solution
 * for embedding map applications into React applications. The component offers 
 * comprehensive iframe management, state handling, error recovery, and security features 
 * for seamless integration with enterprise-grade reliability and performance.
 * 
 * **Primary Purpose:**
 * The MapComponent serves as a secure, configurable wrapper for embedding external map
 * applications into the application. It abstracts away the complexity of iframe
 * management, provides consistent user experience through loading and error states, and ensures
 * proper security controls through sandbox attributes and CSP compliance.
 * 
 */

import React, { useEffect, useState } from 'react';
import { MapSidebarControls } from './components/MapSidebarControls';

interface MapData {
  mapUrl?: string;
}

export interface MapSidebarControlHandlers {
  controlsComponent: React.ReactNode;
}

export interface MapComponentProps {
  theme?: 'light' | 'dark';
  // Optional SSR data
  mapData?: MapData | null;
  // Optional props
  className?: string;
  style?: React.CSSProperties;
  // External sidebar rendering
  renderControlsInLeftSidebar?: boolean;
  onControlsReady?: (handlers: MapSidebarControlHandlers) => void;
  // Visibility control for lazy loading iframes
  isActive?: boolean;
  registerChatAnswerHandler?: (handler: (answer: string) => boolean | void) => void | (() => void);
  registerSidebarChatEventSubscriber?: (
    handler: (event: { type: 'messageSubmitted' } | { type: 'answerComplete' }) => void
  ) => void | (() => void);
}

export const MapComponent: React.FC<MapComponentProps> = ({ 
  theme = 'light', 
  mapData,
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
  
  // When component becomes active for the first time, load the iframe
  useEffect(() => {
    if (isActive && !hasLoadedOnce) {
      setHasLoadedOnce(true);
    }
  }, [isActive, hasLoadedOnce]);

  // Memoize the controls component to prevent unnecessary re-renders
  const controlsComponent = React.useMemo(
    () => <MapSidebarControls />,
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
  const bgColor = theme === 'dark' ? 'bg-black' : 'bg-white';
  const textColor = theme === 'dark' ? 'text-gray-200' : 'text-gray-800';

  // Sanitize URL by removing quotes and validating format
  const sanitizeUrl = (url: string | undefined): string | null => {
    if (!url) return null;
    
    // Remove leading/trailing quotes and whitespace
    let sanitized = url.trim().replaceAll(/^["']|["']$/g, '');
    
    // Validate URL format
    try {
      const urlObj = new URL(sanitized);
      return urlObj.href;
    } catch {
      // If URL is invalid, return null
      return null;
    }
  };

  const sanitizedUrl = sanitizeUrl(mapData?.mapUrl);

  const handleIframeLoad = () => {
    setIsLoading(false);
  };

  const handleIframeError = () => {
    setError('Failed to load map. Please check the URL and network connection.');
    setIsLoading(false);
  };

  useEffect(() => {
    // Reset loading state when URL changes
    setIsLoading(true);
    setError(null);
    
    // Check if mapUrl is empty or null
    if (!mapData?.mapUrl || mapData.mapUrl.trim() === '') {
      setError('Deployment time variables not correctly configured for this mode. Set URL related environment variables.');
      setIsLoading(false);
      return;
    }
    
    // Validate sanitized URL
    if (!sanitizedUrl) {
      setError('Map URL is invalid. Please check the URL format.');
      setIsLoading(false);
    }
  }, [mapData?.mapUrl, sanitizedUrl]);

  return (
    <div 
      className={`h-full w-full relative overflow-hidden ${bgColor} ${className}`}
      style={style}
    >
      {/* Loading State */}
      {isLoading && (
        <div className={`absolute inset-0 flex items-center justify-center ${bgColor}`}>
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-green-500"></div>
            <p className={`mt-4 ${theme === 'dark' ? 'text-gray-400' : 'text-gray-600'}`}>
              Loading map...
            </p>
          </div>
        </div>
      )}

      {/* Error State */}
      {error && (
        <div className={`absolute inset-0 flex items-center justify-center ${bgColor}`}>
          <div className="text-center max-w-md px-6">
            <div className="text-6xl mb-4">⚠️</div>
            <h3 className={`text-lg font-semibold mb-2 ${textColor}`}>
              Load Error
            </h3>
            <div className="max-h-24 overflow-auto rounded p-3 break-words whitespace-pre-wrap bg-black/5 dark:bg-white/5">
              <p className={`${theme === 'dark' ? 'text-gray-400' : 'text-gray-600'}`}>
                {error}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Map Iframe */}
      {/* Note: Using sandbox with allow-scripts and allow-same-origin together can allow 
          iframe content to remove the sandbox attribute. This is acceptable ONLY when 
          the iframe src is from a trusted source. Ensure MAP_URL points 
          to a trusted, secure map instance. */}
      {/* Lazy loading: Only render iframe once the tab has been activated */}
      {!error && sanitizedUrl && hasLoadedOnce && (
        <iframe
          src={sanitizedUrl}
          title="Map"
          className="absolute inset-0 w-full h-full border-0"
          onLoad={handleIframeLoad}
          onError={handleIframeError}
          sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
          allow="fullscreen"
          style={{
            display: isLoading ? 'none' : 'block'
          }}
        />
      )}
    </div>
  );
};

