// SPDX-License-Identifier: Apache-2.0

import { StoreRejection, type Page } from '@/modules/shared/domain/common';
import type {
  AttemptRunRecord,
  OfficialChange,
  RevisionCropsChange,
  RevisionRunRecord,
  RunHistoryRecords,
  VideoListFilter,
  VideoListItem,
  VideoRecord,
  VideoRepository,
} from '@/modules/videos/domain/VideoRepository';
import type { OfficialCrop } from '@/modules/reports/domain/revision';
import type { PostgrestClient } from '@/lib/postgrest/client';
import { explicitVideoId } from '@/modules/reports/domain/run-notes';

type Row = Record<string, unknown>;

const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const numberOrNull = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const asRow = (value: unknown): Row => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Row) : {});
const first = (value: unknown): Row => (Array.isArray(value) ? asRow(value[0]) : asRow(value));

function videoRecord(videoId: string, row: Row): VideoRecord {
  return {
    videoId,
    storageKey: text(row.filepath),
    uploadedAt: text(row.uploaded_datetime),
    source: text(row.source),
    officialRevisionId: numberOrNull(row.official_revision_id),
  };
}

/**
 * set_official_revision and set_revision_crops RAISE plain exceptions; its domain refusals become StoreRejection,
 * anything else is rethrown unchanged.
 */
function officialRefusal(error: unknown): never {
  const message = error instanceof Error ? error.message : '';
  const match = /"message":"([^"]+)"/.exec(message);
  if (match && /not found|embedding|searchable revision/i.test(match[1])) {
    throw new StoreRejection(/not found/i.test(match[1]) ? 'not-found' : 'invalid', match[1]);
  }
  throw error;
}

function videoListItem(row: Row): VideoListItem {
  const revisions = Array.isArray(row.report_revisions) ? row.report_revisions : [];
  const filepath = typeof row.filepath === 'string' ? row.filepath : '';
  return {
    videoId: String(row.id),
    filename: filepath.split('/').pop() || String(row.id),
    uploadedAt: text(row.uploaded_datetime),
    source: text(row.source),
    revisions: revisions.length,
    officialRevisionId: numberOrNull(row.official_revision_id),
  };
}

export class SupabaseVideoAdapter implements VideoRepository {
  private readonly db: PostgrestClient;

  constructor(db: PostgrestClient) {
    this.db = db;
  }

  async listVideos(filter: VideoListFilter, page: number, pageSize: number): Promise<Page<VideoListItem>> {
    if (filter === 'failed') {
      // The PostgreSQL schema records failed attempts in model_runs.notes, not as video flags;
      // there are no standalone failed video rows.
      return { items: [], totalItems: 0 };
    }
    // videos and report_revisions are linked by two foreign keys (a revision -> its video, and
    // a video -> its official revision), so every embed names the revision -> video one.
    const base = 'id,filepath,uploaded_datetime,source,official_revision_id';
    const embed = 'report_revisions!report_revisions_video_id_fkey';
    const isWithoutReport = filter === 'without-report' || filter === 'needs-analysis';
    const query: Record<string, string> = {
      select: isWithoutReport
        ? `${base},${embed}(id)`
        : filter === 'awaiting-selection' || filter === 'with-report'
          ? `${base},${embed}!inner(id)`
          : `${base},${embed}(id)`,
      order: 'uploaded_datetime.desc.nullslast,id.asc',
    };
    if (isWithoutReport) query.report_revisions = 'is.null';
    if (filter === 'awaiting-selection') query.official_revision_id = 'is.null';
    const { rows, total } = await this.db.selectPage('videos', query, pageSize, (page - 1) * pageSize);
    return { items: rows.map(videoListItem), totalItems: total };
  }

  async getVideo(videoId: string): Promise<VideoRecord | null> {
    const row = await this.db.selectOne('videos', { id: videoId });
    return row ? videoRecord(videoId, row) : null;
  }

