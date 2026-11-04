// SPDX-License-Identifier: Apache-2.0
'use client';

import Link from 'next/link';
import { Fragment, type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode, useState } from 'react';
import { PlusIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { EvidenceBadges } from '@/sections/reports/components/EvidenceBadges';
import { Button } from '@/components/ui/button';
import { Card as UICard, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { evidenceDisplay } from '@/lib/evidence/evidence-view';
import { formatTimestamp } from '@/lib/time';
import { severityStyle } from '@/lib/ui/severity';
import { cn } from '@/lib/utils';
import { formatClock } from '@/modules/reports/domain/normalize';
import {
  assetDetail,
  type EvidenceDetailItem,
  entityDetail,
  evidenceDetailLines,
  instrumentDetail,
} from '@/modules/reports/domain/evidence-detail';
import { reportHeading, type ReportView } from '@/modules/reports/domain/report-view';
import { type StillBox, type TrackedReportItem, trackedReportItems } from '@/modules/reports/domain/track';
import { EvidenceFrame } from '@/sections/evidence/components/EvidenceFrame';
import { EvidenceCarousel } from '@/sections/evidence/components/EvidenceCarousel';
import { EvidenceDetailDialog } from '@/sections/reports/components/EvidenceDetailDialog';
import { PresenceBar } from '@/sections/reports/components/PresenceBar';
import { RunDetailsPanel } from '@/sections/reports/components/ReportRunDetails';

const capitalize = (value: string | null) => (value ? value[0].toUpperCase() + value.slice(1) : 'Unclassified');

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 border-b border-border/70 py-3.5 last:border-b-0 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-6">
      <dt className="pt-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</dt>
      <dd className="m-0 min-w-0 text-sm leading-6 text-foreground">{children}</dd>
    </div>
  );
}

/**
 * The scrolling part of View mode: the version, its report fields, its evidence grouped by holder (each card opens in
 * detail; a tracked item's card also selects its box in the player and shows where it is on screen), and the model's own output.
 */
