/*
 * SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import dashjs from 'dashjs';

const MANIFEST_READY_TIMEOUT_MS = 30_000;
const MANIFEST_RETRY_DELAY_MS = 1_000;

export interface DashStreamConfig {
    endpoint: string;
    streamId: string;
    // Supplying a window selects replay: the recording between these times is
    // packaged instead of the live edge.  Live sessions leave both unset.
    startTime?: string;
    endTime?: string;
    videoElement: HTMLVideoElement;
    // The same overlay object the WebRTC path sends; the service parses it with
    // the same reader, so an enabled overlay decodes and draws while an empty
    // one keeps the cheaper bitstream passthrough.
    overlay?: Record<string, unknown>;
    // A video wall names the cameras to compose and the rate to compose them
    // at.  Described exactly as it is for WebRTC so one description serves
    // both protocols.
    composite?: Record<string, unknown>;
    framerate?: number;
    liveDelaySeconds?: number;
    initialBufferSeconds?: number;
    onFirstFrame?: () => void;
    onError?: (message: string) => void;
}

interface DashStartResponse {
    viewerId: string;
    manifestUrl: string;
    audioAvailable: boolean;
    state: string;
}

export class DashStream {
    private player: dashjs.MediaPlayerClass | null = null;
    private viewerId = '';
    private firstFrameReported = false;
    private videoElement: HTMLVideoElement | null = null;
    private firstFrameListener: (() => void) | null = null;
    private autoplayListener: (() => void) | null = null;
    private autoplayBufferLevelEvent: string | null = null;
    private autoplayAttempted = false;
    private pageHideListener: (() => void) | null = null;
    private strandTimer: ReturnType<typeof setInterval> | null = null;
    // Remembered so the session is released through the same API that created it.
    private replay = false;
    private config: DashStreamConfig | null = null;

    private async waitForManifest(manifestUrl: string): Promise<void> {
        const deadline = Date.now() + MANIFEST_READY_TIMEOUT_MS;
        let lastStatus = 0;
        while (Date.now() < deadline) {
            const response = await fetch(manifestUrl, { credentials: 'include' });
            lastStatus = response.status;
            if (response.status === 202 || response.status === 404) {
                await new Promise<void>(resolve => window.setTimeout(resolve, MANIFEST_RETRY_DELAY_MS));
                continue;
            }
            if (response.ok) {
                const manifest = await response.text();
                if (manifest.includes('<MPD')) {
                    return;
                }
                throw new Error('DASH manifest endpoint returned a non-MPD response');
            }
            throw new Error(`DASH manifest request failed (${response.status}): ${await response.text()}`);
        }
        throw new Error(`DASH manifest did not become ready within ${MANIFEST_READY_TIMEOUT_MS / 1000}s (last status: ${lastStatus})`);
    }

    private async attachPlayer(config: DashStreamConfig, result: DashStartResponse): Promise<void> {
        const manifestUrl = new URL(result.manifestUrl, config.endpoint).toString();
        await this.waitForManifest(manifestUrl);
        const player = dashjs.MediaPlayer().create();
        this.player = player;
        // The catalogue a fresh session has when the manifest is first served is
        // the preroll, so a delay larger than that asks for media from before
        // the session existed and the player waits instead of starting.  Keep
        // this at or under the server's preroll.
        //
        // Live trades buffer for a short wait before the first frame, because
        // that wait is the whole of what the viewer experiences as latency.  A
        // recording has no live edge to chase, so replay keeps the larger buffer
        // and spends latency nobody is measuring.
        // The floor under this is what the pipeline cannot go below: a segment
        // has to be written whole, the manifest has to advertise it, and the
        // viewer's round trip has to fetch it.  Target a delay under that floor
        // and the player never reaches it, so it runs permanently fast, drains
        // the buffer against a source producing at exactly real time, and stalls
        // once per segment.  This sits comfortably above the floor instead.
        const isReplay = Boolean(config.startTime);
        const liveDelay = config.liveDelaySeconds ?? (isReplay ? 8 : 5);
        // Keys follow the dash.js 5.x layout that package.json pins.  dash.js
        // silently rejects unknown keys with a console warning instead of
        // failing, so a key from the pre-5 flat layout would leave the default
        // in force and the tuning below would quietly do nothing.
        player.updateSettings({
            streaming: {
                delay: {
                    // How far behind the live edge playback sits.  This is the
                    // headroom that absorbs a late segment without stalling.
                    liveDelay,
                },
                buffer: {
                    // The buffer is a sawtooth: it drains for a segment
                    // duration and refills when the next segment lands.  Stutter
                    // happens when the trough reaches zero, which is most likely
                    // right after start-up while the connection is still ramping,
                    // so playback is held until a cushion has been fetched and
                    // the trough never starts near zero.
                    initialBufferLevel: config.initialBufferSeconds ?? 4,
                    bufferTimeDefault: 12,
                    bufferTimeAtTopQuality: 12,
                },
                // Without catch-up a player that stalls once stays permanently
                // behind: it resumes from where it stopped while the live edge
                // keeps moving, so two viewers of the same camera drift apart by
                // however long each of them stalled.  Nudging the playback rate
                // pulls a lagging player back to the target delay so every
                // viewer converges on the same live edge again.
                // Off deliberately.  Catch-up speeds playback up to close a gap
                // to the live edge, but the source produces at exactly real
                // time, so any rate above 1.0 consumes faster than the stream is
                // made and drains the buffer to nothing.  It then stalls on
                // every segment: play one second, wait for the next, repeat.
                // Capping the rate only decides how long the cushion lasts
                // before that starts.  Without it the player holds the delay it
                // started with and plays at source rate, which is what a live
                // stream with a shallow buffer needs.
                // Catch-up has to be on, but barely.  Off entirely, a player that
                // starts behind - which it does on a fresh session, because it
                // begins at the start of a two second window and the live edge
                // runs away while it buffers - can never converge, and once its
                // playhead falls out of the back of the DVR window the media
                // under it is evicted and playback freezes with a minute of
                // unusable data buffered ahead.  On aggressively, it plays above
                // real time against a real time source and drains the buffer to
                // nothing, stalling once per segment.  A large drift threshold
                // keeps it idle through ordinary jitter, and a small rate cap
                // means the correction it does apply is imperceptible; beyond
                // the threshold dash.js seeks to the edge rather than crawling
                // back, which is what rescues a stranded playhead.
                liveCatchup: {
                    enabled: true,
                    maxDrift: 10,
                    playbackRate: { min: -0.02, max: 0.02 },
                },
                // A starved live player abandons the position it was playing
                // and resumes fetching at the live edge, which leaves a hole
                // between the two.  dash.js will only step over such a hole if
                // gap jumping is enabled explicitly, and the holes this
                // produces are far wider than the small-gap default allows.
                gaps: {
                    jumpGaps: true,
                    jumpLargeGaps: true,
                    smallGapLimit: 1.5,
                    threshold: 0.3,
                    enableSeekFix: true,
                },
                // Which clock decides whether a segment has been published.
                // Left alone dash.js reaches for a public time service, and
                // where that is unreachable it silently uses the device clock.
                utcSynchronization: {
                    enabled: true,
                    useManifestDateHeaderTimeSource: true,
                },
                // The manifest is served 202/Accepted until the packager has
                // prerolled, so the first fetches have to be retried patiently.
                retryAttempts: {
                    MPD: 30,
                },
                retryIntervals: {
                    MPD: 1000,
                },
            },
        });
        // A stall on the viewer's network is invisible from the server: the
        // request log shows the fetch being abandoned but not why the player
        // gave up on it.  Report the player's own account of each interruption,
        // stamped so it lines up with the access log.
        const trace = (what: string, detail: unknown) => {
            // eslint-disable-next-line no-console
            // console.warn makes the browser capture an async stack for every
            // call, which with devtools open costs enough main thread time to
            // cause the stalls this is here to record.  Records also go to an
            // array the page can dump with copy(window.__dashTrace.join('\n')).
            const v = config.videoElement;
            let health = '';
            try {
                const q = v.getVideoPlaybackQuality
                    ? v.getVideoPlaybackQuality()
                    : { droppedVideoFrames: 0, totalVideoFrames: 0 };
                let ahead = 0;
                // Where the playhead sits relative to every buffered range, not
                // just the one it is in.  A stranded playhead reads as
                // buffered_ahead=0 whether the media it needs was never
                // appended or was appended somewhere it cannot reach, and those
                // are different faults with different fixes.  Record the ranges
                // themselves so the two can be told apart after the event.
                const spans: string[] = [];
                let nextStart = -1;
                for (let i = 0; i < v.buffered.length; i += 1) {
                    const from = v.buffered.start(i);
                    const to = v.buffered.end(i);
                    spans.push(`${from.toFixed(1)}-${to.toFixed(1)}`);
                    if (v.currentTime >= from - 0.1 && v.currentTime <= to + 0.1) {
                        ahead = to - v.currentTime;
                    }
                    if (from > v.currentTime && (nextStart < 0 || from < nextStart)) {
                        nextStart = from;
                    }
                }
                const asked = player as unknown as {
                    getCurrentLiveLatency?: () => number;
                    getTargetLiveDelay?: () => number;
                    getDashMetrics?: () => { getCurrentDVRInfo?: (t: string) => {
                        range?: { start?: number; end?: number } } | null };
                };
                const latency = asked.getCurrentLiveLatency ? asked.getCurrentLiveLatency() : -1;
                const target = asked.getTargetLiveDelay ? asked.getTargetLiveDelay() : -1;
                const info = asked.getDashMetrics?.()?.getCurrentDVRInfo?.('video');
                const winEnd = Number(info?.range?.end ?? 0);
                const winStart = Number(info?.range?.start ?? 0);
                health = ` dropped=${q.droppedVideoFrames}/${q.totalVideoFrames}`
                    + ` buffered_ahead=${ahead.toFixed(2)}s readyState=${v.readyState}`
                    + ` ct=${v.currentTime.toFixed(2)}`
                    + ` latency=${Number(latency).toFixed(2)}s target=${Number(target).toFixed(2)}s`
                    + ` window=[${winStart.toFixed(1)}..${winEnd.toFixed(1)}]`
                    + ` ahead_of_playhead=${(winEnd - v.currentTime).toFixed(2)}s`
                    + ` paused=${v.paused} seeking=${v.seeking} rate=${v.playbackRate}`
                    + ` ranges=[${spans.join(',')}]`
                    + ` next_range_start=${nextStart.toFixed(2)}`
                    + ` strand=${(nextStart > 0 ? nextStart - v.currentTime : 0).toFixed(2)}s`;
            } catch {
                health = ' health=unavailable';
            }
            // A fragment event carries the whole parsed manifest hanging off its
            // representation, so serialising it wholesale writes the entire MPD
            // to the console once per segment.  That is a couple of kilobytes a
            // second: it bloats the log the viewer has to send on, and enough of
            // it will wedge the page itself.  Keep the fields that say where the
            // segment belongs and how long it took to arrive, and drop the rest.
            const summarise = (value: unknown): string => {
                if (!value || typeof value !== 'object') {
                    return '';
                }
                const request = (value as { request?: Record<string, unknown> }).request;
                if (!request) {
                    return JSON.stringify(value);
                }
                const url = String(request.url ?? '');
                return JSON.stringify({
                    file: url.slice(url.lastIndexOf('/') + 1),
                    index: request.index,
                    presentationStartTime: request.presentationStartTime,
                    mediaStartTime: request.mediaStartTime,
                    duration: request.duration,
                    bytesLoaded: request.bytesLoaded,
                    firstByteDate: request.firstByteDate,
                    endDate: request.endDate,
                });
            };
            const line = `[dash] ${new Date().toISOString()} ${what}${health} ${summarise(detail)}`;
            const w = window as unknown as { __dashTrace?: string[] };
            if (!w.__dashTrace) {
                w.__dashTrace = [];
            }
            w.__dashTrace.push(line);
            if (w.__dashTrace.length > 3000) {
                w.__dashTrace.shift();
            }
            // eslint-disable-next-line no-console
            console.log(line);
        };
        // Segments arriving while nothing plays is a question about the bytes,
        // not the timing, and a rejected append is invisible in the events above.
        // The element's own error and the player's error channel are where a
        // decode or append failure surfaces.
        config.videoElement.addEventListener('error', () => {
            const err = config.videoElement.error;
            trace('ELEMENT_ERROR', err
                ? { code: err.code, message: err.message }
                : { code: 'unknown' });
        });

        const ev = dashjs.MediaPlayer.events as unknown as Record<string, string>;
        (['PLAYBACK_STALLED', 'PLAYBACK_WAITING', 'BUFFER_EMPTY', 'BUFFER_LOADED',
          'PLAYBACK_SEEKING', 'FRAGMENT_LOADING_ABANDONED', 'PLAYBACK_RATE_CHANGED',
          'PLAYBACK_ERROR', 'ERROR', 'BUFFER_LEVEL_STATE_CHANGED',
          'FRAGMENT_LOADING_COMPLETED', 'QUALITY_CHANGE_RENDERED'] as const)
            .forEach(name => {
                const id = ev[name];
                if (id) {
                    player.on(id, (e: unknown) => trace(name, e));
                }
            });
        player.on(dashjs.MediaPlayer.events.ERROR, (event: { error?: { message?: string }; event?: { message?: string } }) => {
            const message = event.error?.message ?? event.event?.message ?? 'DASH playback error';
            config.onError?.(message);
        });
        this.videoElement = config.videoElement;
        this.startStrandWatchdog(config.videoElement, trace);
        // The current DASH pipeline does not package audio.  Chrome blocks an
        // asynchronous unmuted autoplay after the MPD preroll (and again after
        // a replay seek), leaving a decoded first frame visible with the media
        // element paused forever.  Mark audio-less sessions muted before
        // dash.js attaches its MediaSource so autoplay is permitted.  Keep
        // future audio-bearing sessions unmodified.
        if (!result.audioAvailable) {
            config.videoElement.muted = true;
            config.videoElement.defaultMuted = true;
            // dash.js can append the first MediaSource buffer after the
            // initiating user gesture has expired. Chrome may then leave this
            // muted, fully buffered video paused despite autoplay=true.
            //
            // Do not resume on the very first appended second, though.  That
            // bypasses dash.js' initial-buffer policy and makes a live overlay
            // run at the segment boundary with no jitter cushion.  Wait until
            // the requested initial buffer exists, then use muted play() to
            // retain reliable Chrome autoplay.
            const autoplayBufferSeconds = config.initialBufferSeconds ?? (isReplay ? 1 : 4);
            this.autoplayListener = () => {
                // `progress` and BUFFER_LEVEL_UPDATED fire for every append.
                // Calling play() on each one creates an unbounded retry loop if
                // Chrome rejects a video-only background playback request. The
                // repeated requests then interrupt the decoder and turn a
                // transient autoplay rejection into a permanent frozen frame.
                // A stream attachment gets exactly one automatic attempt; the
                // regular player controls remain available for a user retry.
                if (this.autoplayAttempted) {
                    return;
                }
                const buffered = config.videoElement.buffered;
                const bufferedAhead = buffered.length > 0
                    ? buffered.end(buffered.length - 1) - config.videoElement.currentTime
                    : 0;
                if (bufferedAhead < autoplayBufferSeconds) {
                    return;
                }
                this.autoplayAttempted = true;
                this.removeAutoplayListener();
                void config.videoElement.play().catch(error => {
                    // eslint-disable-next-line no-console
                    console.warn('[dash] muted autoplay was rejected; use the player controls to resume', error);
                });
            };
            config.videoElement.addEventListener('loadeddata', this.autoplayListener);
            config.videoElement.addEventListener('canplay', this.autoplayListener);
            config.videoElement.addEventListener('progress', this.autoplayListener);
            const bufferLevelUpdated = ev.BUFFER_LEVEL_UPDATED;
            if (bufferLevelUpdated) {
                this.autoplayBufferLevelEvent = bufferLevelUpdated;
                player.on(bufferLevelUpdated, this.autoplayListener);
            }
        }
        config.videoElement.playsInline = true;
        this.firstFrameListener = () => {
            if (!this.firstFrameReported) {
                this.firstFrameReported = true;
                config.onFirstFrame?.();
            }
        };
        // `loadeddata` only means that a single frame was decoded.  Reporting
        // it as the first frame hides the UI loader while the image is still
        // frozen waiting for the rest of the live cushion.  `playing` is the
        // moment the user can actually see continuous playback.
        config.videoElement.addEventListener('playing', this.firstFrameListener, { once: true });
        // Manual muted autoplay above deliberately waits for the initial
        // buffer; passing true would make dash.js play after its first append.
        player.initialize(config.videoElement, manifestUrl, false);
    }

    // Gap jumping is dash.js' own recovery and it handles the ordinary case.
    // It does not always fire here: a stranded playhead sits at readyState 1
    // with playback never advancing, so the timeupdate-driven checks that
    // would notice the hole never run, and the player waits on data that has
    // already been appended somewhere it will not look.  Observed holding a
    // playhead still for fourteen minutes with seventy eight seconds of
    // contiguous media buffered beyond the hole.  Watch for exactly that
    // shape - not playing, nothing under the playhead, a range waiting ahead -
    // and move the playhead onto the media that is already there.
    private startStrandWatchdog(video: HTMLVideoElement,
                                trace: (what: string, detail: unknown) => void): void {
        this.stopStrandWatchdog();
        const STALL_TICKS = 4;      // ~2s at the 500ms period below
        let lastTime = -1;
        let stalledTicks = 0;
        this.strandTimer = setInterval(() => {
            // Deliberately not skipping while the element reports seeking.
            // The failure being recovered here IS a seek that never completes:
            // the player seeks to the end of the range it has, which is inside
            // the hole, and the element waits there for data that will never
            // come while reporting seeking for as long as it waits - measured
            // still true after two minutes.  Skipping on seeking means never
            // recovering from precisely the case this exists for.  During a
            // pending seek currentTime already reads the target, so the
            // not-advancing test below still behaves, and an ordinary seek
            // resolves long before the stall threshold.
            if (video.paused || video.ended) {
                stalledTicks = 0;
                lastTime = video.currentTime;
                return;
            }
            if (Math.abs(video.currentTime - lastTime) > 0.01) {
                stalledTicks = 0;
                lastTime = video.currentTime;
                return;
            }
            // Playing, yet the playhead has not moved since the last check.
            stalledTicks += 1;
            if (stalledTicks < STALL_TICKS) {
                return;
            }
            // How far the media under the playhead actually runs, and where the
            // next island of media begins.  A stranded playhead usually has
            // nothing beneath it, but it can also come to rest a fraction of a
            // second short of the end of its range - measured at seventy
            // milliseconds - and that fraction is not playable either.  Judging
            // "covered" by mere containment therefore reads a permanent strand
            // as a throughput problem and declines to act.  Use the distance to
            // the end of the run instead: the playhead has already failed to
            // advance for the stall threshold, so whatever remains beneath it
            // is not going to move playback.
            let runEnd = -1;
            let nextStart = -1;
            for (let i = 0; i < video.buffered.length; i += 1) {
                const from = video.buffered.start(i);
                const to = video.buffered.end(i);
                if (video.currentTime >= from && video.currentTime <= to) {
                    runEnd = to;
                }
            }
            const boundary = runEnd >= 0 ? runEnd : video.currentTime;
            for (let i = 0; i < video.buffered.length; i += 1) {
                const from = video.buffered.start(i);
                if (from > boundary && (nextStart < 0 || from < nextStart)) {
                    nextStart = from;
                }
            }
            // Still a comfortable amount of media under the playhead means the
            // stall is not a stranding; moving would discard buffer the player
            // is entitled to use.
            if (nextStart < 0 || (runEnd >= 0 && runEnd - video.currentTime > 0.5)) {
                return;
            }
            const target = nextStart + 0.05;
            trace('STRAND_RECOVERED', {
                from: Number(video.currentTime.toFixed(3)),
                to: Number(target.toFixed(3)),
                holeSeconds: Number((nextStart - video.currentTime).toFixed(3)),
            });
            stalledTicks = 0;
            lastTime = target;
            video.currentTime = target;
        }, 500);
    }

    private stopStrandWatchdog(): void {
        if (this.strandTimer !== null) {
            clearInterval(this.strandTimer);
            this.strandTimer = null;
        }
    }

    private releasePlayer(): void {
        this.stopStrandWatchdog();
        // Unsubscribe before resetting the dash.js instance so the listener is
        // not retained by a replacement replay player.
        this.removeAutoplayListener();
        if (this.player) {
            this.player.reset();
            this.player = null;
        }
        if (this.videoElement && this.firstFrameListener) {
            this.videoElement.removeEventListener('playing', this.firstFrameListener);
        }
        this.videoElement = null;
        this.firstFrameListener = null;
        this.firstFrameReported = false;
        this.autoplayAttempted = false;
    }

    private removeAutoplayListener(): void {
        if (this.videoElement && this.autoplayListener) {
            this.videoElement.removeEventListener('loadeddata', this.autoplayListener);
            this.videoElement.removeEventListener('canplay', this.autoplayListener);
            this.videoElement.removeEventListener('progress', this.autoplayListener);
        }
        if (this.player && this.autoplayListener && this.autoplayBufferLevelEvent) {
            this.player.off(this.autoplayBufferLevelEvent, this.autoplayListener);
        }
        this.autoplayListener = null;
        this.autoplayBufferLevelEvent = null;
    }

    private removePageHideListener(): void {
        if (this.pageHideListener) {
            window.removeEventListener('pagehide', this.pageHideListener);
            this.pageHideListener = null;
        }
    }

    public async start(config: DashStreamConfig): Promise<DashStartResponse> {
        await this.stop(config.endpoint, config.streamId);
        this.replay = Boolean(config.startTime);
        const startPath = this.replay ? '/vst/api/v1/replay/dash/start' : '/vst/api/v1/live/dash/start';
        const startUrl = new URL(startPath, config.endpoint).toString();
        const requestBody: Record<string, unknown> = { streamId: config.streamId };
        if (this.replay) {
            requestBody.startTime = config.startTime as string;
            if (config.endTime) {
                requestBody.endTime = config.endTime;
            }
        }
        if (config.overlay) {
            requestBody.overlay = config.overlay;
        }
        if (config.composite) {
            requestBody.composite = config.composite;
            if (config.framerate) {
                requestBody.framerate = config.framerate;
            }
        }
        const response = await fetch(startUrl, {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                streamid: config.streamId,
            },
            body: JSON.stringify(requestBody),
        });
        if (!response.ok) {
            throw new Error(`DASH start failed (${response.status}): ${await response.text()}`);
        }
        const body = (await response.json()) as DashStartResponse | { data: DashStartResponse };
        const result = 'data' in body ? body.data : body;
        this.viewerId = result.viewerId;
        this.config = { ...config };
        // React cleanup covers deliberate UI restarts, but it is not a reliable
        // lifecycle hook for a browser/tab close.  Release the DASH viewer
        // lease during pagehide as well; DashStream.stop uses a keepalive
        // request, so this remains deliverable while the page is being torn
        // down.  Without this, a future start can legitimately reuse the
        // abandoned shared pass-through session until its idle timeout.
        this.removePageHideListener();
        this.pageHideListener = () => {
            void this.stop(config.endpoint, config.streamId);
        };
        window.addEventListener('pagehide', this.pageHideListener, { once: true });
        await this.attachPlayer(this.config, result);
        return result;
    }

    public async seekReplay(startTime: string): Promise<DashStartResponse> {
        if (!this.replay || !this.viewerId || !this.config) {
            throw new Error('DASH replay is not ready to seek');
        }
        const response = await fetch(new URL('/vst/api/v1/replay/dash/seek', this.config.endpoint).toString(), {
            method: 'POST',
            credentials: 'include',
            headers: {
                'Content-Type': 'application/json',
                streamid: this.config.streamId,
            },
            body: JSON.stringify({ viewerId: this.viewerId, startTime }),
        });
        if (!response.ok) {
            throw new Error(`DASH seek failed (${response.status}): ${await response.text()}`);
        }
        const body = (await response.json()) as DashStartResponse | { data: DashStartResponse };
        const result = 'data' in body ? body.data : body;

        // The server has destroyed the old packager before publishing this new
        // token.  Reset dash.js before it can request stale fragments, then
        // attach it only to the replacement manifest.
        this.releasePlayer();
        this.viewerId = result.viewerId;
        this.config = { ...this.config, startTime };
        await this.attachPlayer(this.config, result);
        return result;
    }

    public async stop(endpoint?: string, streamId?: string): Promise<void> {
        this.removePageHideListener();
        this.releasePlayer();
        if (!endpoint || !this.viewerId) {
            return;
        }
        const viewerId = this.viewerId;
        this.viewerId = '';
        try {
            const stopPath = this.replay ? '/vst/api/v1/replay/dash/stop' : '/vst/api/v1/live/dash/stop';
            await fetch(new URL(stopPath, endpoint).toString(), {
                method: 'POST',
                credentials: 'include',
                keepalive: true,
                headers: {
                    'Content-Type': 'application/json',
                    ...(streamId ? { streamid: streamId } : {}),
                },
                body: JSON.stringify({ viewerId }),
            });
        } catch {
            // The server's idle reaper releases leases after abrupt navigation or network loss.
        }
    }
}
