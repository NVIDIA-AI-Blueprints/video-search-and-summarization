// SPDX-License-Identifier: Apache-2.0

// Cuts a few seconds of a video into an H.264 MP4 with the ffmpeg from lib/video/host-binaries.ts, for an evidence clip. ffmpeg seeks by HTTP
// range over the signed R2 URL, so the video is never downloaded whole. Video only (no audio track, ever), the source's
// size up to 1920x1080, faststart so the browser can start playing before the whole clip has arrived. faststart rewrites
// the file after encoding, so the clip is written to a temporary file rather than piped.

import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { ClipError, type ClipWindow } from '@/modules/reports/domain/evidence-clip';
import { CommandMissingError, type RunCommand, runCommand } from '@/lib/video/command';

const TIMEOUT_MS = 45_000;
/** A 4-second 1080p clip at crf 23 is a few MB; anything this large is not one. */
const MAX_CLIP_BYTES = 40 * 1024 * 1024;

const SCALE = "scale=w='min(iw,1920)':h='min(ih,1080)':force_original_aspect_ratio=decrease:force_divisible_by=2";

/** ffmpeg arguments for the clip. Exported for tests. */
export function ffmpegClipArguments(inputUrl: string, window: ClipWindow, outputPath: string): string[] {
  return [
    '-v',
    'error',
    '-nostdin',
    '-y',
    '-protocol_whitelist',
    'https,tls,tcp,crypto',
    '-ss',
    String(window.startSeconds),
    '-i',
    inputUrl,
    '-t',
    String(window.lengthSeconds),
    '-map',
    '0:v:0',
    '-an',
    '-vf',
    SCALE,
    '-c:v',
    'libx264',
    '-preset',
    'veryfast',
    '-crf',
    '23',
    '-pix_fmt',
    'yuv420p',
    '-movflags',
    '+faststart',
    '-f',
    'mp4',
    outputPath,
  ];
}

const tail = (text: string) => text.trim().split('\n').slice(-3).join(' ').slice(-400);

/** The clip as MP4 bytes; ClipError 503 when ffmpeg is not installed, 502 when it fails, times out or writes nothing. */
export async function cutClipMp4(
  inputUrl: string,
  window: ClipWindow,
  options: { run?: RunCommand; tmpRoot?: string } = {},
): Promise<Uint8Array> {
  const run = options.run ?? runCommand;
  const dir = await mkdtemp(path.join(options.tmpRoot ?? tmpdir(), 'incident-clip-'));
  try {
    const outputPath = path.join(dir, 'clip.mp4');
    let result: Awaited<ReturnType<RunCommand>>;
    try {
      result = await run('ffmpeg', ffmpegClipArguments(inputUrl, window, outputPath), { timeoutMs: TIMEOUT_MS });
    } catch (error) {
      if (error instanceof CommandMissingError) {
        throw new ClipError(503, 'ffmpeg is not installed on the console host, so clips cannot be cut');
      }
      throw error;
    }
    if (result.timedOut) throw new ClipError(502, 'Cutting the clip took too long and was stopped');
    if (result.code !== 0) throw new ClipError(502, `ffmpeg could not cut the clip: ${tail(result.stderr) || `exit code ${result.code}`}`);
    const bytes = await readFile(outputPath).catch(() => null);
    if (!bytes || bytes.length === 0) throw new ClipError(502, 'ffmpeg produced an empty clip');
    if (bytes.length > MAX_CLIP_BYTES) throw new ClipError(502, 'The cut clip is unexpectedly large');
    return new Uint8Array(bytes);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}
