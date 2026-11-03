// SPDX-License-Identifier: Apache-2.0

// Get report: one revision's view for the report page, with its video and
// evidence images (the whole frame and the crop) signed for the browser, and
// each person's track pointed at the console's own routes.

import type { IncidentRepository, RevisionRecord } from '@/modules/reports/domain/IncidentRepository';
import type { ReportView } from '@/modules/reports/domain/report-view';
import { toReportView } from '@/modules/reports/application/report-view';
import type { SignStorageKey } from '@/modules/reports/application/types';
import type { EvidenceItemType, ReportPayload } from '@/modules/reports/domain/revision';

/** The console route the browser reads one item's track samples from (R2 has no CORS rule for the console origin). */
export function evidenceTrackUrl(videoId: string, revisionId: number, itemType: EvidenceItemType, itemId: string): string {
  const query = new URLSearchParams({ revision: String(revisionId), type: itemType, item: itemId });
  return `/api/reports/${encodeURIComponent(videoId)}/track?${query}`;
}

/** Signs each item's frame and image so the browser can show them; one that cannot be signed is shown as missing. */
async function withImageUrls(view: ReportView, sign: SignStorageKey): Promise<ReportView> {
  const signed = new Map<string, string | null>();
  const urlOf = async (image: string | null) => {
    if (!image) return null;
    if (!signed.has(image)) signed.set(image, await sign(image).catch(() => null));
    return signed.get(image) ?? null;
  };
  const { entities, instruments, assets } = view.structured;
  const items = [
    ...entities.map((item) => ({ item, type: 'entity' as const, id: item.entityId })),
    ...instruments.map((item) => ({ item, type: 'instrument' as const, id: item.instrumentId })),
    ...assets.map((item) => ({ item, type: 'asset' as const, id: item.assetId })),
  ];
  await Promise.all(
    items.map(async ({ item, type, id }) => {
      if (item.evidence) {
        const [imageUrl, frameUrl] = await Promise.all([urlOf(item.evidence.image), urlOf(item.evidence.frame)]);
        const trackUrl = item.evidence.track ? evidenceTrackUrl(view.videoId, view.revisionId, type, id) : null;
        item.evidence = { ...item.evidence, imageUrl, frameUrl, trackUrl };
      }
    }),
  );
  return view;
}

/**
 * The stored payload of the AI revision a revision derives from: its own payload for an AI revision, else the
 * nearest AI ancestor's (a human revision copies its parent's items). Null when the chain holds no AI revision.
 */
async function aiBaseline(incidents: IncidentRepository, record: RevisionRecord): Promise<ReportPayload | null> {
  let current: RevisionRecord | null = record;
  while (current?.revision.kind === 'human') {
    if (current.revision.parentId === null) return null;
    current = await incidents.getRevision(record.revision.videoId, current.revision.parentId);
  }
  return current?.payload ?? null;
}

/** One revision's report view (revisionId null: the official revision, else the newest); null when the video or revision is missing. */
export async function getReport(
  incidents: IncidentRepository,
  videoId: string,
  revisionId: number | null,
  sign: SignStorageKey,
): Promise<ReportView | null> {
  const record = await incidents.getRevision(videoId, revisionId);
  if (!record) return null;
  if (!record.video.storageKey) throw new Error('The report has no linked R2 video');
  const baseline = await aiBaseline(incidents, record);
  return withImageUrls(toReportView(record, await sign(record.video.storageKey), baseline), sign);
}
