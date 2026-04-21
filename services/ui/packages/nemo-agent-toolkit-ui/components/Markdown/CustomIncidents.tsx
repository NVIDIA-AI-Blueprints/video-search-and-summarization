/**
 * CustomIncidents Component
 * 
 * This component renders alerts generated from compatible NVIDIA Metropolis Services.
 * Input data is received from NeMo Agent Toolkit workflows and must be wrapped in <incidents> tags
 * with compatible data structure containing incident information, video clips, and metadata...
 * 
 */

import React, { memo, useState, useMemo } from 'react';
import { IconChevronDown, IconChevronUp, IconPlayerPlay, IconCopy } from '@tabler/icons-react';
import { VideoModal } from './VideoModal';
import { copyToClipboard } from '../../utils/shared/clipboard';

// Constants
const INITIAL_VISIBLE_COUNT = 3;
const INCREMENT_COUNT = 3;

interface CVMetadata {
  Box_on_floor?: string;
  Number_of_people?: string;
  PPE?: string;
}

interface ClipInformation {
  Timestamp: string;
  Stream: string;
  Alerts: string;
  snapshot_url?: string;
  video_url?: string;
  'CV Metadata': CVMetadata;
}

interface AlertDetails {
  'Alert Triggered': string;
  Validation: boolean;
  'Alert Description': string;
}

interface Incident {
  'Alert Title': string;
  'Clip Information': ClipInformation;
  'Alert Details': AlertDetails;
}

interface IncidentsData {
  incidents: Incident[];
  message?: string;
}

interface CustomIncidentsProps {
  payload?: IncidentsData;
  [key: string]: any;
}

const formatTimestamp = (timestamp: string): string => {
  try {
    const date = new Date(timestamp);
    return date.toLocaleString('en-US', {
      month: '2-digit',
      day: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    });
  } catch {
    return timestamp;
  }
};