  async listRunHistory(videoId: string): Promise<RunHistoryRecords | null> {
    const row = await this.db.selectOne('videos', { id: videoId });
    if (!row) return null;
    const [revisionRows, attemptRows] = await Promise.all([
      this.db.selectMany('report_revisions', {
        filters: { video_id: videoId, kind: 'ai' },
        select: 'id,model_run_id,incident_type,severity_level,created_at,model_runs(id,model_name,prompt_version,run_datetime,notes)',
      }),
      this.db.selectContaining('model_runs', 'notes', `"videoId":"${videoId}"`, 'id,model_name,prompt_version,run_datetime,notes'),
    ]);
    const revisions: RevisionRunRecord[] = [];
    for (const revisionRow of revisionRows) {
      const revisionId = numberOrNull(revisionRow.id);
      if (revisionId === null) continue;
      const modelRun = first(revisionRow.model_runs);
      revisions.push({
        revisionId,
        modelRunId: text(revisionRow.model_run_id) ?? '',
        incidentType: text(revisionRow.incident_type),
        severityLevel: numberOrNull(revisionRow.severity_level),
        createdAt: text(revisionRow.created_at),
        modelName: text(modelRun.model_name),
        runDatetime: text(modelRun.run_datetime),
        notes: text(modelRun.notes),
      });
    }
    const attempts: AttemptRunRecord[] = attemptRows.map((attempt) => ({
      modelRunId: text(attempt.id) ?? '',
      modelName: text(attempt.model_name),
      runDatetime: text(attempt.run_datetime),
      notes: text(attempt.notes),
    }));
    // A run used by more than one video is a batch run; its history is not this video's alone.
    const usage = revisions.length
      ? await this.db.selectIn(
          'report_revisions',
          'model_run_id',
          revisions.map((revision) => revision.modelRunId),
          'model_run_id,video_id',
        )
      : [];
    const videosPerRun = new Map<string, Set<string>>();
    for (const usageRow of usage) {
      const set = videosPerRun.get(String(usageRow.model_run_id)) ?? new Set<string>();
      set.add(String(usageRow.video_id));
      videosPerRun.set(String(usageRow.model_run_id), set);
    }
    const sharedRunIds = new Set([...videosPerRun].filter(([, set]) => set.size > 1).map(([id]) => id));
    return { video: videoRecord(videoId, row), revisions, attempts, sharedRunIds };
  }

  async setOfficialRevision(videoId: string, revisionId: number | null, crops: OfficialCrop[]): Promise<OfficialChange> {
    const result = asRow(
      await this.db
        .rpc('set_official_revision', {
          p_video_id: videoId,
          p_revision_id: revisionId,
          p_crops: crops,
        })
        .catch(officialRefusal),
    );
    // The RPC returns exactly these keys (the sibling's supabase/functions-final.sql).
    return {
      videoId: text(result.videoId) ?? videoId,
      officialRevisionId: numberOrNull(result.officialRevisionId),
      crops: numberOrNull(result.crops) ?? 0,
    };
  }

  async setRevisionCrops(videoId: string, revisionId: number, crops: OfficialCrop[]): Promise<RevisionCropsChange> {
    const result = asRow(
      await this.db
        .rpc('set_revision_crops', {
          p_video_id: videoId,
          p_revision_id: revisionId,
          p_crops: crops,
        })
        .catch(officialRefusal),
    );
    return {
      videoId: text(result.videoId) ?? videoId,
      revisionId: numberOrNull(result.revisionId) ?? revisionId,
      crops: numberOrNull(result.crops) ?? 0,
    };
  }

  async deleteVideo(videoId: string): Promise<void> {
    await this.db.deleteWhere('videos', { id: videoId });
    // revisions, evidence crops and query results cascade from the video. Then remove the run
    // records whose parsed notes name exactly this video and that no revision uses any more; the
    // notes text match is only a pre-filter.
    const candidates = await this.db.selectContaining('model_runs', 'notes', `"videoId":"${videoId}"`, 'id,notes');
    for (const candidate of candidates) {
      if (typeof candidate.id !== 'string' || explicitVideoId(candidate.notes) !== videoId) continue;
      if (await this.db.selectOne('report_revisions', { model_run_id: candidate.id }, 'id')) continue;
      await this.db.deleteWhere('model_runs', { id: candidate.id });
    }
  }
}