export function ReportPane({
  report,
  playerDuration,
  onSeek,
  onAddEvidence,
  officialMissing = false,
  compareHref = null,
  selectedTrackId = null,
  onSelectTrack = () => undefined,
}: {
  report: ReportView;
  playerDuration: number | null;
  onSeek: (seconds: number, still?: StillBox | null) => void;
  onAddEvidence: () => void;
  /** True once the versions have loaded and none of them is official. */
  officialMissing?: boolean;
  /** Link to compare this version with another AI version, when there is one. */
  compareHref?: string | null;
  /** The tracked item whose box the player emphasises; null for none. */
  selectedTrackId?: string | null;
  onSelectTrack?: (id: string | null) => void;
}) {
  const { structured, modelOutput, run, differences, revision } = report;
  const { incident } = structured;
  const windowEdited = differences.fields.includes('incident window');
  const original = modelOutput?.original;
  const heldBy = (entityId: string) => structured.instruments.filter((instrument) => instrument.entityId === entityId);
  const heldIds = new Set(structured.entities.flatMap((entity) => heldBy(entity.entityId).map((instrument) => instrument.instrumentId)));
  const unheld = structured.instruments.filter((instrument) => !heldIds.has(instrument.instrumentId));
  const [detail, setDetail] = useState<EvidenceDetailItem | null>(null);
  const itemCount = structured.entities.length + structured.instruments.length + structured.assets.length;
  const tracked = new Map(trackedReportItems(structured).map((item) => [`${item.kind}:${item.id}`, item]));
  const cardProps = (item: EvidenceDetailItem) => {
    const track = tracked.get(`${item.kind}:${item.id}`) ?? null;
    return {
      item,
      track,
      playerDuration,
      selected: track !== null && selectedTrackId === track.id,
      onOpen: setDetail,
      onSeek,
      // Opening the card also selects its box in the player.
      onSelect: track ? (toggle: boolean) => onSelectTrack(toggle && selectedTrackId === track.id ? null : track.id) : undefined,
      videoId: report.videoId,
    };
  };

  return (
    <div className="px-5 pb-16 sm:px-8">
      <UICard className="mt-5 gap-0 border-border/80 bg-card py-0 text-sm" data-testid="version-summary">
        <CardHeader className="flex flex-row items-center gap-4 border-b border-border/70 bg-muted/25 px-4 py-4">
          <div className="grid size-12 shrink-0 place-items-center rounded-xl bg-background font-mono text-sm font-bold text-foreground shadow-xs ring-1 ring-border">
            V{revision.revisionNo}
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base font-semibold">Now Viewing: Version {revision.revisionNo}</CardTitle>
              {report.official.isOfficial && (
                <Badge
                  className="border-amber-400 bg-amber-50 text-[11px] font-bold text-amber-800"
                  data-testid="official-status"
                  variant="outline"
                >
                  ★ Official
                </Badge>
              )}
            </div>
            <CardDescription className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs" data-testid="revision-label">
              <Badge className="px-2 py-0.5 text-[11px] font-semibold" variant="secondary">
                {revision.kind === 'human' ? 'Human' : 'AI'}
              </Badge>
              {revision.kind === 'ai' && <span>{run.model}</span>}
              <span>{formatTimestamp(revision.createdAt, 'time unknown')}</span>
              {revision.note && <span>· {revision.note}</span>}
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="divide-y divide-border p-0">
          {run.status === 'valid_after_structural_repair' && (
            <p className="px-4 py-2 text-xs text-amber-800" data-testid="contract-repair-applied">
              Contract repair applied (IDs only)
            </p>
          )}
          {run.notesKind === 'legacy' && <p className="px-4 py-2 text-xs text-muted-foreground">Earlier prompt version</p>}
          {officialMissing && (
            <p className="bg-amber-50/80 px-4 py-2 text-xs text-amber-950" data-testid="official-status">
              No official version has been selected for this video. Star a version to make it official.
            </p>
          )}
          {compareHref && (
            <p className="px-4 py-2 text-xs">
              <span className="text-muted-foreground">Another AI version is available for this video. </span>
              <Link
                className="font-semibold text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
                href={compareHref}
              >
                Compare versions side by side →
              </Link>
            </p>
          )}
        </CardContent>
      </UICard>

      <UICard className="mt-5 gap-0 border-border/80 py-0" data-testid="report-fields-section">
        <CardHeader className="gap-1 border-b border-border/70 px-4 py-3.5">
          <CardTitle className="text-base font-semibold">Report fields</CardTitle>
          <CardDescription className="text-xs">Incident overview and model assessment</CardDescription>
        </CardHeader>
        <CardContent className="px-4 pb-3 pt-1">
          <dl className="m-0">
            <Row label="Title">
              {reportHeading(report)}
              {modelOutput?.title && (
                <span className="mt-0.5 block text-xs text-muted-foreground/80">
                  Title generated by the model{differences.fields.length ? ' (before reviewer edits)' : ''}
                </span>
              )}
            </Row>
            <Row label="Incident type">
              {capitalize(incident.type)}
              {modelOutput?.location ? ` · ${modelOutput.location}` : ''}
              {differences.type && (
                <span className="mt-0.5 block text-xs font-semibold text-destructive" data-testid="type-edited">
                  Model classified this as {differences.type.model}; reviewer set {differences.type.current}.
                </span>
              )}
            </Row>
            <Row label="Severity">
              <Badge className={`px-3 py-0.5 text-xs font-bold ${severityStyle(incident.severityLevel)}`} variant="outline">
                {incident.severityLevel ?? '—'}/5
              </Badge>
              {differences.severity && (
                <span className="mt-1 block text-xs font-semibold text-destructive" data-testid="severity-edited">
                  Model assessed severity {differences.severity.model}/5; reviewer set {differences.severity.current}/5. The rationale below
                  explains the model&apos;s assessment, not the reviewer&apos;s.
                </span>
              )}
            </Row>
            {incident.confidenceScore !== null && <Row label="Confidence">{Math.round(incident.confidenceScore * 100)}%</Row>}
            <Row label="Time range">
              {incident.startSeconds !== null ? (
                <Button
                  className="h-6 font-mono text-xs font-bold"
                  onClick={() => onSeek(incident.startSeconds ?? 0)}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  {formatClock(incident.startSeconds)}
                  {incident.endSeconds !== null ? `–${formatClock(incident.endSeconds)}` : ''}
                </Button>
              ) : (
                'Not recorded'
              )}
              {windowEdited && original && original.startSeconds !== null && original.endSeconds !== null && (
                <span className="ml-3 text-xs font-semibold text-destructive" data-testid="window-edited">
                  reviewer-set; model window {formatClock(original.startSeconds)}–{formatClock(original.endSeconds)}
                </span>
              )}
            </Row>
            <Row label="Summary">
              <span className="whitespace-pre-wrap">{incident.description ?? 'No summary was recorded.'}</span>
            </Row>
            <Row label="Severity rationale">
              <span className="text-foreground/75">{modelOutput?.severityReason ?? 'No model rationale was retained for this run.'}</span>
              {differences.severity && (
                <span className="mt-0.5 block text-xs text-muted-foreground">For model severity {differences.severity.model}/5</span>
              )}
            </Row>
          </dl>
          {differences.fields.length > 0 && (
            <Badge className="mt-3 text-xs font-semibold text-destructive" data-testid="reviewer-edited" variant="secondary">
              Reviewer-edited after generation: {differences.fields.join(', ')}
            </Badge>
          )}
        </CardContent>
      </UICard>

      <UICard className="mt-5 gap-0 overflow-hidden border-border/80 py-0" data-testid="evidence-section">
        <CardHeader className="flex flex-row items-center justify-between gap-4 border-b border-border/70 bg-muted/20 px-4 py-3.5">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base font-semibold">Evidence items</CardTitle>
              <Badge className="font-mono text-[10px]" variant="secondary">
                {itemCount}
              </Badge>
            </div>
            <CardDescription className="text-xs">People, instruments and assets linked to this incident</CardDescription>
          </div>
          <Button className="shrink-0" data-testid="add-evidence" onClick={onAddEvidence} size="sm" type="button">
            <PlusIcon aria-hidden="true" />
            Add evidence
          </Button>
        </CardHeader>
        {itemCount === 0 ? (
          <CardContent className="px-4 py-5">
            <p
              className="m-0 rounded-lg border border-dashed border-border px-4 py-5 text-center text-sm text-muted-foreground"
              data-testid="evidence-empty"
            >
              No evidence items yet. Add evidence to start documenting people, instruments or assets in this incident.
            </p>
          </CardContent>
        ) : (
          <div className="px-4 pt-3 pb-1" data-testid="evidence-grid">
            <EvidenceCarousel label="Report evidence items">
              {structured.entities.map((entity) => (
                <Fragment key={entity.entityId}>
                  <Card {...cardProps(entityDetail(entity, structured.instruments))} />
                  {heldBy(entity.entityId).map((instrument) => (
                    <Card key={instrument.instrumentId} {...cardProps(instrumentDetail(instrument, structured.entities))} />
                  ))}
                </Fragment>
              ))}
              {unheld.map((instrument) => (
                <Card key={instrument.instrumentId} {...cardProps(instrumentDetail(instrument, structured.entities))} />
              ))}
              {structured.assets.map((asset) => (
                <Card key={asset.assetId} {...cardProps(assetDetail(asset))} />
              ))}
            </EvidenceCarousel>
          </div>
        )}
      </UICard>

      <EvidenceDetailDialog
        item={detail}
        onClose={() => setDetail(null)}
        onPlayFrom={onSeek}
        playerDuration={playerDuration}
        track={detail ? (tracked.get(`${detail.kind}:${detail.id}`) ?? null) : null}
        videoId={report.videoId}
      />

      <div className="-mx-5 mt-6 sm:-mx-8">
        <RunDetailsPanel report={report} />
      </div>
    </div>
  );
}

