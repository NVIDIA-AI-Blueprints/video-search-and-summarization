// SPDX-License-Identifier: Apache-2.0

// The track editor's model: boxAt states, keyframe edits, removed stretches, start/stop, thinning on load and densifying on Done.

import assert from 'node:assert/strict';
import { register } from 'node:module';
import { test } from 'node:test';

register('./support/alias-loader.mjs', import.meta.url);

const m = await import('../src/modules/reports/domain/track-editor.ts');
const { trackBoxAt } = await import('../src/modules/reports/domain/track.ts');

const box = (n) => [100 + n, 100 + n, 300 + n, 300 + n];
const track = (over = {}) => ({
  start: 2,
  stop: 18,
  kfs: [
    { t: 2, box: box(0) },
    { t: 6, box: box(40) },
    { t: 12, box: box(100) },
    { t: 18, box: box(60) },
  ],
  ...over,
});

test('snapTenth rounds to a tenth without float noise', () => {
  assert.equal(m.snapTenth(1.04), 1);
  assert.equal(m.snapTenth(1.06), 1.1);
  assert.equal(m.snapTenth(0.30000000000000004), 0.3);
  assert.equal(m.snapTenth(-0.01), 0);
});

test('formatEditorClock reads m:ss.t', () => {
  assert.equal(m.formatEditorClock(0), '0:00.0');
  assert.equal(m.formatEditorClock(7.8), '0:07.8');
  assert.equal(m.formatEditorClock(75.3), '1:15.3');
});

test('boxAt names the five states', () => {
  const t = track();
  assert.deepEqual(m.boxAt(t, null, 6), { box: box(40), state: 'keyframe', index: 1 });
  const between = m.boxAt(t, null, 4);
  assert.equal(between.state, 'between');
  assert.deepEqual(between.box, box(20));
  assert.equal(m.boxAt(t, null, 1).state, 'outside');
  assert.equal(m.boxAt(t, null, 19).state, 'outside');
  assert.equal(m.boxAt(null, null, 3).state, 'none');
  const held = m.boxAt(track({ start: 0, stop: 20 }), null, 0.5);
  assert.deepEqual([held.state, held.box], ['held', box(0)]);
  assert.equal(m.boxAt(track({ start: 0, stop: 20 }), null, 19.5).state, 'held');
});

test('with no track a saved still box shows at its own second only', () => {
  const still = { t: 11, box: box(0) };
  assert.deepEqual(m.boxAt(null, still, 11), { box: box(0), state: 'still' });
  assert.equal(m.boxAt(null, still, 12).state, 'none');
  assert.equal(m.drawsBox('still'), true);
  assert.equal(m.drawsBox('keyframe'), false);
  assert.equal(m.drawsBox('between'), false);
});

test('setKeyframe updates one at the second, adds one between, and widens the range', () => {
  const updated = m.setKeyframe(track(), 6, box(5), 20);
  assert.equal(updated.kfs.length, 4);
  assert.deepEqual(updated.kfs[1].box, box(5));
  const added = m.setKeyframe(track(), 9, box(5), 20);
  assert.deepEqual(
    added.kfs.map((k) => k.t),
    [2, 6, 9, 12, 18],
  );
  const wider = m.setKeyframe(track(), 19.5, box(5), 20);
  assert.equal(wider.stop, 19.5);
  const earlier = m.setKeyframe(track(), 1, box(5), 20);
  assert.equal(earlier.start, 1);
});

test('setKeyframe clamps and orders the box', () => {
  const t = m.setKeyframe(track(), 6, [400, 900, -20, 1200], 20);
  assert.deepEqual(t.kfs[1].box, [0, 900, 400, 1000]);
});

test('a first box starts a two second track, shortened to the clip end', () => {
  assert.deepEqual(m.setKeyframe(null, 11, box(0), 20), { start: 11, stop: 13, kfs: [{ t: 11, box: box(0) }] });
  const late = m.setKeyframe(null, 19.8, box(0), 20);
  assert.equal(late.stop, 20);
  assert.ok(late.stop - late.start >= m.MIN_TRACK_SECONDS - 1e-9);
  assert.ok(late.start <= 19.8);
  assert.equal(m.setKeyframe(null, 5, box(0), null).stop, 7);
});

