// SPDX-License-Identifier: Apache-2.0

'use client';

import { useEffect, useRef, useState } from 'react';

import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Slider } from '@/components/ui/slider';
import { Textarea } from '@/components/ui/textarea';
import { type BoxToolClient, boxToolClient } from '@/lib/evidence/box-tool-client';
import { endTrackAt, extendTrack, extensionRequest, SLOW_NOTICE_MS, TRACK_WINDOW_SECONDS } from '@/lib/evidence/box-tool';
import { videoFrameUrl } from '@/lib/evidence/evidence-view';
import { pickSeconds } from '@/lib/evidence/frame-picker';
import { scrubberBoxAt, trackSecond, trackSpan } from '@/lib/evidence/track';
import type { Box, BoxProposal, ReviewerDraftItem as BoxToolItem, ReviewerTrack } from '@/modules/reports/domain/reviewer-item';
import type { EvidenceItemType as BoxItemType } from '@/modules/reports/domain/revision';
import { formatClock } from '@/modules/reports/domain/normalize';
import { BoxCanvas } from './BoxCanvas';

const ITEM_TYPE_LABEL: Record<BoxItemType, string> = { entity: 'Entity', instrument: 'Instrument', asset: 'Asset' };

/** The text of an item the reviewer is re-tracking: shown as it is, never changed by the tool. */
export interface ExistingItem {
  itemType: BoxItemType;
  name: string;
  description: string;
  holder: string | null;
  threatLevel: number | null;
  /** The second the item is first seen at, where the box tool opens. */
  firstSeenSeconds: number;
}

interface Card {
  itemType: BoxItemType;
  name: string;
  description: string;
  holderKey: string | null;
  threatLevel: number | null;
}

type Stage = 'box' | 'working' | 'review';

/**
 * The box tool. Step 1: pick a second and draw a box, nothing to type. Step 2: "Analyse this box" asks the describe route
 * what the box holds, then the track route to follow it. Step 3: the AI-filled card beside the tracked preview, where the
 * reviewer fixes the text, scrubs, extends, adjusts the box or ends the track, then adds the item to the draft. With
 * `existing` it only re-tracks that item from a new box; its text is not touched.
 */
