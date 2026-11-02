// SPDX-License-Identifier: Apache-2.0

// Pure rules for the box tool in Edit mode: reading the describe and track routes' answers, changing a track the way the
// preview buttons do (extend, gone from here on), and putting the finished item into the edit draft. The evidence image of
// an item is always the first second it is seen: the track's first sample, or the still image's second.

import { addReviewerItem, type EditDraft } from '@/modules/reports/domain/edit-draft';
import { type Box, type ReviewerDraftItem as BoxToolItem, type ReviewerTrack, firstSeen } from '@/modules/reports/domain/reviewer-item';
import type { EvidenceItemType as BoxItemType } from '@/modules/reports/domain/revision';
import type { TrackSample } from '@/modules/reports/domain/track';

type ReviewerTrackSample = ReviewerTrack['samples'][number];

/** How far one "Extend" button reaches, and the window either side of a fresh box. */
export const TRACK_WINDOW_SECONDS = 3;

const spanOf = (samples: readonly ReviewerTrackSample[]): [number, number] => [samples[0].t, samples[samples.length - 1].t];

/** The track after a further run over one side of it: the new samples win where the two overlap, and the latest run says whether it was lost. */
export function extendTrack(track: ReviewerTrack, run: ReviewerTrack, direction: 'back' | 'on'): ReviewerTrack {
  const [first, last] = track.span;
  const kept = track.samples.filter((sample) => (direction === 'back' ? sample.t >= first : sample.t <= last));
  const fresh = run.samples.filter((sample) => (direction === 'back' ? sample.t < first : sample.t > last));
  const samples = [...kept, ...fresh].sort((a, b) => a.t - b.t);
  return { source: 'reviewer', samples, span: spanOf(samples), partial: run.partial, lostAt: run.lostAt };
}

/** The track ended at a second ("Gone from here on"): later samples go, and it is no longer partial. Null when nothing would be left. */
export function endTrackAt(track: ReviewerTrack, seconds: number): ReviewerTrack | null {
  const samples = track.samples.filter((sample) => sample.t <= seconds);
  if (!samples.length) return null;
  return { source: 'reviewer', samples, span: spanOf(samples), partial: false, lostAt: null };
}

/** The body of a track run that extends an existing track one window to one side, seeded at its edge sample. */
export function extensionRequest(track: ReviewerTrack, direction: 'back' | 'on') {
  const edge = direction === 'back' ? track.samples[0] : track.samples[track.samples.length - 1];
  return {
    seconds: edge.t,
    box: edge.box,
    backSeconds: direction === 'back' ? TRACK_WINDOW_SECONDS : 0,
    forwardSeconds: direction === 'on' ? TRACK_WINDOW_SECONDS : 0,
  };
}

/** Holder names offered on the card: the draft's entities by name, never by E1-style ids. */
export function holderChoices(draft: EditDraft): Array<{ key: string; name: string }> {
  const names = draft.entities.map((entity) => entity.label || entity.type);
  return draft.entities.map((entity, index) => {
    const name = names[index];
    const same = names.filter((other) => other === name).length > 1;
    return { key: entity.key, name: same ? `${name} (${names.slice(0, index + 1).filter((other) => other === name).length})` : name };
  });
}

/** The seen second, evidence box and (for a track) the span: where the item's evidence image is cut from. */
function seenPatch(item: BoxToolItem) {
  const seen = firstSeen(item);
  const second = Math.max(0, Math.round(seen.seconds));
  const last = item.track ? Math.max(second, Math.ceil(item.track.samples[item.track.samples.length - 1].t)) : second;
  return {
    fields: { timestampSeconds: second, imageTimestampSeconds: second, imageBox: [...seen.box], reviewer: item },
    span: { first: Math.min(second, Math.floor(seen.seconds)), last },
  };
}

/** A new draft item from the box tool, as an entity, instrument or asset by the reviewer's chosen type. */
export function addBoxItem(draft: EditDraft, item: BoxToolItem, holderKey: string | null): EditDraft {
  const added = addReviewerItem(draft, item);
  if (item.itemType !== 'instrument') return added;
  // The card picks the holder by key; the draft's own lookup only matched a name.
  const instruments = added.instruments.map((instrument, index) =>
    index === added.instruments.length - 1 ? { ...instrument, holderKey } : instrument,
  );
  return { ...added, instruments };
}

/** An existing draft item re-tracked from a new box: its text and holder stay, its evidence image and seen span follow the track. */
export function retrackDraftItem(draft: EditDraft, kind: BoxItemType, key: string, item: BoxToolItem): EditDraft {
  const { fields, span } = seenPatch(item);
  if (kind === 'entity') {
    return {
      ...draft,
      entities: draft.entities.map((entity) =>
        entity.key === key ? { ...entity, ...fields, firstSeenSeconds: span.first, lastSeenSeconds: span.last } : entity,
      ),
    };
  }
  if (kind === 'instrument') {
    return {
      ...draft,
      instruments: draft.instruments.map((instrument) => (instrument.key === key ? { ...instrument, ...fields } : instrument)),
    };
  }
  return { ...draft, assets: draft.assets.map((asset) => (asset.key === key ? { ...asset, ...fields } : asset)) };
}

export type { Box, BoxItemType, BoxToolItem, TrackSample };
