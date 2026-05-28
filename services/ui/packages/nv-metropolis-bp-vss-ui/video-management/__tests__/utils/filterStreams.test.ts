// SPDX-License-Identifier: MIT
import { filterStreams } from '../../lib-src/utils';
import { makeStream } from '../helpers/streamFixtures';

describe('filterStreams', () => {
  // Filter matches by stream name only (not url, vodUrl, or streamId). Applied when user clicks Search.
  const videoStream = makeStream({ name: 'warehouse_safety', streamId: 'vid-1', url: 'https://a/v.mp4', vodUrl: 'https://a/vod/v.mp4' });
  const singleLetterStream = makeStream({ name: 't', streamId: 'vid-t', url: 'https://a/t.mp4', vodUrl: 'https://a/t.mp4' });
  const rtspStream = makeStream({
    name: 'Camera 1',
    streamId: 'rtsp-1',
    url: 'rtsp://host/stream',
    vodUrl: 'rtsp://host/stream',
  });

  it('returns all streams when search query is empty and both video and RTSP shown', () => {
    const result = filterStreams([videoStream, singleLetterStream, rtspStream], true, true, '');
    expect(result).toHaveLength(3);
  });

  it('filters by single character query (e.g. "t") - match by name only', () => {
    const streams = [videoStream, singleLetterStream, rtspStream];
    const result = filterStreams(streams, true, true, 't');
    // "t" matches only by name: "t" and "warehouse_safety" (name contains "t" in "safety"); "Camera 1" does not
    expect(result).toHaveLength(2);
    expect(result.map((s) => s.name)).toContain('t');
    expect(result.map((s) => s.name)).toContain('warehouse_safety');
  });

  it('finds stream whose name is exactly the single-word search term', () => {
    const streams = [videoStream, singleLetterStream];
    const result = filterStreams(streams, true, true, 't');
    const exactMatch = result.find((s) => s.name === 't');
    expect(exactMatch).toBeDefined();
    expect(exactMatch!.streamId).toBe('vid-t');
  });

  it('filters by single word query', () => {
    const result = filterStreams([videoStream, singleLetterStream], true, true, 'warehouse');
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('warehouse_safety');
  });

  it('search is case-insensitive', () => {
    const result = filterStreams([singleLetterStream], true, true, 'T');
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('t');
  });

  it('matches only by stream name, not by url or streamId', () => {
    const stream = makeStream({ name: 'foo', streamId: 'bar-id', url: 'https://a/baz', vodUrl: 'https://a/baz' });
    expect(filterStreams([stream], true, true, 'bar')).toHaveLength(0);
    expect(filterStreams([stream], true, true, 'baz')).toHaveLength(0);
    expect(filterStreams([stream], true, true, 'foo')).toHaveLength(1);
  });

  it('handles streams with undefined url/vodUrl safely (match by name still works)', () => {
    const streamWithPartialFields = {
      ...singleLetterStream,
      name: 't',
      url: undefined as unknown as string,
      vodUrl: undefined as unknown as string,
    };
    const result = filterStreams([streamWithPartialFields], true, true, 't');
    expect(result).toHaveLength(1);
    expect(result[0].name).toBe('t');
  });

  it('handles undefined name safely (stream does not match search)', () => {
    const streamWithUndefinedName = {
      ...singleLetterStream,
      name: undefined as unknown as string,
    };
    const result = filterStreams([streamWithUndefinedName], true, true, 't');
    expect(result).toHaveLength(0);
  });

  it('respects showVideos: false (filters out non-RTSP)', () => {
    const result = filterStreams([videoStream, singleLetterStream, rtspStream], false, true, '');
    expect(result).toHaveLength(1);
    expect(result[0].url).toMatch(/^rtsp:/);
  });

  it('respects showRtsps: false (filters out RTSP)', () => {
    const result = filterStreams([videoStream, singleLetterStream, rtspStream], true, false, '');
    expect(result).toHaveLength(2);
    expect(result.every((s) => !s.url.startsWith('rtsp://'))).toBe(true);
  });
});
