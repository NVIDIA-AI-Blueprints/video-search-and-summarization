// SPDX-License-Identifier: Apache-2.0

// Set video privacy: the report page's face-blur toggle. Switching it stores videos.blur_faces and queues one
// job through the background jobs (lib/jobs/loop.ts). In agent mode the job re-blurs: the report the page shows is kept
// and its frames, crops and clips are made again from the video, blurred as the toggle now says (modules/analysis/
// application/reblur-video.ts); no model analyses the video. In gateway mode, which stores no images, it re-analyses as
// before. The new revision is what the report shows; earlier revisions keep their images in the version history. Setting the value the video already has changes nothing and queues nothing.

import { isValidR2Key } from '@/lib/r2/key';
import type { AnalysisJobRepository } from '@/modules/jobs/domain/AnalysisJobRepository';
import type { AnalysisJob } from '@/modules/jobs/domain/job';
import { StoreRejection } from '@/modules/shared/domain/common';
import type { VideoRepository } from '@/modules/videos/domain/VideoRepository';

export interface VideoPrivacyChange {
  videoId: string;
  blurFaces: boolean;
  /** The job queued for the change; null when the toggle already had this value. */
  job: AnalysisJob | null;
}

/**
 * Stores the toggle, then queues the job (the re-blur) with `model`, which labels the new run only if the source run names none. Throws StoreRejection: 'not-found' for an unknown video,
 * 'invalid' for one with no stored R2 file to analyse again. When the job cannot be queued, the toggle is put back
 * before the error is rethrown, so the toggle never says blurred while no blurred run is coming.
 */
export async function setVideoPrivacy(
  deps: { videos: VideoRepository; jobs: AnalysisJobRepository },
  videoId: string,
  blurFaces: boolean,
  model: string,
): Promise<VideoPrivacyChange> {
  const video = await deps.videos.getVideo(videoId);
  if (!video) throw new StoreRejection('not-found', `Video not found: ${videoId}`);
  if (video.blurFaces === blurFaces) return { videoId, blurFaces, job: null };
  if (!isValidR2Key(video.storageKey)) throw new StoreRejection('invalid', 'This video has no stored file to analyse again');
  if (!(await deps.videos.setBlurFaces(videoId, blurFaces))) throw new StoreRejection('not-found', `Video not found: ${videoId}`);
  try {
    const job = await deps.jobs.enqueue({
      filePath: video.storageKey,
      filename: video.storageKey.split('/').pop() || videoId,
      model,
      reanalyseVideoId: videoId,
      blurFaces,
    });
    return { videoId, blurFaces, job };
  } catch (error) {
    await deps.videos.setBlurFaces(videoId, video.blurFaces).catch(() => false);
    throw error;
  }
}
