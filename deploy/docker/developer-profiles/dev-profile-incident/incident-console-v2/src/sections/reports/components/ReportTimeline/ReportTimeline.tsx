// SPDX-License-Identifier: Apache-2.0
'use client';

import { EvidenceFrame, KIND_COLOUR, KIND_LABEL } from '@/sections/evidence/components/EvidenceFrame';
import { axisPercent, axisTicks, buildTimelineLanes, timelineDuration, type LaneMarker } from '@/lib/reports/timeline-lanes';
import { formatClock } from '@/modules/reports/domain/normalize';
import type { ReportView } from '@/modules/reports/domain/report-view';

const MARKER_SHAPE: Record<LaneMarker['kind'], string> = { entity: 'rounded-full', instrument: 'rotate-45', asset: '' };

export function ReportTimeline({
  structured,
  videoSeconds,
  currentTime,
  onSeek,
}: {
  structured: ReportView['structured'];
  videoSeconds: number | null;
  currentTime: number;
  onSeek: (seconds: number) => void;
}) {
  const lanes = buildTimelineLanes(structured);
  const duration = timelineDuration(lanes, videoSeconds);
  const ticks = axisTicks(duration);
  const playhead = axisPercent(currentTime, duration);

  return (
    <section aria-label="Timeline" className="flex h-full min-h-0 flex-col bg-card" data-testid="report-timeline">
      <h3 className="px-3 pt-2 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-muted-foreground">
        Timeline · one lane per person and what they hold
      </h3>
      <p className="flex gap-3 px-3 py-1 text-[10.5px] font-bold uppercase tracking-wide text-muted-foreground">
        {(['entity', 'instrument', 'asset'] as const).map((kind) => (
          <span className="flex items-center gap-1" key={kind}>
            <i className={`inline-block size-2 ${MARKER_SHAPE[kind]} ${KIND_COLOUR[kind]}`} />
            {KIND_LABEL[kind]}
          </span>
        ))}
      </p>
      {lanes.length === 0 ? (
        <p className="px-3 py-4 text-sm text-muted-foreground" data-testid="timeline-empty">
          This version has no evidence items to place on a timeline.
        </p>
      ) : (
        <div className="min-h-0 overflow-auto">
          <div className="relative flex min-w-max">
            <div className="sticky left-0 z-10 w-10 shrink-0 bg-card">
              <div className="h-12" />
              <div className="relative h-56">
                {ticks.map((tick) => (
                  <span
                    className="absolute left-1 -translate-y-1/2 font-mono text-[10px] text-muted-foreground"
                    key={tick}
                    style={{ top: `${axisPercent(tick, duration)}%` }}
                  >
                    {formatClock(tick)}
                  </span>
                ))}
              </div>
            </div>
            {lanes.map((lane) => (
              <div className="w-36 shrink-0 border-l border-dashed border-border" data-lane={lane.id} key={lane.id}>
                <div className="flex h-12 flex-col items-center justify-center gap-0.5 px-1 text-center text-[11px] font-semibold leading-tight">
                  {lane.kind === 'entity' && lane.imageUrl && (
                    <EvidenceFrame aspect={1} className="h-5 w-5 !rounded-md" label={lane.title} src={lane.imageUrl} />
                  )}
                  <span className="line-clamp-2">{lane.title}</span>
                </div>
                <button
                  aria-label={`Move the video along ${lane.title}`}
                  className="relative block h-56 w-full cursor-pointer"
                  onClick={(event) => {
                    const box = event.currentTarget.getBoundingClientRect();
                    onSeek(Math.round(((event.clientY - box.top) / box.height) * duration));
                  }}
                  type="button"
                >
                  {lane.span && (
                    <span
                      className="absolute left-1/2 w-3 -translate-x-1/2 rounded-full bg-primary/35"
                      data-testid="lane-span"
                      style={{
                        top: `${axisPercent(lane.span.start, duration)}%`,
                        height: `${Math.max(1, axisPercent(lane.span.end, duration) - axisPercent(lane.span.start, duration))}%`,
                      }}
                    />
                  )}
                  {lane.markers.map((marker) => {
                    const seconds = marker.seconds;
                    if (seconds === null) return null;
                    return (
                      <span
                        aria-label={`${marker.label} at ${formatClock(seconds)}`}
                        className={`absolute left-1/2 size-3 -translate-x-1/2 -translate-y-1/2 border-2 border-background ${MARKER_SHAPE[marker.kind]} ${KIND_COLOUR[marker.kind]}`}
                        data-marker={marker.id}
                        key={`${marker.kind}-${marker.id}`}
                        role="img"
                        style={{ top: `${axisPercent(seconds, duration)}%` }}
                        title={`${marker.label} · ${formatClock(seconds)}`}
                      />
                    );
                  })}
                </button>
              </div>
            ))}
            <div
              aria-hidden
              className="pointer-events-none absolute left-10 right-0 border-t-2 border-foreground"
              data-testid="playhead"
              style={{ top: `calc(3rem + ${playhead * 2.24}px)` }}
            />
          </div>
        </div>
      )}
    </section>
  );
}
