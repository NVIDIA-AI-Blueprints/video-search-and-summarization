// SPDX-License-Identifier: Apache-2.0

// Frame and crop use cases and request validation, with in-memory fakes.

import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { register } from 'node:module';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const { cropEvidenceFrame, getVideoFrame } = await import('../src/lib/application/evidence-frames.ts');
const { FrameRequestError, parseRegion, parseSeconds } = await import('../src/lib/evidence/frame-request.ts');
const { ffmpegFrameArguments } = await import('../src/lib/video/ffmpeg-frame.ts');
const { InMemoryVideoRepository } = await import('../src/modules/videos/infrastructure/memory/videos.ts');

const V = 'vid-1';
const video = (storageKey) => ({ videoId: V, storageKey, uploadedAt: null, source: null, officialRevisionId: null, revisionIds: [] });

function deps({ storageKey = 'videos/a.mp4', duration = 30 } = {}) {
  const frames = [];
  return {
    frames,
    deps: {
      videos: new InMemoryVideoRepository([video(storageKey)]),
      source: {
        durationSeconds: async () => duration,
        frame: async (key, seconds, region) => {
          frames.push({ key, seconds, region });
          return Buffer.from('jpeg');
        },
      },
    },
  };
}

const rejects = (promise, status, pattern) =>
  assert.rejects(promise, (e) => e instanceof FrameRequestError && e.status === status && pattern.test(e.message));
const region = { x: 0.1, y: 0.2, w: 0.3, h: 0.4 };

test('a frame is read at a timestamp inside the video', async () => {
  const { deps: d, frames } = deps();
  assert.equal((await getVideoFrame(d, V, '12.5')).toString(), 'jpeg');
  assert.deepEqual(frames, [{ key: 'videos/a.mp4', seconds: 12.5, region: null }]);
});

test('a timestamp that is malformed, negative or past the end is refused', async () => {
  const { deps: d } = deps({ duration: 30 });
  for (const bad of [null, '', 'abc', '-1', 'Infinity']) await rejects(getVideoFrame(d, V, bad), 400, /timestamp/, bad);
  await rejects(getVideoFrame(d, V, 30), 422, /outside the video/);
  await rejects(getVideoFrame(d, V, 99), 422, /30 s long/);
});

test('an unknown video is 404 and one without an R2 object is 409', async () => {
  await rejects(getVideoFrame(deps().deps, 'nope', 1), 404, /not found/);
  await rejects(getVideoFrame(deps({ storageKey: '/vst/a.mp4' }).deps, V, 1), 409, /no stored R2 object/);
});

test('an unreadable video length leaves the bound to the decoder', async () => {
  const { deps: d } = deps({ duration: null });
  await getVideoFrame(d, V, 9999);
});

test('a crop is cut from the frame at its second and returned as JPEG, with nothing stored', async () => {
  const { deps: d, frames } = deps();
  const jpeg = await cropEvidenceFrame(d, V, { timestampSeconds: 4, region });
  assert.equal(jpeg.toString(), 'jpeg');
  assert.deepEqual(frames, [{ key: 'videos/a.mp4', seconds: 4, region }]);
});

test('a bad rectangle or timestamp is refused before any frame is cut', async () => {
  const { deps: d, frames } = deps();
  const crop = (over) => cropEvidenceFrame(d, V, { timestampSeconds: 1, region, ...over });
  await rejects(crop({ region: { x: 0.8, y: 0, w: 0.3, h: 0.5 } }), 422, /inside the frame/);
  await rejects(crop({ region: { x: -0.1, y: 0, w: 0.3, h: 0.5 } }), 422, /inside the frame/);
  await rejects(crop({ region: { x: 0, y: 0, w: 0, h: 0.5 } }), 422, /at least/);
  await rejects(crop({ region: { x: 0, y: 0, w: 0.5 } }), 400, /four numbers/);
  await rejects(crop({ region: null }), 400, /four numbers/);
  await rejects(crop({ timestampSeconds: 31 }), 422, /outside the video/);
  assert.deepEqual(frames, []);
});

test('a rectangle touching the frame edge is accepted, float noise is clamped', () => {
  assert.deepEqual(parseRegion({ x: 0, y: 0, w: 1, h: 1 }), { x: 0, y: 0, w: 1, h: 1 });
  assert.equal(parseRegion({ x: 0.7, y: 0, w: 0.3000001, h: 1 }).w, 1 - 0.7);
  assert.equal(parseSeconds('0'), 0);
});

test('ffmpeg is told to seek, crop to fractions and write one JPEG to stdout', () => {
  const args = ffmpegFrameArguments('https://r2.test/v.mp4', 12.5, region);
  assert.deepEqual(args.slice(args.indexOf('-ss'), args.indexOf('-ss') + 4), ['-ss', '12.5', '-i', 'https://r2.test/v.mp4']);
  assert.ok(args.includes('crop=w=trunc(iw*0.3):h=trunc(ih*0.4):x=trunc(iw*0.1):y=trunc(ih*0.2)'));
  assert.ok(args.includes('file,https,tls,tcp,crypto'));
  assert.equal(args.at(-1), 'pipe:1');
  assert.ok(!ffmpegFrameArguments('u', 1, null).includes('-vf'));
});

test('ffmpeg frame failures report a terminating signal instead of a null exit code', async () => {
  const source = await readFile(new URL('../src/lib/video/ffmpeg-frame.ts', import.meta.url), 'utf8');
  assert.match(source, /signal \? `terminated by \$\{signal\}` : `exit code \$\{code\}`/);
  assert.match(source, /terminated by SIGSEGV/);
  assert.match(source, /mkdtemp\(join\(tmpdir\(\), 'incident-frame-'\)\)/);
  assert.match(source, /await rm\(directory, \{ force: true, recursive: true \}\)/);
});
