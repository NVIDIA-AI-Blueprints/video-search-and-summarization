// SPDX-License-Identifier: Apache-2.0

// ReviewerEvidenceStore over Cloudflare R2: frames are decoded by ffmpeg over a signed URL (lib/r2/frame-source.ts), objects are
// written once and never overwritten.

import { Readable } from 'node:stream';

import type { ServiceConfiguration } from '@/lib/env';
import { createR2PlaybackUrl, putR2Video, r2ObjectExists } from '@/lib/r2/config';
import type { ReviewerEvidenceStore } from '@/modules/reports/application/types';

export class R2ReviewerEvidenceStore implements ReviewerEvidenceStore {
  private readonly config: ServiceConfiguration;

  constructor(config: ServiceConfiguration) {
    this.config = config;
  }

  async frame(videoKey: string, seconds: number, region: { x: number; y: number; w: number; h: number } | null): Promise<Buffer> {
    // Loaded on first use: it pulls in the ffmpeg binary, which an edit without reviewer items never needs.
    const { extractFrameJpeg } = await import('@/lib/video/ffmpeg-frame');
    return extractFrameJpeg(await createR2PlaybackUrl(this.config, videoKey), seconds, region);
  }

  exists(key: string): Promise<boolean> {
    return r2ObjectExists(this.config, key);
  }

  put(key: string, body: Buffer, contentType: string): Promise<void> {
    return putR2Video(this.config, key, Readable.from([body]), contentType, body.length);
  }
}
