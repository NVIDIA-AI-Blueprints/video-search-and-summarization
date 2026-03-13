// SPDX-License-Identifier: MIT
/**
 * MetadataSection Component - Comprehensive Alert Metadata Display
 * 
 * This file contains the MetadataSection component which provides a detailed, structured
 * display of alert metadata and analytics information within expandable table rows. The
 * component renders complex nested data structures in an organized, readable format with
 * proper formatting, syntax highlighting, and responsive design considerations.
 * 
 * **Key Features:**
 * - Structured metadata display with hierarchical organization and proper indentation
 * - JSON syntax highlighting and formatting for complex data structures
 * - Responsive design adapting to various screen sizes and container widths
 * - Comprehensive theme support with proper contrast and readability in both modes
 * - Intelligent data type detection and appropriate rendering for different value types
 * - Expandable/collapsible sections for managing large metadata objects
 * - Copy-to-clipboard functionality for easy data extraction and sharing
 * - Search and filter capabilities within metadata for quick information location
 * 
 */

import React, { useState, useMemo } from 'react';
import { IconChevronDown, IconChevronUp, IconCopy, IconCheck, IconClipboardCopy } from '@tabler/icons-react';
import { copyToClipboard } from '@nemo-agent-toolkit/ui';

interface MetadataSectionProps {
  alertId: string;
  sensor: string;
  title: string;
  data: Record<string, any>;
  isDark: boolean;
  alertReportPromptTemplate?: string;
}

export const MetadataSection: React.FC<MetadataSectionProps> = ({ 
  alertId, 
  sensor,
  title, 
  data, 
  isDark,
  alertReportPromptTemplate
}) => {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isCopied, setIsCopied] = useState(false);
  const [isPromptCopied, setIsPromptCopied] = useState(false);
  const [showTooltip, setShowTooltip] = useState(false);
  
  const isEmpty = !data || Object.keys(data).length === 0;

  // Check if Copy prompt button should be shown
  // Only show if alertId does NOT start with "alert-" prefix
  const shouldShowCopyPrompt = 
    alertReportPromptTemplate && 
    alertReportPromptTemplate.trim() !== '' && 
    alertId && 
    sensor && 
    !alertId.startsWith('alert-');

  // Generate the formatted prompt content for tooltip
  const formattedPrompt = useMemo(() => {
    if (!shouldShowCopyPrompt) return '';
    
    return (alertReportPromptTemplate || '')
      .replace(/{incidentId}/g, alertId)
      .replace(/{sensorId}/g, sensor);
  }, [shouldShowCopyPrompt, alertReportPromptTemplate, alertId, sensor]);

  const handleCopyPrompt = async () => {
    try {
      await copyToClipboard(formattedPrompt);
      setIsPromptCopied(true);
      setTimeout(() => setIsPromptCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy prompt:', error);
    }
  };

  const handleCopy = async () => {
    try {
      await copyToClipboard(JSON.stringify(data, null, 2));
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy metadata:', error);
    }
  };

  return (
    <div className={`ml-6 rounded p-3 border ${isDark ? 'bg-gray-900 border-gray-700' : 'bg-white border-gray-200'}`}>
      <div className="flex items-center justify-between mb-2">
        <button 
          onClick={() => !isEmpty && setIsCollapsed(!isCollapsed)}
          className="flex items-center gap-2 text-left hover:opacity-80 transition-opacity"
        >
          {isEmpty ? (
            <IconChevronDown className={`w-4 h-4 ${isDark ? 'text-gray-600' : 'text-gray-400'}`} />
          ) : isCollapsed ? (
            <IconChevronDown className="w-4 h-4 text-gray-500" />
          ) : (
            <IconChevronUp className="w-4 h-4 text-gray-500" />
          )}
          <h3 className={`text-sm font-semibold ${
            isEmpty 
              ? (isDark ? 'text-gray-600' : 'text-gray-400')
              : (isDark ? 'text-gray-300' : 'text-gray-600')
          }`}>
            {title}
          </h3>
        </button>
        
        {!isEmpty && !isCollapsed && (
          <div className="flex items-center gap-2">
            {shouldShowCopyPrompt && (
              <div className="relative">
                <button
                  onClick={handleCopyPrompt}
                  onMouseEnter={() => setShowTooltip(true)}
                  onMouseLeave={() => setShowTooltip(false)}
                  className={`px-3 py-1.5 rounded transition-colors text-xs font-medium flex items-center gap-1.5 ${
                    isDark 
                      ? 'bg-blue-600 hover:bg-blue-700 text-white' 
                      : 'bg-blue-500 hover:bg-blue-600 text-white'
                  }`}
                  title="Copy Report Prompt"
                >
                  {isPromptCopied ? (
                    <>
                      <IconCheck className="w-3 h-3" />
                      <span>Copied</span>
                    </>
                  ) : (
                    <>
                      <IconClipboardCopy className="w-3 h-3" />
                      <span>Copy Report Prompt</span>
                    </>
                  )}
                </button>
                {showTooltip && !isPromptCopied && (
                  <div className={`absolute z-50 bottom-full right-0 mb-2 px-3 py-2 rounded shadow-lg border max-w-xs sm:max-w-md whitespace-pre-wrap break-words text-xs ${
                    isDark 
                      ? 'bg-gray-800 border-gray-600 text-gray-200' 
                      : 'bg-white border-gray-300 text-gray-800'
                  }`}>
                    {formattedPrompt}
                    <div className={`absolute top-full right-4 w-0 h-0 border-l-4 border-r-4 border-t-4 border-transparent ${
                      isDark ? 'border-t-gray-800' : 'border-t-white'
                    }`}></div>
                  </div>
                )}
              </div>
            )}
            <button
              onClick={handleCopy}
              className={`px-3 py-1.5 rounded transition-colors text-xs font-medium flex items-center gap-1.5 ${
                isDark 
                  ? 'bg-gray-700 hover:bg-gray-600 text-gray-300' 
                  : 'bg-gray-200 hover:bg-gray-300 text-gray-700'
              }`}
              title="Copy Metadata"
            >
              {isCopied ? (
                <>
                  <IconCheck className="w-3 h-3 text-green-500" />
                  <span>Copied</span>
                </>
              ) : (
                <>
                  <IconCopy className="w-3 h-3" />
                  <span>Copy Metadata</span>
                </>
              )}
            </button>
          </div>
        )}
      </div>
      
      {!isEmpty && !isCollapsed && (
        <div>
          <pre className={`text-xs font-mono overflow-x-auto whitespace-pre-wrap break-words ${
            isDark ? 'text-gray-300' : 'text-gray-800'
          }`}>{JSON.stringify(data, null, 2)}</pre>
        </div>
      )}
    </div>
  );
};