test('moveKeyframe stays 0.1 s from its neighbours and inside start..stop', () => {
  assert.equal(m.moveKeyframe(track(), 1, 5).kfs[1].t, 5);
  assert.equal(m.moveKeyframe(track(), 1, 99).kfs[1].t, 11.9);
  assert.equal(m.moveKeyframe(track(), 1, -4).kfs[1].t, 2.1);
  assert.equal(m.moveKeyframe(track(), 0, -4).kfs[0].t, 2);
  assert.equal(m.moveKeyframe(track(), 3, 99).kfs[3].t, 18);
  const same = track();
  assert.equal(m.moveKeyframe(same, 9, 3), same);
});

test('deleteKeyframe removes one; the last takes the track with it', () => {
  assert.deepEqual(
    m.deleteKeyframe(track(), 1).kfs.map((k) => k.t),
    [2, 12, 18],
  );
  assert.equal(m.deleteKeyframe({ ...track(), kfs: [{ t: 3, box: box(0) }] }, 0), null);
});

test('setStart and setStop keep half a second between them, and the clip bounds', () => {
  assert.equal(m.setStart(track(), 4).start, 4);
  assert.equal(m.setStart(track(), 30).start, 17.5);
  assert.equal(m.setStart(track(), -3).start, 0);
  assert.equal(m.setStop(track(), 16, 20).stop, 16);
  assert.equal(m.setStop(track(), 0, 20).stop, 2.5);
  assert.equal(m.setStop(track(), 99, 20).stop, 20);
  assert.equal(m.setStop(track(), 99, null).stop, 99);
});

test('dragBox moves inside the frame and resizes to at least the minimum', () => {
  const start = [100, 100, 300, 400];
  assert.deepEqual(m.dragBox(start, 'move', 50, 20), [120, 150, 320, 450]);
  assert.deepEqual(m.dragBox(start, 'move', -500, -500), [0, 0, 200, 300]);
  assert.deepEqual(m.dragBox(start, 'move', 900, 900), [800, 700, 1000, 1000]);
  assert.deepEqual(m.dragBox(start, 'se', 30, 10), [100, 100, 310, 430]);
  assert.deepEqual(m.dragBox(start, 'nw', 30, 10), [110, 130, 300, 400]);
  assert.deepEqual(m.dragBox(start, 'n', 0, 500), [280, 100, 300, 400]);
  assert.deepEqual(m.dragBox(start, 'e', -999, 0), [100, 100, 300, 120]);
  assert.deepEqual(m.dragBox(start, 's', 0, 9999), [100, 100, 1000, 400]);
});

test('boxBetween orders the corners and stays in the frame; isUsableBox wants 20 each way', () => {
  assert.deepEqual(m.boxBetween([500, 600], [300, 200]), [200, 300, 600, 500]);
  assert.deepEqual(m.boxBetween([-5, 0], [1200, 20]), [0, 0, 20, 1000]);
  assert.equal(m.isUsableBox([0, 0, 19, 100]), false);
  assert.equal(m.isUsableBox([0, 0, 20, 20]), true);
});

const line = (from, to, rate = 5, start = 0, drift = () => 0) => {
  const out = [];
  for (let i = 0; i <= (to - from) * rate; i++) {
    const t = +(from + i / rate).toFixed(3);
    out.push({ t, box: [100, 100 + (t - start) * 20 + drift(t), 300, 300 + (t - start) * 20 + drift(t)] });
  }
  return out;
};

test('trackFromSamples thins a straight walk to its two ends', () => {
  const loaded = m.trackFromSamples(line(1, 11));
  assert.equal(loaded.start, 1);
  assert.equal(loaded.stop, 11);
  assert.deepEqual(
    loaded.kfs.map((k) => k.t),
    [1, 11],
  );
});