/** The canonical id (E1, I1, A1) that Edit and Compare print for the same item. */
function IdBadge({ id }: { id: string }) {
  return (
    <Badge className="shrink-0 px-1.5 py-0 font-mono text-[10px] font-bold" data-testid="evidence-id" variant="outline">
      {id}
    </Badge>
  );
}

/**
 * One evidence item: its whole video frame with the box drawn on it (never a cut-out crop). The whole card is a button
 * (click, Enter or Space) that opens the item in detail; a tracked item's card also selects its box in the player and shows
 * its presence bar, whose own buttons seek without opening it.
 */
function Card({
  item,
  track,
  playerDuration,
  selected,
  videoId,
  onOpen,
  onSeek,
  onSelect,
}: {
  item: EvidenceDetailItem;
  track: TrackedReportItem | null;
  playerDuration: number | null;
  selected: boolean;
  videoId: string;
  onOpen: (item: EvidenceDetailItem) => void;
  onSeek: (seconds: number) => void;
  onSelect?: (toggle: boolean) => void;
}) {
  const { seconds } = item;
  const lines = evidenceDetailLines(item);
  const shown = evidenceDisplay(item.evidence, videoId);
  // Opening the card also selects its box in the player (a tracked item); inner buttons and links keep their own action.
  const open = () => {
    onOpen(item);
    onSelect?.(false);
  };
  return (
    <UICard
      aria-label={`Show ${item.title} in detail`}
      className={cn(
        'w-64 max-w-full shrink-0 snap-start cursor-pointer overflow-hidden text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        selected && 'ring-2 ring-ring',
      )}
      data-selected={selected ? 'true' : undefined}
      data-testid="evidence-card"
      data-track-item={track?.id}
      onClick={(event: MouseEvent<HTMLDivElement>) => {
        if ((event.target as HTMLElement).closest('button, a')) return;
        open();
      }}
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
        if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return;
        event.preventDefault();
        open();
      }}
      role="button"
      style={selected && track ? ({ '--tw-ring-color': track.colour } as CSSProperties) : undefined}
      tabIndex={0}
    >
      <div data-testid="evidence-open">
        <EvidenceFrame
          aspect={16 / 9}
          box={shown.box}
          className="rounded-none!"
          frameSrc={shown.frameSrc}
          fullFrame
          kind={item.kind}
          label={item.title}
          notLocated={shown.notLocated}
          seconds={seconds}
          src={shown.src}
        />
      </div>
      <CardContent className="space-y-1 p-3">
        <h4 className="flex items-center gap-2 text-sm font-semibold">
          <IdBadge id={item.id} />
          <span className="min-w-0">{item.title}</span>
        </h4>
        <EvidenceBadges badges={lines.badges} />
        <p className="text-muted-foreground">{item.description}</p>
        {lines.holds && (
          <p>
            Holds <b>{lines.holds}</b>
          </p>
        )}
        {lines.threat && <p className="text-muted-foreground">{lines.threat}</p>}
        {lines.heldBy && (
          <p>
            Held by <b>{lines.heldBy}</b>
          </p>
        )}
        {lines.onScreen && <p className="text-muted-foreground">{lines.onScreen}</p>}
        {lines.notTracked && (
          <p className="text-xs text-muted-foreground" data-testid="evidence-not-tracked">
            {lines.notTracked}
          </p>
        )}
        {lines.time && (
          <p className="text-muted-foreground">
            Most visible at <b className="font-mono">{lines.time}</b>
          </p>
        )}
        {track && (
          <PresenceBar
            colour={track.colour}
            durationSeconds={playerDuration}
            firstSeconds={track.firstSeconds}
            label={item.title}
            lastSeconds={track.lastSeconds}
            onSeek={onSeek}
          />
        )}
      </CardContent>
    </UICard>
  );
}
