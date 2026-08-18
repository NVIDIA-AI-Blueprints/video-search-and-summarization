// SPDX-License-Identifier: MIT
/**
 * Incident list rendered inside an assistant response.
 *
 * When the agent answers with alerts it returns structured incidents rather
 * than prose. Each collapses to a headline so a long list stays scannable, and
 * expands to the raw clip and alert JSON — operators check the underlying
 * fields, so the payload is shown verbatim rather than summarised.
 */
import React, { useCallback, useState } from 'react';
import { VideoModal, copyToClipboard, formatTimestamp } from 'common';

/** Incidents shown before "Show more", and the size of each additional page. */
const INCIDENTS_PAGE_SIZE = 3;

export interface IncidentClipInformation {
  Timestamp?: string;
  Stream?: string;
  Alerts?: string;
  snapshot_url?: string;
  video_url?: string;
  [key: string]: unknown;
}

export interface IncidentAlertDetails {
  'Alert Triggered'?: string;
  Validation?: boolean;
  'Alert Description'?: string;
  [key: string]: unknown;
}

export interface Incident {
  'Alert Title'?: string;
  'Clip Information'?: IncidentClipInformation;
  'Alert Details'?: IncidentAlertDetails;
  [key: string]: unknown;
}

export interface CustomIncidentsPayload {
  incidents?: Incident[];
  /** Agent's prose summary across the incidents. */
  message?: string;
}

export interface CustomIncidentsProps {
  payload?: CustomIncidentsPayload;
}

type SubSection = 'clip' | 'details';

const CustomIncidentsView: React.FC<CustomIncidentsProps> = ({ payload }) => {
  // At most one incident is open: they are tall, and two expanded at once
  // pushes the rest of the answer off screen.
  const [expandedIncident, setExpandedIncident] = useState<number | null>(null);
  const [expandedSubSections, setExpandedSubSections] = useState<Set<SubSection>>(new Set());
  const [visibleCount, setVisibleCount] = useState(INCIDENTS_PAGE_SIZE);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [videoTitle, setVideoTitle] = useState('');

  const incidents = Array.isArray(payload?.incidents) ? payload!.incidents : [];

  const toggleIncident = useCallback((index: number) => {
    setExpandedIncident((current) => (current === index ? null : index));
    // Sub-sections belong to whichever incident was open, so switching or
    // closing resets them rather than carrying state to the next one.
    setExpandedSubSections(new Set());
  }, []);

  const toggleSubSection = useCallback((section: SubSection) => {
    setExpandedSubSections((current) => {
      const next = new Set(current);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  }, []);

  if (incidents.length === 0) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">No incidents found</div>;
  }

  const message = payload?.message?.trim();
  const visibleIncidents = incidents.slice(0, visibleCount);
  const remaining = incidents.length - visibleCount;

  return (
    <div className="flex flex-col gap-2">
      {message && (
        <div className="rounded-md border border-gray-200 p-3 text-sm dark:border-gray-600">
          <div className="mb-1 font-semibold">Summary</div>
          <div>{message}</div>
        </div>
      )}

      {visibleIncidents.map((incident, index) => {
        const clip = incident['Clip Information'] ?? {};
        const details = incident['Alert Details'] ?? {};
        const isExpanded = expandedIncident === index;
        const title = incident['Alert Title'] ?? `Incident ${index + 1}`;

        return (
          <div
            key={index}
            className="rounded-md border border-gray-200 dark:border-gray-600"
          >
            <div className="flex items-center justify-between gap-2 p-3">
              <div
                className="flex-1 cursor-pointer text-sm"
                onClick={() => toggleIncident(index)}
              >
                <span className="font-semibold">
                  {`Alert Triggered ${index + 1}: `}
                </span>
                <span>{details['Alert Triggered'] ?? ''}</span>
                {clip.Timestamp && (
                  <span className="ml-2 text-xs text-gray-500">
                    {formatTimestamp(clip.Timestamp)}
                  </span>
                )}
              </div>

              <button
                type="button"
                className="rounded p-1 hover:bg-gray-100 dark:hover:bg-neutral-800"
                onClick={() => {
                  // Renders without a clip when the agent had no video for the
                  // incident; the control stays put so the row does not reflow.
                  if (!clip.video_url) return;
                  setVideoUrl(clip.video_url);
                  setVideoTitle(String(title));
                }}
              >
                <span aria-hidden="true">&#9654;</span>
              </button>
            </div>

            {isExpanded && (
              <div className="border-t border-gray-200 p-3 dark:border-gray-600">
                <SubSectionBlock
                  label="Clip Information"
                  json={clip}
                  copyTitle="Copy Clip Information JSON"
                  isExpanded={expandedSubSections.has('clip')}
                  onToggle={() => toggleSubSection('clip')}
                />
                <SubSectionBlock
                  label="Alert Details"
                  json={details}
                  copyTitle="Copy Alert Details JSON"
                  isExpanded={expandedSubSections.has('details')}
                  onToggle={() => toggleSubSection('details')}
                />
              </div>
            )}
          </div>
        );
      })}

      {incidents.length > INCIDENTS_PAGE_SIZE && (
        <div className="flex gap-2">
          {remaining > 0 && (
            <button
              type="button"
              className="text-sm text-[#76b900] hover:underline"
              onClick={() => setVisibleCount((count) => count + INCIDENTS_PAGE_SIZE)}
            >
              {`Show more (${remaining} more)`}
            </button>
          )}
          {visibleCount > INCIDENTS_PAGE_SIZE && (
            <button
              type="button"
              className="text-sm text-[#76b900] hover:underline"
              onClick={() => setVisibleCount(INCIDENTS_PAGE_SIZE)}
            >
              Show less
            </button>
          )}
        </div>
      )}

      <VideoModal
        isOpen={videoUrl !== null}
        videoUrl={videoUrl ?? ''}
        title={videoTitle}
        onClose={() => setVideoUrl(null)}
      />
    </div>
  );
};

/**
 * Reference-compared: an incident list is expensive to render and the
 * transcript re-renders on every streamed frame. A parent that rebuilds the
 * payload object each render will still re-render, which is the intended
 * signal that the data actually changed.
 */
export const CustomIncidents = React.memo(CustomIncidentsView);
CustomIncidents.displayName = 'CustomIncidents';

interface SubSectionBlockProps {
  label: string;
  json: Record<string, unknown>;
  copyTitle: string;
  isExpanded: boolean;
  onToggle: () => void;
}

const SubSectionBlock: React.FC<SubSectionBlockProps> = ({
  label,
  json,
  copyTitle,
  isExpanded,
  onToggle,
}) => (
  <div className="mb-2 last:mb-0">
    <div className="flex items-center justify-between gap-2">
      <div className="cursor-pointer text-sm font-medium" onClick={onToggle}>
        {label}
      </div>
      <button
        type="button"
        title={copyTitle}
        aria-label={copyTitle}
        className="rounded p-1 text-xs hover:bg-gray-100 dark:hover:bg-neutral-800"
        onClick={() => copyToClipboard(JSON.stringify(json, null, 2))}
      >
        Copy
      </button>
    </div>
    {isExpanded && (
      <pre className="mt-1 overflow-auto rounded bg-gray-50 p-2 text-xs dark:bg-black">
        {JSON.stringify(json, null, 2)}
      </pre>
    )}
  </div>
);
