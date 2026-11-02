// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';
import { z } from 'zod';

import { getAnalysisJobs, getDbAdapters } from '@/lib/container';
import { getServiceConfiguration } from '@/lib/env';
import { errorResponse, rejectionResponse } from '@/lib/http';
import { setVideoPrivacy } from '@/modules/videos/application/set-video-privacy';

// The video's face-blur toggle. PUT {blurFaces} stores it and, when it changed, queues one job for the video
// with the console's default model through the background jobs: in agent mode a re-blur (the report is kept and its
// evidence images are made again, blurred or not), in gateway mode a re-analysis. Response: { privacy: { videoId, blurFaces, job } } (job null when nothing changed). 400 for a
// malformed body, 404 for an unknown video, 422 for a video with no stored file.

const toggleSchema = z.object({ blurFaces: z.boolean() }).strict();

export async function PUT(request: Request, context: { params: Promise<{ videoId: string }> }) {
  try {
    const { videoId } = await context.params;
    const parsed = toggleSchema.safeParse(await request.json().catch(() => null));
    if (!parsed.success) return NextResponse.json({ error: 'blurFaces (true or false) is required' }, { status: 400 });
    const config = getServiceConfiguration();
    const privacy = await setVideoPrivacy(
      { videos: getDbAdapters(config).videos, jobs: getAnalysisJobs(config) },
      videoId,
      parsed.data.blurFaces,
      config.vlmModel,
    );
    return NextResponse.json({ privacy });
  } catch (error) {
    return rejectionResponse(error) ?? errorResponse(error, 'Could not change the face blur setting');
  }
}
