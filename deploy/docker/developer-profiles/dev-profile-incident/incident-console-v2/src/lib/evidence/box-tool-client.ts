// SPDX-License-Identifier: Apache-2.0

// The box tool's two calls, in one replaceable place: it uses the typed browser client for the console's box routes
// (box-client.ts). A tracker answer with no usable track is `null` here, so the dialog can offer redraw or a still image.

import { describeBoxRegion, trackBoxRegion, type TrackBoxRequest } from '@/lib/evidence/box-client';
import type { Box, BoxDescribeResult, ReviewerTrack } from '@/modules/reports/domain/reviewer-item';

export interface BoxToolClient {
  describe(videoId: string, at: { seconds: number; box: Box }, signal?: AbortSignal): Promise<BoxDescribeResult>;
  track(videoId: string, request: TrackBoxRequest, signal?: AbortSignal): Promise<ReviewerTrack | null>;
}

export const boxToolClient: BoxToolClient = {
  describe: (videoId, at, signal) => describeBoxRegion(videoId, at.seconds, at.box, signal),
  async track(videoId, request, signal) {
    try {
      return await trackBoxRegion(videoId, request, signal);
    } catch (cause) {
      if (cause instanceof Error && /no usable track/.test(cause.message)) return null;
      throw cause;
    }
  },
};