export const CustomIncidents = memo<CustomIncidentsProps>(
  ({ payload, ...props }) => {
    const [expandedItem, setExpandedItem] = useState<number | null>(null);
    const [expandedClipInfo, setExpandedClipInfo] = useState<number | null>(null);
    const [expandedAlertDetails, setExpandedAlertDetails] = useState<number | null>(null);
    const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_COUNT);
    const [videoModal, setVideoModal] = useState<{ isOpen: boolean; videoUrl: string; title: string }>({
      isOpen: false,
      videoUrl: '',
      title: ''
    });

    // Parse incidents data and message
    const { incidents, message } = useMemo(() => {
      if (!payload || !payload.incidents) {
        return { incidents: [], message: '' };
      }

      const incidentsArray = Array.isArray(payload.incidents) ? payload.incidents : [];
      const messageText = typeof payload.message === 'string' ? payload.message.trim() : '';

      return { incidents: incidentsArray, message: messageText };
    }, [payload]);

    const toggleExpanded = (index: number) => {
      if (expandedItem === index) {
        // If clicking on already expanded item, collapse it
        setExpandedItem(null);
        // Also collapse sub-sections
        setExpandedClipInfo(null);
        setExpandedAlertDetails(null);
      } else {
        // Expand new item and collapse sub-sections
        setExpandedItem(index);
        setExpandedClipInfo(null);
        setExpandedAlertDetails(null);
      }
    };

    const toggleClipInfo = (index: number) => {
      if (expandedClipInfo === index) {
        setExpandedClipInfo(null);
      } else {
        setExpandedClipInfo(index);
      }
    };

    const toggleAlertDetails = (index: number) => {
      if (expandedAlertDetails === index) {
        setExpandedAlertDetails(null);
      } else {
        setExpandedAlertDetails(index);
      }
    };

    const handleViewMore = () => {
      setVisibleCount(prev => Math.min(prev + INCREMENT_COUNT, incidents.length));
    };

    const handleViewLess = () => {
      setVisibleCount(INITIAL_VISIBLE_COUNT);
    };

    const openVideoModal = (videoUrl: string, title: string) => {
      setVideoModal({
        isOpen: true,
        videoUrl,
        title
      });
    };

    const closeVideoModal = () => {
      setVideoModal({
        isOpen: false,
        videoUrl: '',
        title: ''
      });
    };


    if (incidents.length === 0) {
      return <div className="text-gray-500 dark:text-gray-400">No incidents found</div>;
    }

    // Show incidents based on visibleCount
    const incidentsToShow = incidents.slice(0, visibleCount);
    const hasMoreItems = visibleCount < incidents.length;
    const canShowLess = visibleCount > INITIAL_VISIBLE_COUNT;

    return (
      <div className="incidents-container space-y-2 text-sm">
        {message && (
          <div className="flex items-start mb-6">
            <div className="w-5 h-5 mr-3 mt-0.5 bg-green-600 rounded-sm flex items-center justify-center">
              <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
              </svg>
            </div>
            <span className="text-gray-800 dark:text-gray-100">
              {message}
            </span>
          </div>
        )}
        
        {/* Incidents List */}
        {incidentsToShow.map((incident, index) => {
          const isExpanded = expandedItem === index;
          const isClipInfoExpanded = expandedClipInfo === index;
          const isAlertDetailsExpanded = expandedAlertDetails === index;
          const alertNumber = index + 1;
          const timestamp = formatTimestamp(incident['Clip Information'].Timestamp);

          return (
            <div key={index} className="rounded-lg overflow-hidden bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 transition-all hover:shadow-md border border-gray-200 dark:border-gray-600">
              {/* Alert Header */}
              <div 
                className="flex items-center justify-between p-2 cursor-pointer transition-colors"
                onClick={() => toggleExpanded(index)}
              >
                <div className="flex items-center space-x-3">
                  {isExpanded ? (
                    <IconChevronUp className="w-4 h-4 text-gray-600 dark:text-gray-400" />
                  ) : (
                    <IconChevronDown className="w-4 h-4 text-gray-600 dark:text-gray-400" />
                  )}
                  <div className="text-gray-900 dark:text-white text-sm">
                    <span className="font-bold">Alert Triggered {alertNumber}:</span> {incident['Alert Details']['Alert Triggered']} at {timestamp}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button 
                    className="flex items-center justify-center w-8 h-8 bg-gray-100 dark:bg-gray-700 rounded-md hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors group border border-gray-200 dark:border-gray-500"
                    onClick={(e) => {
                      e.stopPropagation();
                      const videoUrl = incident['Clip Information'].video_url;
                      const title = `Alert Triggered ${alertNumber}: ${incident['Alert Details']['Alert Triggered']}`;
                      if (videoUrl) {
                        openVideoModal(videoUrl, title);
                      }
                    }}
                  >
                    <IconPlayerPlay className="w-4 h-4 text-gray-600 dark:text-gray-300 group-hover:text-gray-800 dark:group-hover:text-white transition-colors" />
                  </button>
                </div>
              </div>

              {/* Expanded Content */}
              {isExpanded && (
                <div className="bg-gray-50 dark:bg-gray-700 rounded-lg ml-8 mb-3 mr-3 border border-gray-200 dark:border-gray-600">
                  {/* Clip Information */}
                  <div 
                    className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors rounded-t-lg"
                    onClick={(e) => {
                      e.preventDefault();
                      toggleClipInfo(index);
                    }}
                  >
                    <div className="flex items-center space-x-2">
                      {isClipInfoExpanded ? (
                        <IconChevronUp className="w-4 h-4 text-gray-600 dark:text-gray-400" />
                      ) : (
                        <IconChevronDown className="w-4 h-4 text-gray-600 dark:text-gray-400" />
                      )}
                      <h4 className="font-medium text-gray-900 dark:text-white text-sm m-0">Clip Information</h4>
                    </div>
                    <div className="flex items-center space-x-2">
                      <button 
                        className="flex items-center justify-center w-6 h-6 bg-gray-200 dark:bg-gray-600 rounded hover:bg-gray-300 dark:hover:bg-gray-500 transition-colors group border border-gray-300 dark:border-gray-500"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          copyToClipboard(incident['Clip Information']);
                        }}
                        title="Copy Clip Information JSON"
                      >
                        <IconCopy className="w-3 h-3 text-gray-600 dark:text-gray-300 group-hover:text-gray-800 dark:group-hover:text-white transition-colors" />
                      </button>
                    </div>
                  </div>
                  {isClipInfoExpanded && (
                    <div className="px-4 pb-4 pt-2">
                      <div className="bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-100 p-4 rounded-md font-mono text-xs whitespace-pre-wrap">
                        {JSON.stringify(incident['Clip Information'], null, 2)}
                      </div>
                    </div>
                  )}

                  {/* Alert Details */}
                  <div 
                    className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors rounded-t-lg"
                    onClick={(e) => {
                      e.preventDefault();
                      toggleAlertDetails(index);
                    }}
                  >
                    <div className="flex items-center space-x-2">
                      {isAlertDetailsExpanded ? (
                        <IconChevronUp className="w-4 h-4 text-gray-600 dark:text-gray-400" />
                      ) : (
                        <IconChevronDown className="w-4 h-4 text-gray-600 dark:text-gray-400" />
                      )}
                      <h4 className="font-medium text-gray-900 dark:text-white text-sm m-0">Alert Details</h4>
                    </div>
                    <div className="flex items-center space-x-2">
                      <button 
                        className="flex items-center justify-center w-6 h-6 bg-gray-200 dark:bg-gray-600 rounded hover:bg-gray-300 dark:hover:bg-gray-500 transition-colors group border border-gray-300 dark:border-gray-500"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          copyToClipboard(incident['Alert Details']);
                        }}
                        title="Copy Alert Details JSON"
                      >
                        <IconCopy className="w-3 h-3 text-gray-600 dark:text-gray-300 group-hover:text-gray-800 dark:group-hover:text-white transition-colors" />
                      </button>
                    </div>
                  </div>
                  {isAlertDetailsExpanded && (
                    <div className="px-4 pb-4 pt-2">
                      <div className="bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-100 p-4 rounded-md font-mono text-xs whitespace-pre-wrap">
                        {JSON.stringify(incident['Alert Details'], null, 2)}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}

        {/* View More/Less Buttons */}
        {(hasMoreItems || canShowLess) && (
          <div className="flex justify-center mt-6 space-x-3">
            {hasMoreItems && (
              <button 
                onClick={handleViewMore}
                className="px-6 py-3 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-800 dark:text-gray-100 rounded-lg border border-gray-300 dark:border-gray-600 transition-all hover:shadow-md font-medium"
              >
                Show more ({incidents.length - visibleCount} more)
              </button>
            )}
            {canShowLess && (
              <button 
                onClick={handleViewLess}
                className="px-6 py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg border border-gray-300 dark:border-gray-600 transition-all hover:shadow-md font-medium"
              >
                Show less
              </button>
            )}
          </div>
        )}

        {/* Video Modal */}
        <VideoModal
          isOpen={videoModal.isOpen}
          videoUrl={videoModal.videoUrl}
          title={videoModal.title}
          onClose={closeVideoModal}
        />
      </div>
    );
  },
  (prevProps, nextProps) => 
    prevProps.payload === nextProps.payload
);

CustomIncidents.displayName = 'CustomIncidents';