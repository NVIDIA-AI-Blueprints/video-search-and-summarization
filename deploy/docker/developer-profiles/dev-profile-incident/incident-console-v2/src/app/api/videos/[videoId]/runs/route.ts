// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';

import { getAnalysisPorts, getDbAdapters } from '@/lib/container';
import { fetchEvidenceEmbeddings } from '@/modules/reports/infrastructure/agent/evidence-embeddings';
import { storeSearchableCrops } from '@/modules/reports/application/store-searchable-crops';
import { analysisErrorResponse } from '@/lib/http/analysis-responses';
import { runContractAnalysis } from '@/modules/analysis/application/run-contract-analysis';
import { getServiceConfiguration, isSupabaseConfigured, type ServiceConfiguration } from '@/lib/env';
import { errorResponse } from '@/lib/http';
import { createR2PlaybackUrl } from '@/lib/r2/config';
import { isValidR2Key } from '@/lib/r2/key';
import { buildRunHistory } from '@/modules/videos/application/run-history';
import type { VideoRunHistory } from '@/modules/videos/domain/run-types';

export const dynamic = 'force-dynamic';
// The slowest allowlisted model's timeout (contracts/inference.json) plus persistence.
export const maxDuration = 360;

function database(): ServiceConfiguration {
  const config = getServiceConfiguration();
  if (!isSupabaseConfigured(config)) throw new Error('Supabase PostgREST is not configured');
  return config;
}

/** The video's analysis history: every attempt, successful or failed, newest first. */
export async function GET(_request: Request, context: { params: Promise<{ videoId: string }> }) {
  try {
    const { videoId } = await context.params;
    const config = database();
    const records = await getDbAdapters(config).videos.listRunHistory(videoId);
    if (!records) return NextResponse.json({ error: 'Video not found' }, { status: 404 });
    const { video } = records;
    const { runs, counts } = buildRunHistory({
      videoId,
      revisions: records.revisions,
      attempts: records.attempts,
      sharedRunIds: records.sharedRunIds,
    });
    const filepath = video.storageKey ?? '';
    let playbackUrl: string | null = null;
    if (isValidR2Key(filepath)) {
      try {
        playbackUrl = await createR2PlaybackUrl(config, filepath);
      } catch {
        playbackUrl = null;
      }
    }
    const officialRevisionId = video.officialRevisionId;
    const history: VideoRunHistory = {
      video: {
        videoId,
        filename: filepath.split('/').pop() || videoId,
        uploadedAt: video.uploadedAt,
        source: video.source,
        playbackUrl,
      },
      runs: runs.map((run) => ({ ...run, isOfficial: run.revision !== null && run.revision.revisionId === officialRevisionId })),
      counts,
      officialRevisionId,
    };
    return NextResponse.json(history);
  } catch (error) {
    return errorResponse(error, 'Could not load the analysis history');
  }
}

/**
 * Re-analyse the stored video with an allowlisted model: the same canonical
 * request, strict validation and ID-only recovery as a new upload, always a
 * new run and a new AI revision, never touching the video row (so its upload
 * time is kept) or any earlier run or revision. additionalInstruction is the
 * reviewer prompt typed in the console.
 */
export async function POST(request: Request, context: { params: Promise<{ videoId: string }> }) {
  try {
    const { videoId } = await context.params;
    const body = (await request.json().catch(() => null)) as { model?: unknown; additionalInstruction?: unknown } | null;
    if (!body || typeof body !== 'object') return NextResponse.json({ error: 'A JSON body with model is required' }, { status: 400 });
    const config = database();
    if (config.analysisMode === 'agent') {
      if (!config.agentUrl) throw new Error('INCIDENT_AGENT_BASE_URL is not configured');
    } else if (!config.gatewayUrl) {
      throw new Error('VLM_GATEWAY_URL is not configured');
    }
    const video = await getDbAdapters(config).videos.getVideo(videoId);
    if (!video) return NextResponse.json({ error: 'Video not found' }, { status: 404 });
    if (!isValidR2Key(video.storageKey))
      return NextResponse.json({ error: 'This video has no stored R2 object to analyse' }, { status: 409 });

    const { run, revisionId } = await runContractAnalysis(
      {
        videoId,
        r2Key: video.storageKey,
        model: body.model ?? config.vlmModel,
        additionalInstruction: body.additionalInstruction,
      },
      getAnalysisPorts(config),
      config.analysisMode,
    );
    const searchable = await storeSearchableCrops(getDbAdapters(config), videoId, revisionId, (id) => fetchEvidenceEmbeddings(config, id));
    return NextResponse.json({
      run: {
        videoId: run.videoId,
        modelRunId: run.modelRunId,
        revisionId,
        model: run.request.model,
        promptVersion: run.contractVersion,
        outcome: run.status,
        searchable,
      },
    });
  } catch (error) {
    return analysisErrorResponse(error, 'Re-analysis failed');
  }
}
