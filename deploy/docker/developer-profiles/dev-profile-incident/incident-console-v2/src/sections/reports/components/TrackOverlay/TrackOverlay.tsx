// SPDX-License-Identifier: Apache-2.0
'use client';

import { type RefObject, useEffect, useRef, useState } from 'react';

import { cn } from '@/lib/utils';
import { containedBoxStyle, parseTrackSamples, type TrackSample, trackBoxAt } from '@/modules/reports/domain/track';

/** One tracked item the overlay draws: its name (shown as the box label, never its id), colour and the console route its samples are read from. */
export interface TrackedItem {
  id: string;
  label: string;
  colour: string;
  trackUrl: string;
}

// Each track is read once per page load; a failed or malformed one is cached as empty, so it draws nothing and is not retried.
const loaded = new Map<string, Promise<TrackSample[]>>();
function loadTrack(url: string): Promise<TrackSample[]> {
  let pending = loaded.get(url);
  if (!pending) {
    pending = fetch(url)
      .then((response) => (response.ok ? response.json() : null))
      .then((body: { samples?: unknown } | null) => parseTrackSamples(body?.samples) ?? [])
      .catch(() => []);
    loaded.set(url, pending);
  }
  return pending;
}

/** The video's current second, read every frame while it plays (timeupdate alone moves a box only about four times a second). */
function usePlayhead(video: RefObject<HTMLVideoElement | null>): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const element = video.current;
    if (!element) return;
    let frame = 0;
    const read = () => setSeconds(element.currentTime);
    const tick = () => {
      read();
      frame = requestAnimationFrame(tick);
    };
    const play = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(tick);
    };
    const stop = () => {
      cancelAnimationFrame(frame);
      read();
    };
    read();
    if (!element.paused) play();
    element.addEventListener('play', play);
    element.addEventListener('pause', stop);
    element.addEventListener('ended', stop);
    element.addEventListener('seeked', read);
    element.addEventListener('timeupdate', read);
    return () => {
      cancelAnimationFrame(frame);
      element.removeEventListener('play', play);
      element.removeEventListener('pause', stop);
      element.removeEventListener('ended', stop);
      element.removeEventListener('seeked', read);
      element.removeEventListener('timeupdate', read);
    };
  }, [video]);
  return seconds;
}

/**
 * A box over the report player that follows each tracked person: the box at the playhead (interpolated between samples,
 * hidden outside the track's span), one colour per person with its name, the selected one emphasised. It never takes a
 * pointer event, so the player's own controls work through it.
 */
export function TrackOverlay({
  video,
  items,
  frameAspect,
  selectedId,
}: {
  video: RefObject<HTMLVideoElement | null>;
  items: TrackedItem[];
  /** The video's own width / height. */
  frameAspect: number;
  selectedId: string | null;
}) {
  const seconds = usePlayhead(video);
  const tileRef = useRef<HTMLDivElement>(null);
  const [tileAspect, setTileAspect] = useState(frameAspect);
  const [tracks, setTracks] = useState<Record<string, TrackSample[]>>({});

  useEffect(() => {
    let live = true;
    for (const item of items) {
      void loadTrack(item.trackUrl).then((samples) => {
        if (live) setTracks((current) => (current[item.trackUrl] === samples ? current : { ...current, [item.trackUrl]: samples }));
      });
    }
    return () => {
      live = false;
    };
  }, [items]);

  useEffect(() => {
    const tile = tileRef.current;
    if (!tile) return;
    const measure = () => {
      if (tile.clientWidth > 0 && tile.clientHeight > 0) setTileAspect(tile.clientWidth / tile.clientHeight);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(tile);
    return () => observer.disconnect();
  }, []);

  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden" data-testid="track-overlay" ref={tileRef}>
      {items.map((item) => {
        const samples = tracks[item.trackUrl];
        const style = samples ? containedBoxStyle(trackBoxAt(samples, seconds), frameAspect, tileAspect) : null;
        if (!style) return null;
        const selected = item.id === selectedId;
        const dimmed = selectedId !== null && !selected;
        return (
          <div
            className={cn(
              'absolute rounded-sm border-2 border-solid transition-opacity',
              selected && 'border-[3px]',
              dimmed && 'opacity-50',
            )}
            data-selected={selected ? 'true' : undefined}
            data-testid="track-box"
            data-track-item={item.id}
            key={item.id}
            style={{ ...style, borderColor: item.colour, boxShadow: selected ? `0 0 0 2px rgb(0 0 0 / 0.45)` : undefined }}
          >
            <span
              className={cn(
                'absolute -top-[1.35rem] -left-[2px] max-w-56 truncate rounded-sm px-1 py-px font-mono text-[10.5px] font-bold text-white',
                selected && 'text-[11.5px]',
              )}
              style={{ backgroundColor: item.colour }}
            >
              {item.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
