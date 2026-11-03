// SPDX-License-Identifier: Apache-2.0

import { NextResponse } from 'next/server';

import { getDbAdapters } from '@/lib/container';
import { getVideoFrame } from '@/lib/application/evidence-frames';
import { FrameRequestError } from '@/lib/evidence/frame-request';
import { errorResponse } from '@/lib/http';
import { r2FrameConfiguration, r2FrameSource } from '@/lib/r2/frame-source';

export const dynamic = 'force-dynamic';
export const maxDuration = 30;

/**
 * The video's frame at ?t=<seconds> as a JPEG, for the evidence image and the box tool. 400 for a
 * missing or malformed t, 422 for a t past the end of the video, 404 for an
 * unknown video, 503 when ffmpeg is not installed on the console host.
 */
export async function GET(request: Request, context: { params: Promise<{ videoId: string }> }) {
  try {
    const { videoId } = await context.params;
    const config = r2FrameConfiguration();
    const jpeg = await getVideoFrame(
      { videos: getDbAdapters(config).videos, source: r2FrameSource(config) },
      videoId,
      new URL(request.url).searchParams.get('t'),
    );
    return new NextResponse(new Uint8Array(jpeg), { headers: { 'Content-Type': 'image/jpeg', 'Cache-Control': 'private, max-age=300' } });
  } catch (error) {
    if (error instanceof FrameRequestError) return NextResponse.json({ error: error.message }, { status: error.status });
    return errorResponse(error, 'Could not read the video frame');
  }
}
