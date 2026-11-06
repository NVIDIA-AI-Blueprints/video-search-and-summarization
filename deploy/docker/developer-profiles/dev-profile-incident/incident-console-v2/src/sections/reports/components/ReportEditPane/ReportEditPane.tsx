// SPDX-License-Identifier: Apache-2.0

'use client';

import { Fragment, useEffect, useRef, useState } from 'react';
import { PlusIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { MutedVideo } from '@/components/muted-video';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { evidenceDisplay, videoFrameUrl } from '@/lib/evidence/evidence-view';
import type { BoxToolClient } from '@/lib/evidence/box-tool-client';
import { addBoxItem, holderChoices, retrackDraftItem } from '@/lib/evidence/box-tool';
import {
  deleteDraftItem,
  draftItemName,
  isDraftEntity,
  isDraftInstrument,
  isSavedEntity,
  isSavedInstrument,
  pairReport,
  revertBlocked,
  revertDraftItem,
  rowDiffers,
  savedItemId,
  savedItemName,
  type PairRow,
  type Structured,
} from '@/lib/reports/compare';
import { severityStyle } from '@/lib/ui/severity';
import { cn } from '@/lib/utils';
import type { ReviewerDraftItem as BoxToolItem, ReviewerTrack } from '@/modules/reports/domain/reviewer-item';
import type { EvidenceItemType as BoxItemType } from '@/modules/reports/domain/revision';
import { EDITABLE_ENTITY_TYPES, EDITABLE_INCIDENT_TYPES, type EditDraft } from '@/modules/reports/domain/edit-draft';
import { formatClock } from '@/modules/reports/domain/normalize';
import { EvidenceFrame } from '@/sections/evidence/components/EvidenceFrame';
import { EvidenceCarousel } from '@/sections/evidence/components/EvidenceCarousel';
import { BoxTool, type ExistingItem } from '@/sections/evidence/components/BoxTool';
import { BoxCanvas } from '@/sections/evidence/components/BoxTool/BoxCanvas';
import { TrackEditor } from '@/sections/evidence/components/TrackEditor';
import { trackEditorSource } from '@/lib/evidence/track-editor-source';

const capitalize = (value: string | null) => (value ? value[0].toUpperCase() + value.slice(1) : 'Unclassified');
const clock = (seconds: number | null) => (seconds === null ? '—' : formatClock(seconds));
const rowKey = (row: PairRow) => `${row.kind}-${row.b?.key ?? (row.a ? savedItemId(row.a) : '')}`;
const draftDisplayId = (item: NonNullable<PairRow['b']>) =>
  isDraftEntity(item) ? item.canonicalId : (item.sourceId ?? (isDraftInstrument(item) ? 'New instrument' : 'New asset'));

/**
 * Edit mode's pane: the version the draft started from (A) on the left and the
 * editable draft (B) on the right, field by field and evidence row by row. A
 * is never editable. Delete removes a draft item (and the instruments an entity
 * holds); Revert puts an edited or deleted item back exactly as A has it.
 * "+ Add evidence" opens the box tool: the reviewer draws a box, the AI names
 * and tracks it, and the result goes into a new draft item. Edit track adjusts
 * an existing item's track without touching its text. An item's evidence image
 * is always the first second it is seen.
 */
export function ReportEditPane({
  a,
  aRevisionNo,
  baseRevisionNo,
  draft,
  title,
  videoId,
  videoSrc,
  onChange,
  onSeek,
  boxClient,
  initiallyAddEvidence = false,
  officialMissing = false,
}: {
  a: Structured | null;
  aRevisionNo: number;
  baseRevisionNo: number;
  draft: EditDraft;
  title: string;
  videoId: string;
  videoSrc: string;
  onChange: (draft: EditDraft) => void;
  onSeek: (seconds: number) => void;
  /** Replaces the box tool's calls to the console routes (tests). */
  boxClient?: BoxToolClient;
  /** Open the existing box tool immediately when entering Edit from the View evidence section. */
  initiallyAddEvidence?: boolean;
  /** True once revisions have loaded and none of them is official. */
  officialMissing?: boolean;
}) {
  // The box tool: adding new evidence, or re-tracking a card's item from a new box.
  const [boxTool, setBoxTool] = useState<{ row: PairRow['b'] } | null>(initiallyAddEvidence ? { row: null } : null);
  const [carouselControlsTarget, setCarouselControlsTarget] = useState<HTMLDivElement | null>(null);
  // The track editor: fix a card's track with keyframes. `saved` is the opened version's item, whose track it starts from.
  const [trackEditor, setTrackEditor] = useState<{
    row: NonNullable<PairRow['b']>;
    saved: NonNullable<PairRow['a']>['evidence'] | null;
  } | null>(null);
  const pairing = pairReport(a, draft);
  const patchIncident = (patch: Partial<EditDraft['incident']>) => onChange({ ...draft, incident: { ...draft.incident, ...patch } });
  const patchEntity = (key: string, patch: Partial<EditDraft['entities'][number]>) =>
    onChange({ ...draft, entities: draft.entities.map((entity) => (entity.key === key ? { ...entity, ...patch } : entity)) });
  const patchInstrument = (key: string, patch: Partial<EditDraft['instruments'][number]>) =>
    onChange({
      ...draft,
      instruments: draft.instruments.map((instrument) => (instrument.key === key ? { ...instrument, ...patch } : instrument)),
    });
  const patchAsset = (key: string, patch: Partial<EditDraft['assets'][number]>) =>
    onChange({ ...draft, assets: draft.assets.map((asset) => (asset.key === key ? { ...asset, ...patch } : asset)) });
  const remove = (row: PairRow) => {
    if (row.b) onChange(deleteDraftItem(draft, row.kind, row.b.key));
  };
  const revert = (row: PairRow) => {
    if (a) onChange(revertDraftItem(draft, a, row));
  };
  const editTrack = (row: PairRow) => {
    if (row.b) setTrackEditor({ row: row.b, saved: row.a?.evidence ?? null });
  };
  const finishTrackEditor = (track: ReviewerTrack) => {
    const target = trackEditor?.row;
    if (!target) return;
    const kind: BoxItemType = isDraftEntity(target) ? 'entity' : isDraftInstrument(target) ? 'instrument' : 'asset';
    const text = existingForTool(target);
    onChange(
      retrackDraftItem(draft, kind, target.key, {
        itemType: kind,
        name: text.name,
        description: text.description,
        ...(text.threatLevel !== null ? { threatLevel: text.threatLevel } : {}),
        aiProposal: null,
        track,
      }),
    );
    setTrackEditor(null);
  };
  const finishBoxTool = (item: BoxToolItem, holderKey: string | null) => {
    const target = boxTool?.row ?? null;
    if (target) {
      const kind: BoxItemType = isDraftEntity(target) ? 'entity' : isDraftInstrument(target) ? 'instrument' : 'asset';
      onChange(retrackDraftItem(draft, kind, target.key, item));
    } else onChange(addBoxItem(draft, item, holderKey));
    setBoxTool(null);
  };
  const existingForTool = (item: NonNullable<PairRow['b']>): ExistingItem => ({
    itemType: isDraftEntity(item) ? 'entity' : isDraftInstrument(item) ? 'instrument' : 'asset',
    name: draftItemName(item),
    description: item.description,
    holder: isDraftInstrument(item) && item.holderKey ? draftHolder(item) : null,
    threatLevel: isDraftInstrument(item) ? item.threat_level : null,
    firstSeenSeconds: item.reviewer?.track?.samples[0].t ?? item.timestampSeconds ?? 0,
  });
  const holderOptions = draft.entities.map((entity, index) => ({
    key: entity.key,
    label: `${entity.canonicalId || index + 1} · ${entity.label || entity.type}`,
  }));
  const draftHolder = (instrument: EditDraft['instruments'][number]) => {
    if (!instrument.holderKey) return 'unknown';
    const holder = draft.entities.find((entity) => entity.key === instrument.holderKey);
    return holder ? holder.label || holder.type : 'unknown';
  };
  const savedHolder = (instrument: PairRow['a']) => {
    if (!instrument || !isSavedInstrument(instrument) || !instrument.entityId) return 'unknown';
    const holder = a?.entities.find((entity) => entity.entityId === instrument.entityId);
    return holder ? holder.label || holder.type : instrument.entityId;
  };
  const inherited = (row: PairRow) => {
    const entity = row.a;
    if (!entity || !isSavedEntity(entity) || !a) return '';
    const names = a.instruments.filter((instrument) => instrument.entityId === entity.entityId).map((instrument) => instrument.name);
    return names.length ? `Holds ${names.join(', ')}` : 'Holds nothing visible';
  };

  return (
    <div className="px-5 pb-6 sm:px-8" data-testid="report-edit-pane">
      <Card className="mt-5 gap-0 border-border/80 py-0" data-testid="version-summary">
        <CardHeader className="flex flex-row items-center gap-4 border-b border-l-4 border-border/70 border-l-primary bg-primary/5 px-4 py-4">
          <div className="grid size-12 shrink-0 place-items-center rounded-xl bg-background font-mono text-sm font-bold shadow-xs ring-1 ring-border">
            V{baseRevisionNo}
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base font-semibold">Now Editing: Version {baseRevisionNo}</CardTitle>
              <Badge variant="secondary">Editable draft</Badge>
            </div>
            <CardDescription className="text-xs">Changes will be saved as a new version.</CardDescription>
          </div>
        </CardHeader>
        {officialMissing && (
          <CardContent className="bg-amber-50/80 px-4 py-2 text-xs text-amber-950" data-testid="official-status">
            No official version has been selected for this video. Star a version to make it official.
          </CardContent>
        )}
      </Card>
      <Card className="mt-4 gap-0 border-border/80 py-0" data-testid="compare-header">
        <CardContent className="grid gap-2 p-3 text-xs sm:grid-cols-[5rem_minmax(0,1fr)_minmax(0,1fr)] sm:items-center sm:gap-3">
          <span className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted-foreground">Compare</span>
          <div className="flex min-w-0 items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2" data-side="A">
            <Badge className="font-mono text-[10px]" variant="outline">
              A
            </Badge>
            <b>v{aRevisionNo}</b>
            <span className="text-muted-foreground">Read-only</span>
          </div>
          <div className="flex min-w-0 items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2" data-side="B">
            <Badge className="font-mono text-[10px]" variant="secondary">
              B
            </Badge>
            <b>Draft</b>
            <span className="text-muted-foreground">from v{baseRevisionNo} · Editable</span>
          </div>
        </CardContent>
      </Card>

      <Card className="mt-5 gap-0 border-border/80 py-0" data-testid="report-fields-compare">
        <CardHeader className="gap-1 border-b border-l-4 border-border/70 border-l-primary bg-primary/5 px-4 py-3.5">
          <CardTitle className="text-base font-semibold">Report fields</CardTitle>
          <CardDescription className="text-xs">Compare the saved values with your editable draft</CardDescription>
        </CardHeader>
        <CardContent className="px-4 pb-3 pt-1">
          <dl className="m-0">
            <PairField
              label="Title"
              left={title}
              right={
                <span className="text-ink/60">
                  {title} <span className="text-[11px] text-ink-caption">(model-generated)</span>
                </span>
              }
            />
            <PairField
              label="Incident type"
              left={capitalize(a?.incident.type ?? null)}
              right={
                <Select
                  onValueChange={(val) => patchIncident({ type: val === '__unclassified__' ? null : val })}
                  value={draft.incident.type ?? '__unclassified__'}
                >
                  <SelectTrigger aria-label="Draft incident type" className="h-9 w-full bg-white text-xs">
                    <SelectValue placeholder="Choose…" />
                  </SelectTrigger>
                  <SelectContent position="popper">
                    <SelectItem value="__unclassified__">Choose…</SelectItem>
                    {EDITABLE_INCIDENT_TYPES.map((type) => (
                      <SelectItem key={type} value={type}>
                        {capitalize(type)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              }
            />
            <PairField
              label="Severity"
              left={
                <span className={`rounded-full px-3 py-0.5 text-xs font-bold ${severityStyle(a?.incident.severityLevel ?? null)}`}>
                  {a?.incident.severityLevel ?? '—'}/5
                </span>
              }
              right={
                <Select
                  onValueChange={(val) => patchIncident({ severity_level: val === '__none__' ? null : Number(val) })}
                  value={draft.incident.severity_level ? String(draft.incident.severity_level) : '__none__'}
                >
                  <SelectTrigger
                    aria-label="Draft severity"
                    className={cn('h-9 w-full bg-white text-xs font-bold', severityStyle(draft.incident.severity_level))}
                  >
                    <SelectValue placeholder="Choose…" />
                  </SelectTrigger>
                  <SelectContent position="popper">
                    <SelectItem value="__none__">Choose…</SelectItem>
                    {[1, 2, 3, 4, 5].map((level) => (
                      <SelectItem key={level} value={String(level)}>
                        {level}/5
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              }
            />
            <PairField
              label="Confidence"
              left={a?.incident.confidenceScore != null ? `${Math.round(a.incident.confidenceScore * 100)}%` : '—'}
              right={
                <span className="text-ink/55">
                  {a?.incident.confidenceScore != null ? `${Math.round(a.incident.confidenceScore * 100)}%` : '—'}{' '}
                  <span className="text-[11px] text-ink-caption">set by the model</span>
                </span>
              }
            />
            <PairField
              label="Time range"
              left={a?.incident.startSeconds != null ? `${clock(a.incident.startSeconds)}–${clock(a.incident.endSeconds)}` : 'Not recorded'}
              right={
                <span className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-[11px] text-ink-caption">{clock(draft.incident.start_timestamp)}</span>
                  <Input
                    aria-label="Draft incident start in seconds"
                    className="h-8 w-20 text-xs"
                    inputMode="numeric"
                    min={0}
                    onChange={(event) =>
                      patchIncident({
                        start_timestamp: event.target.value === '' ? null : Math.max(0, Math.floor(Number(event.target.value))),
                      })
                    }
                    step={1}
                    type="number"
                    value={draft.incident.start_timestamp ?? ''}
                  />
                  <span>–</span>
                  <Input
                    aria-label="Draft incident end in seconds"
                    className="h-8 w-20 text-xs"
                    inputMode="numeric"
                    min={0}
                    onChange={(event) =>
                      patchIncident({
                        end_timestamp: event.target.value === '' ? null : Math.max(0, Math.floor(Number(event.target.value))),
                      })
                    }
                    step={1}
                    type="number"
                    value={draft.incident.end_timestamp ?? ''}
                  />
                  <span className="font-mono text-[11px] text-ink-caption">{clock(draft.incident.end_timestamp)}</span>
                </span>
              }
            />
            <PairField
              label="Summary"
              left={<span className="whitespace-pre-wrap">{a?.incident.description ?? 'No summary was recorded.'}</span>}
              right={
                <Textarea
                  aria-label="Draft summary"
                  className="min-h-[92px] text-xs"
                  onChange={(event) => patchIncident({ description: event.target.value })}
                  value={draft.incident.description}
                />
              }
            />
          </dl>
        </CardContent>
      </Card>

      <Card className="mt-5 gap-0 overflow-hidden border-border/80 py-0" data-testid="evidence-compare">
        <CardHeader className="flex flex-row items-center justify-between gap-4 border-b border-l-4 border-border/70 border-l-primary bg-primary/5 px-4 py-3.5">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base font-semibold">Evidence items</CardTitle>
              <Badge className="font-mono text-[10px]" variant="secondary">
                {pairing.liveCount}
              </Badge>
            </div>
            <CardDescription className="text-xs">A is read-only · B is your editable draft</CardDescription>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <Button data-testid="add-evidence" onClick={() => setBoxTool({ row: null })} size="sm" type="button">
              <PlusIcon aria-hidden="true" />
              Add evidence
            </Button>
            <div aria-label="Evidence navigation" className="flex min-h-8 gap-2" ref={setCarouselControlsTarget} />
          </div>
        </CardHeader>
        {pairing.rows.length === 0 ? (
          <CardContent className="px-4 py-5">
            <p
              className="m-0 rounded-lg border border-dashed border-border px-4 py-5 text-center text-sm text-muted-foreground"
              data-testid="edit-evidence-empty"
            >
              No evidence items yet. Add evidence to document people, instruments or assets in this incident.
            </p>
          </CardContent>
        ) : (
          <div className="px-4 pt-3 pb-1">
            <EvidenceCarousel controlsTarget={carouselControlsTarget} label="Read-only and editable evidence pairs" lanes>
              <div className="sticky left-0 z-10 row-span-2 grid grid-rows-subgrid gap-y-2 bg-background pr-1">
                <div className="flex min-h-20 items-center justify-center bg-background">
                  <Badge className="border-border bg-muted/30 font-mono text-muted-foreground" variant="outline">
                    A
                  </Badge>
                </div>
                <div className="flex min-h-20 items-center justify-center bg-background">
                  <Badge className="border-border bg-muted/30 font-mono text-muted-foreground" variant="outline">
                    B
                  </Badge>
                </div>
              </div>
              {pairing.rows.map((row) => (
                <PairRowView
                  aRevisionNo={aRevisionNo}
                  videoId={videoId}
                  videoSrc={videoSrc}
                  draft={draft}
                  draftHolder={draftHolder}
                  holderOptions={holderOptions}
                  inherited={row.kind === 'entity' ? inherited(row) : ''}
                  key={rowKey(row)}
                  onRemove={remove}
                  onEditTrack={editTrack}
                  onRevert={revert}
                  onSeek={onSeek}
                  patchAsset={patchAsset}
                  patchEntity={patchEntity}
                  patchInstrument={patchInstrument}
                  row={row}
                  savedHolder={savedHolder}
                />
              ))}
            </EvidenceCarousel>
          </div>
        )}
      </Card>

      {trackEditor && (
        <TrackEditor
          itemId={trackEditor.row.sourceId}
          kind={isDraftEntity(trackEditor.row) ? 'entity' : isDraftInstrument(trackEditor.row) ? 'instrument' : 'asset'}
          name={draftItemName(trackEditor.row)}
          onClose={() => setTrackEditor(null)}
          onDone={finishTrackEditor}
          videoSrc={videoSrc}
          {...trackEditorProps(trackEditor.row, trackEditor.saved)}
        />
      )}
      {boxTool && (
        <BoxTool
          client={boxClient}
          existing={boxTool.row ? existingForTool(boxTool.row) : null}
          holders={holderChoices(draft)}
          initialSeconds={boxTool.row ? existingForTool(boxTool.row).firstSeenSeconds : (draft.incident.start_timestamp ?? 0)}
          onClose={() => setBoxTool(null)}
          onDone={finishBoxTool}
          videoId={videoId}
          videoSrc={videoSrc}
        />
      )}
    </div>
  );
}

/** What the track editor opens with: the item's track (the reviewer's or the saved one), else a still box, else nothing. */
function trackEditorProps(item: NonNullable<PairRow['b']>, saved: NonNullable<PairRow['a']>['evidence'] | null) {
  const source = trackEditorSource(item, saved);
  return {
    badge: source.badge,
    samples: source.samples,
    trackUrl: source.trackUrl,
    still: source.still,
    initialSeconds: item.timestampSeconds ?? 0,
  };
}

function PairField({ label, left, right }: { label: string; left: React.ReactNode; right: React.ReactNode }) {
  return (
    <div className="grid gap-1.5 border-b border-border/70 py-3.5 last:border-b-0 sm:grid-cols-[7rem_minmax(0,1fr)_minmax(0,1fr)] sm:gap-5">
      <dt className="pt-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">{label}</dt>
      <dd className="m-0 min-w-0 text-sm leading-6 text-foreground">{left}</dd>
      <dd className="m-0 min-w-0 text-sm leading-6 text-foreground">{right}</dd>
    </div>
  );
}

type PatchEntity = (key: string, patch: Partial<EditDraft['entities'][number]>) => void;
type PatchInstrument = (key: string, patch: Partial<EditDraft['instruments'][number]>) => void;
type PatchAsset = (key: string, patch: Partial<EditDraft['assets'][number]>) => void;

function PairRowView({
  aRevisionNo,
  videoId,
  videoSrc,
  draft,
  row,
  inherited,
  savedHolder,
  draftHolder,
  holderOptions,
  onRemove,
  onEditTrack,
  onRevert,
  onSeek,
  patchEntity,
  patchInstrument,
  patchAsset,
}: {
  aRevisionNo: number;
  videoId: string;
  videoSrc: string;
  draft: EditDraft;
  row: PairRow;
  inherited: string;
  savedHolder: (instrument: PairRow['a']) => string;
  draftHolder: (instrument: EditDraft['instruments'][number]) => string;
  holderOptions: Array<{ key: string; label: string }>;
  onRemove: (row: PairRow) => void;
  onEditTrack: (row: PairRow) => void;
  onRevert: (row: PairRow) => void;
  onSeek: (seconds: number) => void;
  patchEntity: PatchEntity;
  patchInstrument: PatchInstrument;
  patchAsset: PatchAsset;
}) {
  const changed = rowDiffers(row, draft);
  const blocked = revertBlocked(row, draft);
  return (
    <Fragment>
      <div className="min-w-0 snap-start border-r border-dotted border-border pr-3" data-row-kind={row.kind} data-side="A">
        {row.a ? (
          <ReadCard inherited={inherited} item={row.a} onSeek={onSeek} savedHolder={savedHolder} videoId={videoId} />
        ) : (
          <div
            className="flex items-center gap-2 rounded-lg border border-dashed border-primary/30 bg-primary/5 p-4 text-xs font-semibold text-primary"
            data-testid="new-in-b"
          >
            <Badge variant="secondary">New in B</Badge>
          </div>
        )}
      </div>
      <div className="min-w-0 snap-start border-r border-dotted border-border pr-3" data-row-kind={row.kind} data-side="B">
        {row.b ? (
          <EditCard
            draftHolder={draftHolder}
            videoId={videoId}
            videoSrc={videoSrc}
            saved={row.a?.evidence ?? null}
            holderOptions={holderOptions}
            item={row.b}
            onRemove={() => onRemove(row)}
            onEditTrack={() => onEditTrack(row)}
            onRevert={changed ? () => onRevert(row) : null}
            onSeek={onSeek}
            patchAsset={patchAsset}
            patchEntity={patchEntity}
            patchInstrument={patchInstrument}
          />
        ) : (
          <div
            className="rounded-lg border border-dashed border-destructive/30 bg-destructive/5 p-4 text-xs text-destructive"
            data-testid="deleted-in-b"
          >
            <b className="block text-sm">Deleted</b>
            <span>Removed from your draft.</span>
            <div className="mt-2">
              <Button
                className="rounded-full px-3 py-1 text-[11px] font-semibold disabled:opacity-40"
                disabled={blocked}
                onClick={() => onRevert(row)}
                size="xs"
                title={blocked ? 'Revert its holder first' : 'Bring it back exactly as A has it'}
                type="button"
                variant="outline"
              >
                Revert to v{aRevisionNo}
              </Button>
            </div>
          </div>
        )}
      </div>
    </Fragment>
  );
}

function ReadCard({
  item,
  inherited,
  savedHolder,
  onSeek,
  videoId,
}: {
  item: NonNullable<PairRow['a']>;
  inherited: string;
  savedHolder: (instrument: PairRow['a']) => string;
  onSeek: (seconds: number) => void;
  videoId: string;
}) {
  const shown = evidenceDisplay(item.evidence, videoId);
  const kind: BoxItemType = isSavedEntity(item) ? 'entity' : isSavedInstrument(item) ? 'instrument' : 'asset';
  return (
    <CardFrame
      box={shown.box}
      frameSrc={shown.frameSrc}
      header={
        <>
          <Badge className="shrink-0 px-1.5 py-0 font-mono text-[10px] font-bold" data-testid="edit-readonly-evidence-id" variant="outline">
            {savedItemId(item)}
          </Badge>
          <span className="min-w-0">{savedItemName(item)}</span>
        </>
      }
      imageUrl={shown.src}
      kind={kind}
      name={`${savedItemId(item)} · ${savedItemName(item)}`}
      notLocated={shown.notLocated}
      onSeek={onSeek}
      seconds={item.timestampSeconds}
    >
      <p className="text-ink/60">{item.description}</p>
      {isSavedInstrument(item) && (
        <p className="text-ink/50">
          Threat {item.threatLevel ?? 'unrated'}
          {item.threatLevel !== null ? '/5' : ''}
        </p>
      )}
      {isSavedInstrument(item) && (
        <p>
          Held by <b>{savedHolder(item)}</b>
        </p>
      )}
      {isSavedEntity(item) && inherited && <p>{inherited}</p>}
      {isSavedEntity(item) && item.firstSeenSeconds !== null && (
        <p className="text-ink/50">
          On screen {formatClock(item.firstSeenSeconds)}–{formatClock(item.lastSeenSeconds ?? item.firstSeenSeconds)}
        </p>
      )}
      <p className="text-ink/50">
        Most visible at <b className="font-mono">{clock(item.timestampSeconds)}</b>
      </p>
    </CardFrame>
  );
}

function EditCard({
  item,
  videoId,
  videoSrc,
  saved,
  holderOptions,
  draftHolder,
  onRemove,
  onEditTrack,
  onRevert,
  onSeek,
  patchEntity,
  patchInstrument,
  patchAsset,
}: {
  item: NonNullable<PairRow['b']>;
  videoId: string;
  videoSrc: string;
  /** The saved item's evidence, whose frame and box the draft keeps showing until the reviewer replaces the image. */
  saved: NonNullable<PairRow['a']>['evidence'] | null;
  holderOptions: Array<{ key: string; label: string }>;
  draftHolder: (instrument: EditDraft['instruments'][number]) => string;
  onRemove: () => void;
  onEditTrack: () => void;
  onRevert: (() => void) | null;
  onSeek: (seconds: number) => void;
  patchEntity: PatchEntity;
  patchInstrument: PatchInstrument;
  patchAsset: PatchAsset;
}) {
  // A frame the reviewer picked is shown as the frame and the box they drew (the crop is only what Save stores for search);
  // until then the draft shows the saved item's frame and box, as View does.
  const picked = item.imageBox && item.imageTimestampSeconds !== null;
  const inherited = !picked && saved && saved.image === item.image ? evidenceDisplay(saved, videoId) : null;
  const browserPreview = item.sourceId === null && item.reviewer !== null;
  const nameInput = isDraftEntity(item) ? (
    <Input
      aria-label="Entity name"
      className="h-7 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm font-semibold shadow-none focus-visible:ring-2"
      onChange={(event) => patchEntity(item.key, { label: event.target.value })}
      placeholder={item.type}
      value={item.label}
    />
  ) : isDraftInstrument(item) ? (
    <Input
      aria-label="Instrument name"
      className="h-7 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm font-semibold shadow-none focus-visible:ring-2"
      onChange={(event) => patchInstrument(item.key, { name: event.target.value })}
      value={item.name}
    />
  ) : (
    <Input
      aria-label="Asset name"
      className="h-7 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm font-semibold shadow-none focus-visible:ring-2"
      onChange={(event) => patchAsset(item.key, { name: event.target.value })}
      value={item.name}
    />
  );
  return (
    <CardFrame
      box={picked ? item.imageBox : (inherited?.box ?? item.reviewer?.track?.samples[0]?.box ?? item.reviewer?.stillBox ?? null)}
      frameSrc={picked ? videoFrameUrl(videoId, item.imageTimestampSeconds ?? 0) : (inherited?.frameSrc ?? null)}
      header={
        <>
          <Badge className="shrink-0 px-1.5 py-0 font-mono text-[10px] font-bold" variant="outline">
            {draftDisplayId(item)}
          </Badge>
          {nameInput}
        </>
      }
      imageUrl={item.imageUrl}
      name={`${draftDisplayId(item)} · ${draftItemName(item)}`}
      notLocated={inherited?.notLocated ?? false}
      onSeek={onSeek}
      previewVideoSrc={browserPreview ? videoSrc : null}
      seconds={item.timestampSeconds}
    >
      {isDraftEntity(item) && (
        <Select onValueChange={(val) => patchEntity(item.key, { type: val })} value={item.type}>
          <SelectTrigger aria-label="Entity type" className="h-8 w-full bg-white text-xs">
            <SelectValue placeholder="Entity type" />
          </SelectTrigger>
          <SelectContent position="popper">
            {EDITABLE_ENTITY_TYPES.map((type) => (
              <SelectItem key={type} value={type}>
                {capitalize(type)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Textarea
        aria-label="Description"
        className="mt-1 min-h-[60px] text-xs"
        onChange={(event) =>
          isDraftEntity(item)
            ? patchEntity(item.key, { description: event.target.value })
            : isDraftInstrument(item)
              ? patchInstrument(item.key, { description: event.target.value })
              : patchAsset(item.key, { description: event.target.value })
        }
        value={item.description}
      />
      {isDraftInstrument(item) && (
        <div className="mt-1 grid grid-cols-2 gap-1.5">
          <Select
            onValueChange={(val) => patchInstrument(item.key, { threat_level: val === '__none__' ? null : Number(val) })}
            value={item.threat_level ? String(item.threat_level) : '__none__'}
          >
            <SelectTrigger aria-label="Threat level" className="h-8 w-full bg-white text-xs">
              <SelectValue placeholder="Threat level…" />
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value="__none__">Threat level…</SelectItem>
              {[1, 2, 3, 4, 5].map((level) => (
                <SelectItem key={level} value={String(level)}>
                  threat {level}/5
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            onValueChange={(val) => patchInstrument(item.key, { holderKey: val === '__none__' ? null : val })}
            value={item.holderKey ?? '__none__'}
          >
            <SelectTrigger aria-label="Held by" className="h-8 w-full bg-white text-xs">
              <SelectValue placeholder="Holder not determined" />
            </SelectTrigger>
            <SelectContent position="popper">
              <SelectItem value="__none__">Holder not determined</SelectItem>
              {holderOptions.map((option) => (
                <SelectItem key={option.key} value={option.key}>
                  held by {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
      {isDraftInstrument(item) && (
        <p className="text-ink/50">
          Held by <b>{draftHolder(item)}</b>
        </p>
      )}
      {item.reviewer ? (
        <>
          <p className="text-ink/50">
            First seen at <b className="font-mono">{clock(item.timestampSeconds)}</b> · this frame is the evidence image
          </p>
          <p className="flex flex-wrap items-center gap-1.5 text-ink/50" data-testid="reviewer-track-line">
            <Badge className="text-[10px]" variant="outline">
              Added by reviewer
            </Badge>
            {item.reviewer.track ? (
              <span>
                Your track · {draftItemName(item)} · {clock(item.reviewer.track.span[0])}–{clock(item.reviewer.track.span[1])}
              </span>
            ) : (
              <span>Still image</span>
            )}
            {item.reviewer.track?.partial && (
              <Badge className="border-clay/40 text-[10px] text-clay" variant="outline">
                Incomplete track
              </Badge>
            )}
          </p>
        </>
      ) : (
        <p className="text-ink/50">
          Most visible at <b className="font-mono">{clock(item.timestampSeconds)}</b>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-1.5 pt-1 text-[11px] font-semibold">
        <Button
          className="h-6 rounded-full px-2 text-[11px] text-ink hover:bg-ink/5"
          data-testid="edit-track"
          onClick={onEditTrack}
          size="xs"
          type="button"
          variant="ghost"
        >
          Edit track
        </Button>
        <Button
          className="h-6 rounded-full px-2 text-[11px] text-clay hover:bg-clay/10 hover:text-clay"
          onClick={onRemove}
          size="xs"
          type="button"
          variant="ghost"
        >
          Delete
        </Button>
        {onRevert && (
          <Button
            className="h-6 rounded-full px-2 text-[11px] text-moss hover:bg-moss/10 hover:text-moss"
            onClick={onRevert}
            size="xs"
            type="button"
            variant="ghost"
          >
            Revert to A
          </Button>
        )}
      </div>
    </CardFrame>
  );
}

function CardFrame({
  box = null,
  frameSrc = null,
  header,
  imageUrl,
  kind,
  name,
  notLocated = false,
  seconds,
  onSeek,
  previewVideoSrc = null,
  children,
}: {
  box?: readonly number[] | null;
  /** The whole video frame to draw the box on; the image is shown whole when there is none. */
  frameSrc?: string | null;
  header: React.ReactNode;
  imageUrl: string | null;
  kind?: BoxItemType;
  name: string;
  notLocated?: boolean;
  seconds: number | null;
  onSeek: (seconds: number) => void;
  previewVideoSrc?: string | null;
  children: React.ReactNode;
}) {
  return (
    <Card className="w-full max-w-88 gap-0 overflow-hidden rounded-xl border border-border/80 bg-card p-0 text-xs shadow-xs">
      <button
        aria-label={seconds === null ? `${name} image` : `Play video from ${formatClock(seconds)}: ${name}`}
        className="block w-full disabled:cursor-default focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
        disabled={seconds === null}
        onClick={() => seconds !== null && onSeek(seconds)}
        type="button"
      >
        {previewVideoSrc && seconds !== null ? (
          <DraftVideoFrame box={box} label={name || 'Evidence'} seconds={seconds} src={previewVideoSrc} />
        ) : (
          <EvidenceFrame
            aspect={16 / 9}
            box={box}
            className="rounded-none!"
            frameSrc={frameSrc}
            fullFrame
            kind={kind}
            label={name || 'Evidence'}
            notLocated={notLocated}
            seconds={seconds}
            src={imageUrl}
          />
        )}
      </button>
      <CardHeader className="gap-0 border-b border-border/70 bg-primary/5 px-3 py-2.5">
        <CardTitle className="flex min-w-0 items-center gap-2 text-sm font-semibold">{header}</CardTitle>
      </CardHeader>
      <div className="flex flex-col gap-1 p-3">{children}</div>
    </Card>
  );
}

function DraftVideoFrame({ box, label, seconds, src }: { box: readonly number[] | null; label: string; seconds: number; src: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    const video = ref.current;
    if (video && Math.abs(video.currentTime - seconds) >= 0.02) video.currentTime = seconds;
  }, [seconds]);
  return (
    <BoxCanvas box={box} drawable={false} label={label} testId="draft-evidence-video-preview">
      <MutedVideo
        className="h-full w-full object-fill"
        onLoadedMetadata={(event) => {
          event.currentTarget.currentTime = seconds;
        }}
        playsInline
        preload="metadata"
        ref={ref}
        src={src}
      />
    </BoxCanvas>
  );
}