export function BoxTool({
  videoId,
  videoSrc,
  existing = null,
  holders,
  initialSeconds,
  client = boxToolClient,
  onClose,
  onDone,
}: {
  videoId: string;
  videoSrc: string;
  existing?: ExistingItem | null;
  /** The draft's entities by name, for an instrument's holder. */
  holders: Array<{ key: string; name: string }>;
  initialSeconds: number;
  client?: BoxToolClient;
  onClose: () => void;
  onDone: (item: BoxToolItem, holderKey: string | null) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [stage, setStage] = useState<Stage>('box');
  const [seconds, setSeconds] = useState(() => pickSeconds(initialSeconds, null));
  const [duration, setDuration] = useState<number | null>(null);
  const [seed, setSeed] = useState<{ seconds: number; box: Box } | null>(null);
  const [card, setCard] = useState<Card>(() => ({
    itemType: existing?.itemType ?? 'asset',
    name: existing?.name ?? '',
    description: existing?.description ?? '',
    holderKey: null,
    threatLevel: existing?.threatLevel ?? null,
  }));
  const [proposal, setProposal] = useState<BoxProposal | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // undefined: not tracked yet; null: kept as a still image (or the tracker gave nothing).
  const [track, setTrack] = useState<ReviewerTrack | null | undefined>(undefined);
  const [trackFailed, setTrackFailed] = useState(false);
  const [partialKept, setPartialKept] = useState(false);
  const [phase, setPhase] = useState<'describe' | 'track' | null>(null);
  const [busy, setBusy] = useState(false);
  const [waited, setWaited] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [previewSeconds, setPreviewSeconds] = useState(0);
  const [drawing, setDrawing] = useState(false);
  const [drawnBox, setDrawnBox] = useState<Box | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  // Count the seconds waited on the AI; after a few, say it is still working rather than leave the same line on screen.
  useEffect(() => {
    setWaited(0);
    if (stage !== 'working') return;
    const started = Date.now();
    const timer = setInterval(() => setWaited(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [stage]);
  const slow = waited * 1000 >= SLOW_NOTICE_MS;

  const run = async <T,>(task: (signal: AbortSignal) => Promise<T>): Promise<T | undefined> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      return await task(controller.signal);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'The request failed');
      return undefined;
    } finally {
      if (abortRef.current === controller) setBusy(false);
    }
  };

  const trackRequest = (at: { seconds: number; box: Box }, text: Pick<Card, 'itemType' | 'name' | 'description'>) => ({
    seconds: at.seconds,
    box: at.box,
    itemType: text.itemType,
    name: text.name.trim(),
    description: text.description.trim(),
    backSeconds: TRACK_WINDOW_SECONDS,
    forwardSeconds: TRACK_WINDOW_SECONDS,
  });

  /** Follows the box from `at` for a card, replacing any earlier track: the tracker result opens the review step. */
  const followBox = async (at: { seconds: number; box: Box }, text: Card) => {
    setPhase('track');
    const result = await run((signal) => client.track(videoId, trackRequest(at, text), signal));
    if (result === undefined) return false;
    setSeed(at);
    setTrackFailed(result === null);
    setTrack(result);
    setPartialKept(false);
    setPreviewSeconds(Math.round(result ? result.samples[0].t : at.seconds));
    return true;
  };

  const analyse = async () => {
    if (!seed) return;
    setStage('working');
    setProposal(null);
    setNotice(null);
    setTrack(undefined);
    let text = card;
    if (!existing) {
      setPhase('describe');
      const described = await run((signal) => client.describe(videoId, { seconds: seed.seconds, box: seed.box }, signal));
      if (!described) return setStage('box');
      setProposal(described.proposal);
      if (!described.proposal) {
        setNotice(described.reason ?? 'The AI could not tell what is in this box.');
        setPreviewSeconds(Math.round(seed.seconds));
        return setStage('review');
      }
      const p = described.proposal;
      const holder = p.holder ? holders.find((h) => h.name.toLowerCase() === p.holder?.toLowerCase()) : undefined;
      text = { itemType: p.itemType, name: p.name, description: p.description, holderKey: holder?.key ?? null, threatLevel: p.threatLevel };
      setCard(text);
    }
    if (await followBox(seed, text)) setStage('review');
    else setStage('box');
  };

  const cancelRun = () => {
    abortRef.current?.abort();
    setBusy(false);
    setStage(track === undefined && !proposal && !notice ? 'box' : 'review');
  };

  const extend = async (direction: 'back' | 'on') => {
    if (!track) return;
    const request = extensionRequest(track, direction);
    const result = await run((signal) => client.track(videoId, { ...trackRequest(request, card), ...request }, signal));
    if (result === undefined) return;
    if (result === null) return setError('The tracker found nothing further to add.');
    setTrack(extendTrack(track, result, direction));
    setPartialKept(false);
  };

  const retrackFromDrawn = async () => {
    if (!drawnBox) return;
    const ok = await followBox({ seconds: previewSeconds, box: drawnBox }, card);
    if (ok) {
      setDrawing(false);
      setDrawnBox(null);
    }
  };

  const keepStill = () => {
    if (seed) setPreviewSeconds(Math.round(seed.seconds));
    setTrack(null);
    setTrackFailed(false);
  };

  const goneFromHere = () => {
    if (!track) return;
    const ended = endTrackAt(track, previewSeconds);
    if (ended) {
      setTrack(ended);
      setPartialKept(false);
    }
  };

  const holderName = holders.find((h) => h.key === card.holderKey)?.name;
  const lost = !!track?.partial && !partialKept;
  const trackless = track === null && trackFailed;
  const decided = track === undefined ? false : track === null ? !trackFailed : !lost;
  const textReady = card.name.trim() !== '' && card.description.trim() !== '';
  const canAdd = !busy && decided && textReady && seed !== null;

  const add = () => {
    if (!canAdd || !seed) return;
    onDone(
      {
        itemType: card.itemType,
        name: card.name.trim(),
        description: card.description.trim(),
        ...(card.itemType === 'instrument' && holderName ? { holder: holderName } : {}),
        ...(card.itemType === 'instrument' && card.threatLevel !== null ? { threatLevel: card.threatLevel } : {}),
        aiProposal: proposal,
        track: track ?? null,
        ...(track ? {} : { stillSeconds: seed.seconds, stillBox: seed.box }),
      },
      card.itemType === 'instrument' ? card.holderKey : null,
    );
  };

  const span = track ? trackSpan(track.samples) : null;
  const previewBox: readonly number[] | null = drawing
    ? drawnBox
    : track
      ? previewSeconds >= track.span[0] - 0.5 && previewSeconds <= track.span[1] + 0.5
        ? scrubberBoxAt(track.samples, previewSeconds)
        : null
      : seed && Math.round(seed.seconds) === previewSeconds
        ? seed.box
        : null;
  const frameSeconds = span ? trackSecond(previewSeconds, span) : previewSeconds;

  return (
    <Dialog
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
      open
    >
      <DialogContent
        aria-label={existing ? `Edit ${existing.name}` : 'Add evidence'}
        className="w-full max-w-[calc(100%-2rem)] gap-4 rounded-2xl bg-white p-5 shadow-xl sm:max-w-5xl"
        role="dialog"
      >
        <DialogHeader className="flex flex-row items-center justify-between gap-3 space-y-0 pr-6">
          <DialogTitle className="text-sm font-bold text-ink">
            {existing ? `Edit ${existing.name || 'evidence'}` : 'Add evidence'}
            <span className="ml-2 font-mono text-[10.5px] font-bold uppercase tracking-wider text-ink-caption">
              {stage === 'box' ? '1 · Box it' : stage === 'working' ? '2 · Analysing' : '3 · Check and add'}
            </span>
          </DialogTitle>
          <Badge className="font-mono text-[11px] text-muted-foreground" variant="outline">
            {formatClock(stage === 'review' ? previewSeconds : seconds)}
          </Badge>
        </DialogHeader>
        <DialogDescription className="sr-only">
          Draw a box on the frame. The AI names it and follows it through the video; you check the result before it joins the draft.
        </DialogDescription>

        {error && (
          <Alert variant="destructive">
            <AlertDescription data-testid="box-tool-error">{error}</AlertDescription>
          </Alert>
        )}

        {stage === 'box' && (
          <>
            <BoxCanvas box={seed?.box ?? null} drawable onDraw={(box) => setSeed({ seconds, box })} testId="box-tool-frame">
              <video
                className="h-full w-full object-fill"
                muted
                onLoadedMetadata={(event) => {
                  const length = event.currentTarget.duration;
                  setDuration(Number.isFinite(length) && length > 0 ? length : null);
                  event.currentTarget.currentTime = seconds;
                }}
                playsInline
                preload="auto"
                ref={videoRef}
                src={videoSrc}
              />
            </BoxCanvas>
            <Slider
              aria-label="Frame second"
              disabled={duration === null}
              max={duration ?? 0}
              min={0}
              onValueChange={([val]) => {
                const next = pickSeconds(val, duration);
                setSeconds(next);
                if (videoRef.current) videoRef.current.currentTime = next;
                setSeed(null);
              }}
              step={0.1}
              value={[seconds]}
            />
            <p className="text-[11px] text-muted-foreground">
              {existing
                ? `Pick a second and draw the right box around ${existing.name}. Its text stays as it is; only the track changes.`
                : 'Pick a second and draw a box around what the model missed. No form: the AI names it for you.'}
            </p>
          </>
        )}

        {stage === 'working' && (
          <div className="space-y-3 py-6" data-testid="box-tool-progress">
            <p className="text-sm font-semibold text-ink">
              {phase === 'describe'
                ? slow
                  ? `The AI is still looking… ${waited} s`
                  : 'The AI is reading the box…'
                : `Following ${card.name || 'it'} through the video…`}
            </p>
            <Progress value={phase === 'describe' ? 30 : 70} />
            <p className="text-[11px] text-muted-foreground">
              {existing ? 'Tracking' : 'Reading the box, then tracking it'} {TRACK_WINDOW_SECONDS} s either side. This can take a few
              minutes if the model is slow.
            </p>
          </div>
        )}

        {stage === 'review' && (
          <div className="grid gap-4 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            <div className="min-w-0 space-y-2">
              <BoxCanvas
                box={previewBox}
                drawable={drawing && !busy}
                label={card.name || undefined}
                onDraw={setDrawnBox}
                testId="box-tool-preview"
              >
                {/* biome-ignore lint/performance/noImgElement: a JPEG the console's own frame route cuts; next/image would re-encode it */}
                <img
                  alt={`Frame at ${formatClock(frameSeconds)}`}
                  className="h-full w-full object-fill"
                  draggable={false}
                  src={videoFrameUrl(videoId, frameSeconds)}
                />
              </BoxCanvas>
              {span && track ? (
                <Slider
                  aria-label="Track second"
                  data-testid="box-tool-scrubber"
                  disabled={busy || drawing || span.first === span.last}
                  max={span.last}
                  min={span.first}
                  onValueChange={([val]) => setPreviewSeconds(trackSecond(val, span))}
                  step={1}
                  value={[frameSeconds]}
                />
              ) : null}
              <p className="text-[11px] text-muted-foreground" data-testid="box-tool-track-line">
                {track
                  ? `Your track · ${card.name || 'this item'} · ${formatClock(track.span[0])}–${formatClock(track.span[1])}`
                  : track === null && !trackFailed
                    ? 'Kept as a still image. The evidence image is this frame.'
                    : 'Not tracked yet.'}
                {track?.partial && <span className="ml-1 font-semibold text-clay">Incomplete track</span>}
              </p>
              {track && (
                <p className="text-[11px] text-muted-foreground">
                  First seen at <b className="font-mono">{formatClock(track.samples[0].t)}</b> · this frame is the evidence image
                </p>
              )}
              <div className="flex flex-wrap items-center gap-1.5">
                <Button
                  disabled={busy || !track || track.span[0] <= 0 || drawing}
                  onClick={() => extend('back')}
                  size="xs"
                  type="button"
                  variant="outline"
                >
                  Extend {TRACK_WINDOW_SECONDS} s back
                </Button>
                <Button disabled={busy || !track || drawing} onClick={() => extend('on')} size="xs" type="button" variant="outline">
                  Extend {TRACK_WINDOW_SECONDS} s on
                </Button>
                <Button
                  disabled={busy || !track || drawing || previewSeconds <= track.samples[0].t}
                  onClick={goneFromHere}
                  size="xs"
                  type="button"
                  variant="outline"
                >
                  Gone from here on
                </Button>
                {!drawing ? (
                  <Button
                    disabled={busy}
                    onClick={() => {
                      setDrawing(true);
                      setDrawnBox(null);
                    }}
                    size="xs"
                    type="button"
                    variant="outline"
                  >
                    Adjust box
                  </Button>
                ) : (
                  <>
                    <Button disabled={busy || !drawnBox || !textReady} onClick={retrackFromDrawn} size="xs" type="button">
                      {busy ? 'Tracking…' : `Re-track from ${formatClock(previewSeconds)}`}
                    </Button>
                    <Button
                      disabled={busy}
                      onClick={() => {
                        setDrawing(false);
                        setDrawnBox(null);
                      }}
                      size="xs"
                      type="button"
                      variant="ghost"
                    >
                      Cancel adjusting
                    </Button>
                  </>
                )}
              </div>
              {drawing && (
                <p className="text-[11px] text-muted-foreground">
                  {span ? 'Scrub is paused. ' : ''}Draw the right box on this frame; the tracker restarts from it and replaces the current
                  track.
                </p>
              )}
              {track === undefined && notice && !drawing && (
                <Button
                  disabled={busy || !textReady || !seed}
                  onClick={() => seed && followBox(seed, card)}
                  size="xs"
                  type="button"
                  data-testid="box-tool-track-now"
                >
                  {busy ? 'Tracking…' : 'Track from this box'}
                </Button>
              )}
            </div>

            <div className="min-w-0 space-y-2 text-xs" data-testid="box-tool-card">
              {notice && (
                <Alert data-testid="box-tool-ai-failed">
                  <AlertDescription>
                    <b>The AI could not identify this.</b> {notice} Fill it in yourself, then track it from your box.
                  </AlertDescription>
                </Alert>
              )}
              {proposal && !existing && (
                <p className="text-[11px] text-muted-foreground">
                  AI filled this in ({Math.round(proposal.confidence * 100)}% sure). Fix any word before adding.
                </p>
              )}
              {track?.partial && !partialKept && (
                <Alert data-testid="box-tool-lost">
                  <AlertDescription className="space-y-2">
                    <p>
                      <b>The tracker lost {card.name || 'it'}</b>
                      {track.lostAt !== null ? ` at ${formatClock(track.lostAt)}` : ''}.
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      <Button onClick={() => setPartialKept(true)} size="xs" type="button" variant="outline">
                        Keep as incomplete track
                      </Button>
                      <Button
                        onClick={() => {
                          setPreviewSeconds(Math.round(track.lostAt ?? track.span[1]));
                          setDrawing(true);
                          setDrawnBox(null);
                        }}
                        size="xs"
                        type="button"
                        variant="outline"
                      >
                        Redraw{track.lostAt !== null ? ` at ${formatClock(track.lostAt)}` : ''}
                      </Button>
                      <Button onClick={keepStill} size="xs" type="button" variant="outline">
                        Keep as still image
                      </Button>
                    </div>
                  </AlertDescription>
                </Alert>
              )}
              {trackless && (
                <Alert data-testid="box-tool-lost">
                  <AlertDescription className="space-y-2">
                    <p>
                      <b>The tracker found nothing to follow.</b>
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      <Button
                        onClick={() => {
                          setDrawing(true);
                          setDrawnBox(null);
                        }}
                        size="xs"
                        type="button"
                        variant="outline"
                      >
                        Redraw the box
                      </Button>
                      <Button onClick={keepStill} size="xs" type="button" variant="outline">
                        Keep as still image
                      </Button>
                    </div>
                  </AlertDescription>
                </Alert>
              )}

              {existing ? (
                <div className="space-y-1" data-testid="box-tool-existing-text">
                  <p className="text-sm font-semibold">{existing.name}</p>
                  <p className="text-ink/60">{existing.description}</p>
                  <p className="text-[11px] text-ink-caption">The text stays as it is; only the track changes.</p>
                </div>
              ) : (
                <>
                  <Select onValueChange={(val) => setCard({ ...card, itemType: val as BoxItemType })} value={card.itemType}>
                    <SelectTrigger aria-label="Item type" className="h-8 w-full bg-white text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent position="popper">
                      {(Object.keys(ITEM_TYPE_LABEL) as BoxItemType[]).map((type) => (
                        <SelectItem key={type} value={type}>
                          {ITEM_TYPE_LABEL[type]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Input
                    aria-label="Name"
                    className="h-8 text-xs"
                    onChange={(event) => setCard({ ...card, name: event.target.value })}
                    placeholder="Name"
                    value={card.name}
                  />
                  <Textarea
                    aria-label="Description"
                    className="min-h-[72px] text-xs"
                    onChange={(event) => setCard({ ...card, description: event.target.value })}
                    placeholder="Description"
                    value={card.description}
                  />
                  {card.itemType === 'instrument' && (
                    <div className="grid grid-cols-2 gap-1.5">
                      <Select
                        onValueChange={(val) => setCard({ ...card, threatLevel: val === '__none__' ? null : Number(val) })}
                        value={card.threatLevel ? String(card.threatLevel) : '__none__'}
                      >
                        <SelectTrigger aria-label="Threat level" className="h-8 w-full bg-white text-xs">
                          <SelectValue placeholder="Threat level…" />
                        </SelectTrigger>
                        <SelectContent position="popper">
                          <SelectItem value="__none__">Threat level…</SelectItem>
                          {[1, 2, 3, 4, 5].map((level) => (
                            <SelectItem key={level} value={String(level)}>
                              threat {level}/5
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Select
                        onValueChange={(val) => setCard({ ...card, holderKey: val === '__none__' ? null : val })}
                        value={card.holderKey ?? '__none__'}
                      >
                        <SelectTrigger aria-label="Held by" className="h-8 w-full bg-white text-xs">
                          <SelectValue placeholder="Holder not determined" />
                        </SelectTrigger>
                        <SelectContent position="popper">
                          <SelectItem value="__none__">Holder not determined</SelectItem>
                          {holders.map((holder) => (
                            <SelectItem key={holder.key} value={holder.key}>
                              held by {holder.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        <DialogFooter className="mt-2 flex flex-row items-center justify-end gap-2 text-sm">
          <Button
            className="rounded-full border border-ink/20 font-semibold"
            onClick={() => (stage === 'working' ? cancelRun() : busy ? undefined : onClose())}
            size="sm"
            type="button"
            variant="outline"
          >
            {stage === 'working' ? 'Stop' : 'Cancel'}
          </Button>
          {stage === 'box' && (
            <Button
              className="rounded-full bg-ink font-semibold text-white hover:bg-ink/90 disabled:opacity-40"
              disabled={!seed || busy}
              onClick={analyse}
              size="sm"
              type="button"
            >
              {existing ? 'Track this box' : 'Analyse this box'}
            </Button>
          )}
          {stage === 'review' && (
            <Button
              className="rounded-full bg-ink font-semibold text-white hover:bg-ink/90 disabled:opacity-40"
              disabled={!canAdd}
              onClick={add}
              size="sm"
              type="button"
            >
              {existing ? 'Update track in draft' : 'Add to draft'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
