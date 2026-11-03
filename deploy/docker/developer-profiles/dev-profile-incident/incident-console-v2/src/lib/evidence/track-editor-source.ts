// SPDX-License-Identifier: Apache-2.0

// Where the track editor starts from for a draft item: the reviewer's own track (already in the draft), the saved AI track
// (read from the track route), a still box, or nothing. Pure; the editor does the fetching.

import type { ItemEvidence } from '@/modules/reports/domain/report-view';
import type { ReviewerDraftItem } from '@/modules/reports/domain/reviewer-item';
import { boxRegion } from '@/lib/evidence/frame-geometry';
import type { StillBoxAt } from '@/modules/reports/domain/track-editor';
import type { TrackSample } from '@/modules/reports/domain/track';

export type TrackEditorBadge = 'AI track' | 'Your track' | 'Still image' | 'No track';

export interface TrackEditorSource {
  /** Samples already in hand (a reviewer's track). */
  samples: TrackSample[] | null;
  /** Same-origin URL to read the saved track from; null when `samples` is set or there is none. */
  trackUrl: string | null;
  /** A saved still box, shown dashed at its own second where there is no track. */
  still: StillBoxAt | null;
  badge: TrackEditorBadge;
}

interface DraftItemForSource {
  reviewer: ReviewerDraftItem | null;
  image: string | null;
}

/** The saved item's evidence applies while the draft still has the saved item's image (a replaced image is a new item). */
export function trackEditorSource(
  item: DraftItemForSource,
  saved: Pick<ItemEvidence, 'image' | 'trackUrl' | 'located' | 'boxSeconds' | 'boundingBox'> | null,
): TrackEditorSource {
  const reviewer = item.reviewer;
  if (reviewer?.track) return { samples: reviewer.track.samples, trackUrl: null, still: null, badge: 'Your track' };
  if (reviewer) {
    const still = reviewer.stillBox && boxRegion(reviewer.stillBox) && reviewer.stillSeconds !== undefined;
    return {
      samples: null,
      trackUrl: null,
      still: still ? { t: reviewer.stillSeconds as number, box: reviewer.stillBox as StillBoxAt['box'] } : null,
      badge: 'Still image',
    };
  }
  if (!saved || saved.image !== item.image) return { samples: null, trackUrl: null, still: null, badge: 'No track' };
  if (saved.trackUrl) return { samples: null, trackUrl: saved.trackUrl, still: null, badge: 'AI track' };
  const located = saved.located !== false && saved.boxSeconds !== null && boxRegion(saved.boundingBox);
  return {
    samples: null,
    trackUrl: null,
    still: located ? { t: saved.boxSeconds as number, box: saved.boundingBox as StillBoxAt['box'] } : null,
    badge: located ? 'Still image' : 'No track',
  };
}
