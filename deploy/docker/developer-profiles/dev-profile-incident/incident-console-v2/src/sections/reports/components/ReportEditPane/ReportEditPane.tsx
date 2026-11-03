// SPDX-License-Identifier: Apache-2.0

'use client';

import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
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
import { BoxTool, type ExistingItem } from '@/sections/evidence/components/BoxTool';
import { TrackEditor } from '@/sections/evidence/components/TrackEditor';
import { trackEditorSource } from '@/lib/evidence/track-editor-source';

const capitalize = (value: string | null) => (value ? value[0].toUpperCase() + value.slice(1) : 'Unclassified');
const clock = (seconds: number | null) => (seconds === null ? '—' : formatClock(seconds));
const rowKey = (row: PairRow) => `${row.kind}-${row.b?.key ?? (row.a ? savedItemId(row.a) : '')}`;

/**
 * Edit mode's pane: the version the draft started from (A) on the left and the
 * editable draft (B) on the right, field by field and evidence row by row. A
 * is never editable. Delete removes a draft item (and the instruments an entity
 * holds); Revert puts an edited or deleted item back exactly as A has it.
 * "+ Add evidence" and each card's Edit open the box tool: the reviewer draws a
 * box, the AI names and tracks it, and the result goes into the draft item
 * (add) or re-tracks it without touching its text (edit). An item's evidence
 * image is always the first second it is seen.
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
}) {
  // The box tool: adding new evidence, or re-tracking a card's item from a new box.
  const [boxTool, setBoxTool] = useState<{ row: PairRow['b'] } | null>(null);
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
  const edit = (row: PairRow) => {
    if (row.b) setBoxTool({ row: row.b });
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
      <div
        className="mt-4 grid gap-2 rounded-2xl border border-ink/10 bg-white/95 px-3 py-2 text-xs sm:grid-cols-[6rem_minmax(0,1fr)_minmax(0,1fr)] sm:gap-4"
        data-testid="compare-header"
      >
        <span className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-ink-caption">Compare</span>
        <div className="min-w-0 rounded-xl bg-[#fdf0ea] px-3 py-2" data-side="A">
          <Badge
            className="border-[#9b3518]/20 bg-white/80 font-mono text-[10.5px] font-bold uppercase tracking-wide text-[#9b3518]"
            variant="outline"
          >
            A
          </Badge>{' '}
          <b>v{aRevisionNo}</b> <span className="text-ink/50">read-only</span>
        </div>
        <div className="min-w-0 rounded-xl bg-[#eef5e6] px-3 py-2" data-side="B">
          <Badge
            className="border-moss/20 bg-white/80 font-mono text-[10.5px] font-bold uppercase tracking-wide text-moss"
            variant="outline"
          >
            B
          </Badge>{' '}
          <b>Draft</b> <span className="text-ink/50">from v{baseRevisionNo} · editable</span>
        </div>
      </div>

      <section className="mt-5" data-testid="report-fields-compare">
        <h3 className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-ink-caption">Report fields</h3>
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
                    patchIncident({ end_timestamp: event.target.value === '' ? null : Math.max(0, Math.floor(Number(event.target.value))) })
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
      </section>

      <section className="mt-6" data-testid="evidence-compare">
        <div className="flex items-center justify-between gap-3 rounded-xl border border-ink/10 bg-white/95 px-3 py-2">
          <div className="flex items-center gap-2">
            <h3 className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-ink-caption">Evidence items</h3>
            <Badge className="font-mono text-[10px]" variant="outline">
              {pairing.liveCount}
            </Badge>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-ink-caption">A is read-only · B is your draft</span>
            <Button
              className="rounded-full bg-ink px-3 text-xs font-semibold text-white hover:bg-ink/90"
              data-testid="add-evidence"
              onClick={() => setBoxTool({ row: null })}
              size="sm"
              type="button"
            >
              + Add evidence
            </Button>
          </div>
        </div>
        {pairing.rows.length === 0 ? (
          <p className="mt-3 rounded-xl border border-dashed border-ink/15 p-4 text-sm text-ink-caption" data-testid="edit-evidence-empty">
            This version has no evidence items.
          </p>
        ) : (
          <div className="mt-3 space-y-5">
            {pairing.units.map((unit) => (
              <div data-unit={unit.kind} key={unit.kind === 'entity' && unit.entity ? rowKey(unit.entity) : unit.kind}>
                {unit.kind !== 'entity' && (
                  <div className="mb-2">
                    <b className="font-mono text-[10.5px] uppercase tracking-wider text-ink-caption">
                      {unit.kind === 'loose' ? 'Holder unknown' : 'Assets'}
                    </b>
                    <span className="ml-2 text-[11px] text-ink-caption">
                      {unit.kind === 'loose' ? 'instruments no entity visibly holds' : 'no holder, no threat level'}
                    </span>
                  </div>
                )}
                {unit.entity && (
                  <PairRowView
                    aRevisionNo={aRevisionNo}
                    videoId={videoId}
                    draft={draft}
                    draftHolder={draftHolder}
                    holderOptions={holderOptions}
                    inherited={inherited(unit.entity)}
                    onRemove={remove}
                    onEdit={edit}
                    onEditTrack={editTrack}
                    onRevert={revert}
                    onSeek={onSeek}
                    patchAsset={patchAsset}
                    patchEntity={patchEntity}
                    patchInstrument={patchInstrument}
                    row={unit.entity}
                    savedHolder={savedHolder}
                  />
                )}
                {unit.rows.map((row) => (
                  <div className="sm:pl-14" key={rowKey(row)}>
                    <div className="mb-1 hidden font-mono text-[10px] uppercase tracking-wider text-ink/35 sm:block">holds ↳</div>
                    <PairRowView
                      aRevisionNo={aRevisionNo}
                      videoId={videoId}
                      draft={draft}
                      draftHolder={draftHolder}
                      holderOptions={holderOptions}
                      inherited=""
                      onRemove={remove}
                      onEdit={edit}
                      onEditTrack={editTrack}
                      onRevert={revert}
                      onSeek={onSeek}
                      patchAsset={patchAsset}
                      patchEntity={patchEntity}
                      patchInstrument={patchInstrument}
                      row={row}
                      savedHolder={savedHolder}
                    />
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </section>

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
    <div className="grid gap-1.5 border-b border-ink/10 py-3 sm:grid-cols-[6rem_minmax(0,1fr)_minmax(0,1fr)] sm:gap-4">
      <dt className="pt-0.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] text-ink-caption">{label}</dt>
      <dd className="m-0 min-w-0 text-sm leading-6 text-ink/80">{left}</dd>
      <dd className="m-0 min-w-0 text-sm leading-6 text-ink/80">{right}</dd>
    </div>
  );
}

type PatchEntity = (key: string, patch: Partial<EditDraft['entities'][number]>) => void;
type PatchInstrument = (key: string, patch: Partial<EditDraft['instruments'][number]>) => void;
type PatchAsset = (key: string, patch: Partial<EditDraft['assets'][number]>) => void;

function PairRowView({
  aRevisionNo,
  videoId,
  draft,
  row,
  inherited,
  savedHolder,
  draftHolder,
  holderOptions,
  onRemove,
  onEdit,
  onEditTrack,
  onRevert,
  onSeek,
  patchEntity,
  patchInstrument,
  patchAsset,
}: {
  aRevisionNo: number;
  videoId: string;
  draft: EditDraft;
  row: PairRow;
  inherited: string;
  savedHolder: (instrument: PairRow['a']) => string;
  draftHolder: (instrument: EditDraft['instruments'][number]) => string;
  holderOptions: Array<{ key: string; label: string }>;
  onRemove: (row: PairRow) => void;
  onEdit: (row: PairRow) => void;
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
    <div
      className="grid gap-2 border-b border-ink/10 py-3 last:border-0 sm:grid-cols-[6rem_minmax(0,1fr)_minmax(0,1fr)] sm:gap-4"
      data-row-kind={row.kind}
    >
      <span className="font-mono text-[10.5px] font-bold uppercase tracking-wider text-ink-caption">{row.kind}</span>
      <div className="min-w-0" data-side="A">
        {row.a ? (
          <ReadCard inherited={inherited} item={row.a} onSeek={onSeek} savedHolder={savedHolder} videoId={videoId} />
        ) : (
          <div
            className="flex items-center gap-2 rounded-2xl border border-dashed border-moss/40 bg-moss/5 p-4 text-xs font-semibold text-moss"
            data-testid="new-in-b"
          >
            <Badge className="border-moss/30 bg-moss/10 text-moss" variant="outline">
              New in B
            </Badge>
          </div>
        )}
      </div>
      <div className="min-w-0" data-side="B">
        {row.b ? (
          <EditCard
            draftHolder={draftHolder}
            videoId={videoId}
            saved={row.a?.evidence ?? null}
            holderOptions={holderOptions}
            item={row.b}
            onRemove={() => onRemove(row)}
            onEdit={() => onEdit(row)}
            onEditTrack={() => onEditTrack(row)}
            onRevert={changed ? () => onRevert(row) : null}
            onSeek={onSeek}
            patchAsset={patchAsset}
            patchEntity={patchEntity}
            patchInstrument={patchInstrument}
          />
        ) : (
          <div
            className="rounded-2xl border border-dashed border-ink/20 bg-[repeating-linear-gradient(45deg,#f4efe9,#f4efe9_8px,#efe7de_8px,#efe7de_16px)] p-4 text-xs text-[#9b3518]"
            data-testid="deleted-in-b"
          >
            <b className="block text-sm">Deleted</b>
            <span>Removed from your draft.</span>
            <div className="mt-2">
              <Button
                className="rounded-full border border-ink/20 bg-white px-3 py-1 text-[11px] font-semibold text-ink disabled:opacity-40"
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
    </div>
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
  return (
    <CardFrame
      box={shown.box}
      frameSrc={shown.frameSrc}
      imageUrl={shown.src}
      name={savedItemName(item)}
      notLocated={shown.notLocated}
      onSeek={onSeek}
      seconds={item.timestampSeconds}
    >
      <p className="text-sm font-semibold">{savedItemName(item)}</p>
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
  saved,
  holderOptions,
  draftHolder,
  onRemove,
  onEdit,
  onEditTrack,
  onRevert,
  onSeek,
  patchEntity,
  patchInstrument,
  patchAsset,
}: {
  item: NonNullable<PairRow['b']>;
  videoId: string;
  /** The saved item's evidence, whose frame and box the draft keeps showing until the reviewer replaces the image. */
  saved: NonNullable<PairRow['a']>['evidence'] | null;
  holderOptions: Array<{ key: string; label: string }>;
  draftHolder: (instrument: EditDraft['instruments'][number]) => string;
  onRemove: () => void;
  onEdit: () => void;
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
  return (
    <CardFrame
      box={picked ? item.imageBox : (inherited?.box ?? null)}
      frameSrc={picked ? videoFrameUrl(videoId, item.imageTimestampSeconds ?? 0) : (inherited?.frameSrc ?? null)}
      imageUrl={item.imageUrl}
      name={draftItemName(item)}
      notLocated={inherited?.notLocated ?? false}
      onSeek={onSeek}
      seconds={item.timestampSeconds}
    >
      {isDraftEntity(item) ? (
        <>
          <Input
            aria-label="Entity name"
            className="h-8 text-xs"
            onChange={(event) => patchEntity(item.key, { label: event.target.value })}
            placeholder={item.type}
            value={item.label}
          />
          <Select onValueChange={(val) => patchEntity(item.key, { type: val })} value={item.type}>
            <SelectTrigger aria-label="Entity type" className="mt-1 h-8 w-full bg-white text-xs">
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
        </>
      ) : isDraftInstrument(item) ? (
        <Input
          aria-label="Instrument name"
          className="h-8 text-xs"
          onChange={(event) => patchInstrument(item.key, { name: event.target.value })}
          value={item.name}
        />
      ) : (
        <Input
          aria-label="Asset name"
          className="h-8 text-xs"
          onChange={(event) => patchAsset(item.key, { name: event.target.value })}
          value={item.name}
        />
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
          onClick={onEdit}
          size="xs"
          type="button"
          variant="ghost"
        >
          Edit
        </Button>
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
  imageUrl,
  name,
  notLocated = false,
  seconds,
  onSeek,
  children,
}: {
  box?: readonly number[] | null;
  /** The whole video frame to draw the box on; the image is shown whole when there is none. */
  frameSrc?: string | null;
  imageUrl: string | null;
  name: string;
  notLocated?: boolean;
  seconds: number | null;
  onSeek: (seconds: number) => void;
  children: React.ReactNode;
}) {
  return (
    <Card className="w-full max-w-88 overflow-hidden rounded-2xl border border-ink/10 bg-white p-0 text-xs shadow-xs">
      <button
        aria-label={seconds === null ? `${name} image` : `Play video from ${formatClock(seconds)}: ${name}`}
        className="block w-full disabled:cursor-default focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
        disabled={seconds === null}
        onClick={() => seconds !== null && onSeek(seconds)}
        type="button"
      >
        <EvidenceFrame
          aspect={16 / 9}
          box={box}
          className="rounded-none!"
          frameSrc={frameSrc}
          fullFrame
          label={name || 'Evidence'}
          notLocated={notLocated}
          seconds={seconds}
          src={imageUrl}
        />
      </button>
      <div className="space-y-1 p-3">{children}</div>
    </Card>
  );
}
