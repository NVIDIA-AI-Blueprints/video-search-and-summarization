// SPDX-License-Identifier: Apache-2.0

// ClipStore over Cloudflare R2 and the host's ffmpeg, using the console's R2 configuration.

import { Readable } from 'node:stream';

import type { ServiceConfiguration } from '@/lib/env';
import { createR2PlaybackUrl, putR2Video, r2ObjectExists, readR2VideoDurationSeconds, verifyR2Video } from '@/lib/r2/config';
import type { RunCommand } from '@/lib/video/command';
import { cutClipMp4 } from '@/lib/video/ffmpeg-clip';
import type { ClipStore } from '@/modules/reports/application/get-evidence-clip';

const isPreconditionFailed = (error: unknown) => (error as { $metadata?: { httpStatusCode?: number } }).$metadata?.httpStatusCode === 412;

export class R2EvidenceClipStore implements ClipStore {
  private readonly config: ServiceConfiguration;
  private readonly run: RunCommand | undefined;

  constructor(config: ServiceConfiguration, run?: RunCommand) {
    this.config = config;
    this.run = run;
  }

  exists(key: string): Promise<boolean> {
    return r2ObjectExists(this.config, key);
  }

  async durationSeconds(videoKey: string): Promise<number | null> {
    const { contentLength } = await verifyR2Video(this.config, videoKey);
    return readR2VideoDurationSeconds(this.config, videoKey, contentLength);
  }

  async cut(videoKey: string, window: { startSeconds: number; lengthSeconds: number }): Promise<Uint8Array> {
    return cutClipMp4(await createR2PlaybackUrl(this.config, videoKey), window, { run: this.run });
  }

  async put(key: string, bytes: Uint8Array): Promise<void> {
    try {
      await putR2Video(this.config, key, Readable.from([Buffer.from(bytes)]), 'video/mp4', bytes.length);
    } catch (error) {
      // Another console process stored the same clip first: it was cut from the same second, so it is kept.
      if (!isPreconditionFailed(error)) throw error;
    }
  }

  sign(key: string): Promise<string> {
    return createR2PlaybackUrl(this.config, key);
  }
}
