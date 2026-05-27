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

import 'webrtc-adapter';
import StreamManager from '../StreamManager';
import getAPIPath, { StreamType } from '../utils/apis';
import { logger } from '../utils/logger';
import { RetryConfig } from '../utils/interfaces';
import InboundStream from './inbound/InboundStream';
import OutboundStream from './outbound/OutboundStream';

/** Class to handle both inbound and outbound WebRTC peer connections */
export default class NvWebRTC {
    private isCleanupInProgress: boolean = false;

    private inboundPeerId: string;
    private outboundPeerId: string;

    private inboundStream: InboundStream | null = null;
    private outboundStream: OutboundStream | null = null;

    private streamStatusInterval: NodeJS.Timeout | null = null;

    private streamManager: StreamManager;
    private streamType: StreamType;

    constructor(streamManager: StreamManager, inboundPeerId: string, outboundPeerId: string) {
        this.streamManager = streamManager;

        this.inboundPeerId = inboundPeerId;
        this.outboundPeerId = outboundPeerId;

        this.streamType = this.streamManager.streamType;
        this.initiateStreams();
    }

    private initiateStreams(): void {
        this.inboundStream = new InboundStream(this.streamManager, this.inboundPeerId);
        this.inboundStream.initiate();

        if (this.streamManager.getConfig().enableCamera || this.streamManager.getConfig().enableMicrophone) {
            this.outboundStream = new OutboundStream(this.streamManager, this.outboundPeerId);
            this.outboundStream.initiate();
        }

        logger.info('[WEBRTC] Stream setup initiated.');
        // Start periodic stream status queries
        this.startStreamStatusQueries();
    }

    private startStreamStatusQueries(): void {
        // Only start status queries for live and replay streams
        if (this.streamType !== StreamType.Live && this.streamType !== StreamType.Replay) {
            return;
        }

        const queryStatus = (): void => {
            // Query inbound stream status
            // Stream status is needed for all streams
            if (this.inboundPeerId) {
                // Need to query stream query for replay streams only when both startTime and endTime are present
                if (
                    this.streamType === StreamType.Replay &&
                    this.streamManager.streamConfig?.startTime &&
                    this.streamManager.streamConfig?.endTime
                ) {
                    const inboundStreamQueryPayload = {
                        apiKey: getAPIPath(this.streamType).streamQuery,
                        peerId: this.inboundPeerId,
                        data: { peerId: this.inboundPeerId },
                    };
                    this.streamManager.sendWebSocketMessage(JSON.stringify(inboundStreamQueryPayload));
                }
            }

            // Query outbound stream status
            if (this.outboundPeerId) {
                // TODO: Remove this once we have the correct usecase for outbound stream status and query
                return;
                const outboundStreamStatusPayload = {
                    apiKey: getAPIPath(this.streamType).streamStatus,
                    peerId: this.outboundPeerId,
                    data: { peerId: this.outboundPeerId },
                };
                this.streamManager.sendWebSocketMessage(JSON.stringify(outboundStreamStatusPayload));

                const outboundStreamQueryPayload = {
                    apiKey: getAPIPath(this.streamType).streamQuery,
                    peerId: this.outboundPeerId,
                    data: { peerId: this.outboundPeerId },
                };
                this.streamManager.sendWebSocketMessage(JSON.stringify(outboundStreamQueryPayload));
            }
        };

        // Start querying every 200ms
        this.streamStatusInterval = setInterval(queryStatus, 1000);
    }

    public handleWebSocketMessage(msg: string): void {
        this.inboundStream?.handleWebSocketMessage(msg);
        this.outboundStream?.handleWebSocketMessage(msg);
    }

    public getInboundPeerConnectionObject(): RTCPeerConnection | null {
        return this.inboundStream?.getPeerConnectionObject() || null;
    }

    // Toggle microphone. Returns boolean value true if microphone is enabled
    // and false if microphone is disabled. Returns undefined in case of error
    public toggleMicrophone = (): boolean | undefined => {
        if (this.outboundStream) {
            return this.outboundStream.toggleMicrophone();
        }
        return undefined;
    };

    public getOutboundPeerConnectionObject(): RTCPeerConnection | null {
        return this.outboundStream?.getPeerConnectionObject() || null;
    }

    public doCleanup(): void {
        // Prevent multiple cleanup calls
        if (this.isCleanupInProgress) {
            logger.debug('Cleanup already in progress, skipping');
            return;
        }

        this.isCleanupInProgress = true;

        try {
            // Clear all timeouts and intervals first
            if (this.streamStatusInterval) {
                clearInterval(this.streamStatusInterval);
                this.streamStatusInterval = null;
            }

            // Cleanup inbound stream
            if (this.inboundStream) {
                this.inboundStream.doCleanup();
                this.inboundStream = null;
            }

            // Cleanup outbound stream
            if (this.outboundStream) {
                this.outboundStream.doCleanup();
                this.outboundStream = null;
            }

            logger.info('[WEBRTC] Cleanup completed successfully.');
        } catch (error) {
            logger.error('Error during WebRTC cleanup:', error);
        } finally {
            this.isCleanupInProgress = false;
        }
    }

    public setUserInitiatedStop(isUserInitiated: boolean): void {
        if (this.inboundStream) {
            this.inboundStream.setUserInitiatedStop(isUserInitiated);
        }
        if (this.outboundStream) {
            // Add when OutboundStream has retry support
            // this.outboundStream.setUserInitiatedStop(isUserInitiated);
        }
    }

    public updateRetryConfig(config: RetryConfig): void {
        if (this.inboundStream) {
            this.inboundStream.updateRetryConfig(config);
        }
        if (this.outboundStream) {
            // Add when OutboundStream has retry support
            // this.outboundStream.updateRetryConfig(config);
        }
    }

    public resetRetryStates(): void {
        if (this.inboundStream) {
            this.inboundStream.resetRetryState();
        }
        if (this.outboundStream) {
            // Add when OutboundStream has retry support
            // this.outboundStream.resetRetryState();
        }
    }
}
