// SPDX-License-Identifier: Apache-2.0

// Frame and crop use cases for adding evidence: the frame of a
// video at a second, and a crop of that frame as JPEG bytes. A crop is not
// stored here: the browser holds it until the reviewer saves, and Save uploads
// it. They depend on the video port and a frame source, so they never import
// ffmpeg or R2 and are tested with fakes.

import { assertWithinVideo, FrameRequestError, parseRegion, parseSeconds } from '@/lib/evidence/frame-request';
import type { FrameRegion } from '@/lib/evidence/frame-geometry';
import type { VideoRepository } from '@/modules/videos/domain/VideoRepository';
import { isValidR2Key } from '@/lib/r2/key';

/** Reads a video's length in seconds (null when it cannot be read) and one JPEG frame, optionally cropped. */
export interface FrameSource {
  durationSeconds(storageKey: string): Promise<number | null>;
  frame(storageKey: string, seconds: number, region: FrameRegion | null): Promise<Buffer>;
}

export interface EvidenceFrameDeps {
  videos: Pick<VideoRepository, 'getVideo'>;
  source: FrameSource;
}

async function checkedVideo({ videos, source }: EvidenceFrameDeps, videoId: string, rawSeconds: unknown) {
  const seconds = parseSeconds(rawSeconds);
  const video = await videos.getVideo(videoId);
  if (!video) throw new FrameRequestError(404, 'Video not found');
  if (!isValidR2Key(video.storageKey)) throw new FrameRequestError(409, 'This video has no stored R2 object to read frames from');
  assertWithinVideo(seconds, await source.durationSeconds(video.storageKey));
  return { seconds, storageKey: video.storageKey };
}

/** The full frame (JPEG) of the video at `rawSeconds`. */
export async function getVideoFrame(deps: EvidenceFrameDeps, videoId: string, rawSeconds: unknown): Promise<Buffer> {
  const { seconds, storageKey } = await checkedVideo(deps, videoId, rawSeconds);
  return deps.source.frame(storageKey, seconds, null);
}

export interface EvidenceCropRequest {
  timestampSeconds: unknown;
  /** {x, y, w, h} as fractions (0-1) of the frame. */
  region: unknown;
}

/**
 * The frame at the timestamp cropped to the rectangle, as JPEG bytes. Nothing
 * is written: the caller holds the crop until the reviewer saves it.
 */
export async function cropEvidenceFrame(deps: EvidenceFrameDeps, videoId: string, request: EvidenceCropRequest): Promise<Buffer> {
  const region = parseRegion(request.region);
  const { seconds, storageKey } = await checkedVideo(deps, videoId, request.timestampSeconds);
  return deps.source.frame(storageKey, seconds, region);
}
