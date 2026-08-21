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
    // Remembered so the session is released through the same API that created it.
    private replay = false;

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

        const manifestUrl = new URL(result.manifestUrl, config.endpoint).toString();
        await this.waitForManifest(manifestUrl);
        const player = dashjs.MediaPlayer().create();
        this.player = player;
        const liveDelay = config.liveDelaySeconds ?? 8;
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
                liveCatchup: {
                    enabled: true,
                    maxDrift: 1,
                    playbackRate: { min: -0.05, max: 0.05 },
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
        player.on(dashjs.MediaPlayer.events.ERROR, (event: { error?: { message?: string }; event?: { message?: string } }) => {
            const message = event.error?.message ?? event.event?.message ?? 'DASH playback error';
            config.onError?.(message);
        });
        this.videoElement = config.videoElement;
        this.firstFrameListener = () => {
            if (!this.firstFrameReported) {
                this.firstFrameReported = true;
                config.onFirstFrame?.();
            }
        };
        config.videoElement.addEventListener('loadeddata', this.firstFrameListener, { once: true });
        player.initialize(config.videoElement, manifestUrl, true);
        return result;
    }

    public async stop(endpoint?: string, streamId?: string): Promise<void> {
        if (this.player) {
            this.player.reset();
            this.player = null;
        }
        if (this.videoElement && this.firstFrameListener) {
            this.videoElement.removeEventListener('loadeddata', this.firstFrameListener);
        }
        this.videoElement = null;
        this.firstFrameListener = null;
        this.firstFrameReported = false;
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