test('trackFromSamples keeps a corner and reads a long jump as continuous', () => {
  const bend = line(0, 10).map((s) =>
    s.t > 5 ? { ...s, box: [s.box[0] + (s.t - 5) * 60, s.box[1], s.box[2] + (s.t - 5) * 60, s.box[3]] } : s,
  );
  const loaded = m.trackFromSamples(bend);
  assert.deepEqual(
    loaded.kfs.map((k) => k.t),
    [0, 5, 10],
  );
  const split = m.trackFromSamples([...line(0, 3), ...line(5, 8)]);
  assert.deepEqual([split.start, split.stop], [0, 8]);
  assert.equal(m.trackFromSamples([]), null);
  assert.deepEqual(
    m.trackFromSamples([{ t: 4, box: box(0) }]).kfs.map((k) => k.t),
    [4],
  );
});

test('a thinned track stays within the tolerance of the samples it came from', () => {
  const noisy = line(0, 20, 5, 0, (t) => Math.sin(t) * 40);
  const loaded = m.trackFromSamples(noisy);
  assert.ok(loaded.kfs.length < noisy.length / 2);
  for (const s of noisy) {
    const at = m.boxAt(loaded, null, s.t).box;
    for (let i = 0; i < 4; i++) assert.ok(Math.abs(at[i] - s.box[i]) <= m.THIN_TOLERANCE + 1, `t=${s.t}`);
  }
});

test('densify writes four a second with the start and stop exactly, and reads back in the viewer', () => {
  const samples = m.densify(track({ start: 2, stop: 4 }));
  assert.deepEqual(
    samples.map((s) => s.t),
    [2, 2.25, 2.5, 2.75, 3, 3.25, 3.5, 3.75, 4],
  );
  assert.deepEqual(samples[0].box, box(0));
  const odd = m.densify(track({ start: 2.1, stop: 3 }));
  assert.equal(odd[0].t, 2.1);
  assert.equal(odd[odd.length - 1].t, 3);
  for (let i = 1; i < samples.length; i++) assert.ok(samples[i].t - samples[i - 1].t <= 1);
});

test('densify never writes past the cap and never leaves a gap', () => {
  const long = {
    start: 0,
    stop: 900,
    kfs: [
      { t: 0, box: box(0) },
      { t: 900, box: box(100) },
    ],
  };
  const samples = m.densify(long);
  assert.ok(samples.length <= m.MAX_SAMPLES);
  assert.equal(samples[0].t, 0);
  assert.equal(samples[samples.length - 1].t, 900);
  for (let i = 1; i < samples.length; i++) assert.ok(samples[i].t - samples[i - 1].t <= 1, 'no accidental gap');
});

test('timelineWindow shows the track with ten seconds either side, twenty at least, within the clip', () => {
  assert.deepEqual(m.timelineWindow(track(), 2, 20), { from: 0, to: 20 });
  assert.deepEqual(m.timelineWindow({ ...track(), start: 100, stop: 130 }, 100, 600), { from: 90, to: 140 });
  assert.deepEqual(m.timelineWindow({ ...track(), start: 100, stop: 103 }, 100, 600), { from: 90, to: 113 });
  assert.deepEqual(m.timelineWindow({ ...track(), start: 2, stop: 4 }, 2, 600), { from: 0, to: 22 });
  assert.deepEqual(m.timelineWindow({ ...track(), start: 595, stop: 599 }, 595, 600), { from: 576, to: 600 });
  assert.deepEqual(m.timelineWindow(null, 300, 600), { from: 290, to: 310 });
  assert.deepEqual(m.timelineWindow(track(), 2, 600, true), { from: 0, to: 600 });
  const wide = m.timelineWindow(track(), 150, 600);
  assert.ok(wide.from <= 150 && wide.to >= 150, 'the playhead stays in view');
});

test('rulerTicks label every other second in a short window and thin out in a long one', () => {
  const short = m.rulerTicks(0, 20);
  assert.equal(short.length, 21);
  assert.deepEqual(
    short
      .filter((x) => x.label)
      .map((x) => x.t)
      .slice(0, 3),
    [0, 2, 4],
  );
  const long = m.rulerTicks(0, 600);
  assert.ok(long.length < 30);
  assert.deepEqual(m.rulerTicks(90, 140)[0], { t: 90, label: true });
});

test('a load, an edit and a densify read back in the viewer across a former gap', () => {
  const loaded = m.trackFromSamples([...line(0, 3), ...line(5, 8)]);
  const samples = m.densify(m.moveKeyframe(loaded, 0, 0));
  assert.ok(trackBoxAt(samples, 4));
});
