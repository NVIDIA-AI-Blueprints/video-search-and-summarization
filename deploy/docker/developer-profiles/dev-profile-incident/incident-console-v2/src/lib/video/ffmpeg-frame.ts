// SPDX-License-Identifier: Apache-2.0

// Decodes one frame of a video as a JPEG with the ffmpeg from lib/video/host-binaries.ts, optionally
// cropped to a rectangle given as fractions of the frame. ffmpeg seeks by HTTP
// range over the signed R2 URL, so the video is never downloaded whole.

import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

import type { FrameRegion } from '@/lib/evidence/frame-geometry';
import { FrameRequestError } from '@/lib/evidence/frame-request';
import { resolveHostBinary } from '@/lib/video/host-binaries';

const TIMEOUT_MS = 20_000;
const MAX_JPEG_BYTES = 10 * 1024 * 1024;

/** ffmpeg arguments for one JPEG on stdout. Exported for tests. */
export function ffmpegFrameArguments(inputUrl: string, seconds: number, region: FrameRegion | null): string[] {
  const crop = region
    ? ['-vf', `crop=w=trunc(iw*${region.w}):h=trunc(ih*${region.h}):x=trunc(iw*${region.x}):y=trunc(ih*${region.y})`]
    : [];
  return [
    '-v',
    'error',
    '-nostdin',
    '-protocol_whitelist',
    'file,https,tls,tcp,crypto',
    '-ss',
    String(seconds),
    '-i',
    inputUrl,
    '-frames:v',
    '1',
    ...crop,
    '-c:v',
    'mjpeg',
    '-pix_fmt',
    'yuvj420p',
    '-q:v',
    '2',
    '-f',
    'image2pipe',
    'pipe:1',
  ];
}

async function extractFrameJpegOnce(inputUrl: string, seconds: number, region: FrameRegion | null): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const child = spawn(resolveHostBinary('ffmpeg'), ffmpegFrameArguments(inputUrl, seconds, region), {
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const chunks: Buffer[] = [];
    let size = 0;
    let stderr = '';
    let settled = false;
    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      action();
    };
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      finish(() => reject(new FrameRequestError(502, 'Reading the frame from the video timed out')));
    }, TIMEOUT_MS);

    child.stdout.on('data', (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_JPEG_BYTES) {
        child.kill('SIGKILL');
        finish(() => reject(new FrameRequestError(502, 'The decoded frame is unexpectedly large')));
        return;
      }
      chunks.push(chunk);
    });
    child.stderr.on('data', (chunk: Buffer) => {
      stderr = (stderr + chunk.toString()).slice(-400);
    });
    child.on('error', (error: NodeJS.ErrnoException) =>
      finish(() =>
        reject(
          error.code === 'ENOENT'
            ? new FrameRequestError(503, 'ffmpeg is not installed on the console host, so frames cannot be extracted')
            : error,
        ),
      ),
    );
    child.on('close', (code, signal) =>
      finish(() => {
        if (code === 0 && size > 0) resolve(Buffer.concat(chunks as unknown as Uint8Array[]));
        else if (code === 0) reject(new FrameRequestError(422, 'The video has no frame at that timestamp'));
        else
          reject(
            new FrameRequestError(
              502,
              `ffmpeg could not read the video: ${stderr.trim() || (signal ? `terminated by ${signal}` : `exit code ${code}`)}`,
            ),
          );
      }),
    );
  });
}

async function downloadVideo(inputUrl: string, target: string): Promise<void> {
  const response = await fetch(inputUrl, { cache: 'no-store', signal: AbortSignal.timeout(120_000) });
  if (!response.ok || !response.body) throw new FrameRequestError(502, `The video download failed (${response.status})`);
  await pipeline(Readable.fromWeb(response.body as never), createWriteStream(target));
}

/**
 * Extracts one frame over the signed R2 URL. Some ffmpeg-static builds segfault in their HTTPS/range reader for otherwise
 * valid MP4s; only for that signal, retry from a streamed temporary local file and always remove it afterwards.
 */
export async function extractFrameJpeg(inputUrl: string, seconds: number, region: FrameRegion | null): Promise<Buffer> {
  try {
    return await extractFrameJpegOnce(inputUrl, seconds, region);
  } catch (error) {
    if (!(error instanceof FrameRequestError) || !/terminated by SIGSEGV/.test(error.message) || !/^https:/.test(inputUrl)) throw error;
    const directory = await mkdtemp(join(tmpdir(), 'incident-frame-'));
    const localVideo = join(directory, 'video.mp4');
    try {
      await downloadVideo(inputUrl, localVideo);
      return await extractFrameJpegOnce(localVideo, seconds, region);
    } finally {
      await rm(directory, { force: true, recursive: true });
    }
  }
}
