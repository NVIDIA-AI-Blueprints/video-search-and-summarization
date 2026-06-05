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
import { IceServer, BitrateSettings, WebRTCConfig, RetryConfig, RetryState, StreamRestartInfo } from '../../utils/interfaces';
import StreamManager from '../../StreamManager';
import getAPIPath, { StreamType } from '../../utils/apis';
import { logger } from '../../utils/logger';
import { getPublicIPAddress } from '../../utils/trickleICE';
import { ErrorType, ErrorTypes } from '../../utils/error';
import { InboundPeerConnection } from './InboundPeerConnection';
import { InboundSignaling } from './InboundSignaling';

const WATCH_DOG_TIMER = 45000;
const DEFAULT_RETRY_CONFIG: RetryConfig = {
    maxRetries: 3,
    retryDelayMs: 2000,
    backoffMultiplier: 2,
    maxRetryDelayMs: 10000,
};

export default class InboundStream {
    public mediaStream: MediaStream | null = null;
    public peerId: string;
    public peerConnection: RTCPeerConnection | null = null;
    public earlyCandidates: RTCIceCandidate[] = [];
    public earlyCandidatesFromServer: RTCIceCandidate[] = [];
    public isSDPAnswerProcessed: boolean = false;
    public iceServers: IceServer[] | null = null;
    public isConnected: boolean = false;
    public minBitrate: number | null = null;
    public maxBitrate: number | null = null;
    public startBitrate: number | null = null;
    public publicIPAddress!: string | null;
    public connectionWatchDog: NodeJS.Timeout | null = null;
    public streamManager: StreamManager;
    public streamType: StreamType;
    public isUserInitiatedStop: boolean = false;
    public retryConfig: RetryConfig = DEFAULT_RETRY_CONFIG;
    public retryState: RetryState = {
        currentRetryCount: 0,
        isRetrying: false,
        lastRetryTime: 0,
        retryTimer: null,
    };

    private peerConnectionManager: InboundPeerConnection;
    private signalingManager: InboundSignaling;

    constructor(streamManager: StreamManager, peerId: string) {
        this.streamManager = streamManager;
        this.peerId = peerId;
        this.streamType = this.streamManager.streamType;
        this.peerConnectionManager = new InboundPeerConnection(this);
        this.signalingManager = new InboundSignaling(this);
    }

    public initiate(): void {
        logger.info('[INBOUND_STREAM] Initiating...');
        this.startWatchDog();
        this.getVstConfig();
        this.mediaStream = new MediaStream();
    }

    public getPeerConnectionObject(): RTCPeerConnection | null {
        return this.peerConnection;
    }

    public isStreamConnected(): boolean {
        return this.isConnected;
    }

    public async handleWebSocketMessage(msg: string): Promise<void> {
        await this.signalingManager.handleWebSocketMessage(msg);
    }

    public generateErrorCode(errorMessage: string): number {
        // Simple hash function to convert string to number
        let hash = 0;
        for (let i = 0; i < errorMessage.length; i++) {
            const char = errorMessage.charCodeAt(i);
            hash = (hash << 5) - hash + char;
            hash = hash & hash; // Convert to 32bit integer
        }
        // Ensure the result is positive and under 100
        return Math.abs(hash % 100);
    }

    public updateMediaSessionId(data: { mediaSessionId?: string }): void {
        if (data && data.mediaSessionId) {
            this.streamManager.setMediaSessionId(data.mediaSessionId);
        }
    }

    public startWatchDog(): void {
        // Clear any existing watchdog timer first to prevent multiple timers
        if (this.connectionWatchDog) {
            clearTimeout(this.connectionWatchDog);
            this.connectionWatchDog = null;
        }

        this.connectionWatchDog = setTimeout(() => {
            if (this.isConnected == false) {
                const errorType: ErrorType = ErrorTypes.INBOUND_STREAM_ERROR;
                this.streamManager.handleWebRTCError(errorType);
            }
        }, WATCH_DOG_TIMER);
    }

    public clearWatchDog(): void {
        if (this.connectionWatchDog) {
            clearTimeout(this.connectionWatchDog);
            this.connectionWatchDog = null;
            logger.debug('[INBOUND_STREAM] Watchdog timer cleared on successful connection.');
        }
    }

    public addStreamDataListener(): void {
        const videoElement = document.getElementById(this.streamManager.getConfig().inboundStreamVideoElementId);
        if (videoElement && !videoElement.hasAttribute('data-listener-added')) {
            videoElement.setAttribute('data-listener-added', 'true');
            videoElement.addEventListener('loadeddata', () => {
                logger.info('[INBOUND_STREAM] First frame loaded.');
                // callback when first frame is received. To be used to show loading GIF
                const callback = this.streamManager.getConfig().firstFrameReceivedCallback;
                if (callback) {
                    callback();
                }
            });
        }
    }

    public async getPublicAddress(iceServers: { iceServers: IceServer[] }): Promise<void> {
        this.publicIPAddress = await getPublicIPAddress(iceServers);
        logger.debug('Inbound Stream ----> ', 'Public IP address is', this.publicIPAddress);
        if (this.publicIPAddress) {
            this.streamManager.setPublicIPAddress(this.publicIPAddress);
        }
    }

    public async handleSDPOffer(sdpOffer: { sessionDescription: RTCSessionDescriptionInit; streamId: string }): Promise<void> {
        if (!sdpOffer || !sdpOffer.sessionDescription || !sdpOffer.streamId) {
            logger.warn('[INBOUND_STREAM] Received incomplete SDP offer data, skipping.');
            return;
        }
        this.startPeerConnection(this.iceServers, sdpOffer.sessionDescription);
    }

    public startPeerConnection(iceServers: IceServer[] | null, extendedOffer: RTCSessionDescriptionInit): void {
        this.createRTCPeerConnection(iceServers, false);
        this.processSDPOffer(extendedOffer);
    }

    public processICECandidate(candidate: RTCIceCandidateInit): void {
        if (candidate) {
            logger.debug('[INBOUND_STREAM]', `Adding ICE candidate - :${JSON.stringify(candidate)}`);
            this.peerConnection
                ?.addIceCandidate(candidate)
                .then(() => {
                    logger.debug('[INBOUND_STREAM]', `addIceCandidate OK`);
                })
                .catch(error => {
                    logger.debug('[INBOUND_STREAM]', `addIceCandidate error`, error);
                });
        }
    }

    public processSDPOffer(extendedOffer: RTCSessionDescriptionInit): void {
        if (this.peerConnection) {
            logger.debug('SDP Offer: ', extendedOffer);
            this.peerConnection
                .setRemoteDescription(new RTCSessionDescription(extendedOffer))
                .then(() => {
                    logger.debug('[INBOUND_STREAM]', 'setRemoteDescription complete, creating answer');
                    this.isSDPAnswerProcessed = true; //TODO: in case of UE its SDP offer. Refactor name later.
                    // process the cached VST candidates
                    logger.debug('[INBOUND_STREAM]', 'SDP offer processed, process cached VST ICE candidates');
                    this.processReceivedICECandidate(this.earlyCandidatesFromServer);
                    return this.peerConnection?.createAnswer();
                })
                .then(sessionDescription => {
                    logger.debug('[INBOUND_STREAM]', 'createAnswer complete, setLocalDescription');
                    return this.peerConnection?.setLocalDescription(sessionDescription);
                })
                .then(() => {
                    logger.debug('[INBOUND_STREAM]', '**** Adding remote early candidates *****', this.earlyCandidates);
                    this.earlyCandidates.forEach(this.processICECandidate.bind(this));
                    logger.debug('[INBOUND_STREAM]', 'setLocalDescription complete, sendAnswer');
                    const answerPayload = {
                        apiKey: getAPIPath(this.streamType).setAnswer,
                        peerId: this.peerId,
                        data: {
                            sessionDescription: this.peerConnection?.localDescription,
                            peerId: this.peerId,
                        },
                    };
                    const jsonString = JSON.stringify(answerPayload);
                    this.streamManager.sendWebSocketMessage(jsonString);
                })
                .catch(e => {
                    logger.error('Error during offer handling: ', e);
                });
        }
    }

    public getVstConfig(): void {
        logger.debug('[INBOUND_STREAM]', 'Calling getVstConfig');
        const jsonData = {
            apiKey: getAPIPath(this.streamType).configuration,
            data: null,
            peerId: this.peerId,
        };
        const jsonString = JSON.stringify(jsonData);
        this.streamManager.sendWebSocketMessage(jsonString);
    }

    public rewriteSdp(sdp: RTCSessionDescriptionInit): RTCSessionDescriptionInit {
        logger.debug('[INBOUND_STREAM]', 'Rewriting SDP with bitrates');
        if (this.maxBitrate && this.minBitrate && this.startBitrate) {
            const sdpStringFind = 'a=fmtp:(.*) (.*)';
            const sdpStringReplace = `a=fmtp:$1 $2;x-google-max-bitrate=${this.maxBitrate};x-google-min-bitrate=${this.minBitrate};x-google-start-bitrate=${this.startBitrate}`;
            let newSDP = sdp.sdp?.toString();
            newSDP = newSDP?.replace(new RegExp(sdpStringFind, 'g'), sdpStringReplace);
            sdp.sdp = newSDP;
            logger.debug('[INBOUND_STREAM]', 'using modified SDP Answer: ', sdp);
        }
        return sdp;
    }

    public processReceivedICECandidate(candidates: RTCIceCandidateInit[]): void {
        logger.debug('[INBOUND_STREAM]', `Received candidates from VMS: ${JSON.stringify(candidates)}`);
        if (candidates) {
            logger.debug('[INBOUND_STREAM]', 'Creating RTCIceCandidate from each received candidate..');
            for (let i = 0; i < candidates.length; i += 1) {
                const candidate = candidates[i];
                logger.debug('[INBOUND_STREAM]', `Adding ICE candidate - ${i} :${JSON.stringify(candidate)}`);
                this.peerConnection
                    ?.addIceCandidate(candidate)
                    .then(() => {
                        logger.debug('[INBOUND_STREAM]', `addIceCandidate OK - ${i}`);
                    })
                    .catch(error => {
                        logger.debug('[INBOUND_STREAM]', `addIceCandidate error - ${i}`, error);
                    });
            }
        }
    }

    public setRemoteDescription(sessionDescriptionAnswer: RTCSessionDescriptionInit): void {
        if (this.peerConnection) {
            this.peerConnection
                .setRemoteDescription(new RTCSessionDescription(sessionDescriptionAnswer))
                .then(() => {
                    this.isSDPAnswerProcessed = true;
                    // process VST candidates that were received before SDP answer
                    logger.debug(
                        '[INBOUND_STREAM]',
                        'SDP answer processed, processing VST early candidates',
                        this.earlyCandidatesFromServer
                    );
                    this.processReceivedICECandidate(this.earlyCandidatesFromServer);
                    this.earlyCandidates.forEach(this.peerConnectionManager.addIceCandidate.bind(this.peerConnectionManager));
                })
                .catch(e => {
                    logger.error('Failed to set remote description', e);
                    const errorType: ErrorType = ErrorTypes.INBOUND_STREAM_REMOTE_DESCRIPTION_ERROR;
                    this.streamManager.handleWebRTCError(errorType);
                });
        } else {
            logger.error('Failed to set remote description, no peer connection found');
            const errorType: ErrorType = ErrorTypes.INBOUND_STREAM_ERROR;
            this.streamManager.handleWebRTCError(errorType);
        }
    }

    public sendSessionDescriptionToVst(sessionDescription: RTCSessionDescriptionInit): void {
        interface SessionDescriptionPayload {
            [key: string]: unknown;
        }
        const sessionDescriptionPayload: SessionDescriptionPayload = {
            apiKey: getAPIPath(this.streamType).startStream,
            peerId: this.peerId,
            data: {
                clientIpAddr: this.publicIPAddress,
                peerId: this.peerId,
                sessionDescription,
                options: { quality: 'auto', rtptransport: 'udp', timeout: 60 },
            },
        };

        logger.debug('[INBOUND_STREAM]', 'stream/start payload', sessionDescriptionPayload);
        if (this.streamManager.streamConfig?.streamId) {
            (sessionDescriptionPayload.data as Record<string, unknown>).streamId = this.streamManager.streamConfig?.streamId;
        }
        if (this.streamManager.streamConfig?.startTime) {
            (sessionDescriptionPayload.data as Record<string, unknown>).startTime = this.streamManager.streamConfig?.startTime;
        }
        if (this.streamManager.streamConfig?.endTime) {
            (sessionDescriptionPayload.data as Record<string, unknown>).endTime = this.streamManager.streamConfig?.endTime;
        }
        if (this.streamManager.streamConfig?.options) {
            const options = { ...this.streamManager.streamConfig.options };

            // Add needHalo with default value false only for live streams
            if (this.streamType === StreamType.Live) {
                if (options.overlay) {
                    options.overlay = {
                        ...options.overlay,
                        needHalo: options.overlay.needHalo ?? false,
                    };
                } else {
                    options.overlay = {
                        needHalo: false,
                    };
                }
            }

            (sessionDescriptionPayload.data as Record<string, unknown>).options = options;
        }
        if (this.streamManager.streamConfig?.tag) {
            (sessionDescriptionPayload.data as Record<string, unknown>).tag = this.streamManager.streamConfig?.tag;
        }

        logger.debug('[INBOUND_STREAM]', 'Payload of stream start: ', sessionDescriptionPayload);
        const jsonString = JSON.stringify(sessionDescriptionPayload);
        this.streamManager.sendWebSocketMessage(jsonString);
    }

    public createRTCPeerConnection(iceServerList: IceServer[] | null, flag: boolean = true): void {
        this.peerConnectionManager.create(iceServerList, flag);
    }

    public getIceServers(): void {
        logger.debug('[INBOUND_STREAM]', 'Calling getIceServers', this.peerId);
        const jsonData = {
            apiKey: getAPIPath(this.streamType).iceServers,
            peerId: this.peerId,
            data: { peerId: this.peerId },
        };
        const jsonString = JSON.stringify(jsonData);
        this.streamManager.sendWebSocketMessage(jsonString);
    }

    public getBitrateSettings(config: WebRTCConfig): BitrateSettings {
        try {
            const resolution = config.WebrtcOutDefaultResolution;
            const height = parseInt(resolution.split('x')[1], 10);
            let settings = null;
            switch (height) {
                case 480:
                    settings = config.webrtc_video_quality_tunning.resolution_480;
                    break;
                case 720:
                    settings = config.webrtc_video_quality_tunning.resolution_720;
                    break;
                case 1080:
                    settings = config.webrtc_video_quality_tunning.resolution_1080;
                    break;
                case 1440:
                    settings = config.webrtc_video_quality_tunning.resolution_1440;
                    break;
                case 2160:
                    settings = config.webrtc_video_quality_tunning.resolution_2160;
                    break;
                default:
                    throw new Error(`Unsupported resolution height: ${height}`);
            }
            const [bitrate_min, bitrate_max] = settings.bitrate_range;
            return {
                bitrate_min,
                bitrate_max,
                bitrate_start: settings.bitrate_start,
            };
        } catch (error) {
            logger.debug('[INBOUND_STREAM]', 'Failed to do get bitrates');
            return {
                bitrate_min: null,
                bitrate_max: null,
                bitrate_start: null,
            };
        }
    }

    public doCleanup(): void {
        logger.info('[INBOUND_STREAM] Cleanup process started.');

        if (this.connectionWatchDog) {
            clearTimeout(this.connectionWatchDog);
            this.connectionWatchDog = null;
        }

        // Reset retry state
        this.resetRetryState();

        // Stop and clear media streams
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }

        // Close peer connection
        if (this.peerConnection) {
            // Detach event handlers to prevent re-triggering cleanup
            this.peerConnection.oniceconnectionstatechange = null;
            this.peerConnection.onconnectionstatechange = null;
            this.peerConnection.onicecandidateerror = null;
            this.peerConnection.onicegatheringstatechange = null;
            this.peerConnection.onsignalingstatechange = null;
            this.peerConnection.ontrack = null;

            const jsonPayload = {
                apiKey: getAPIPath(this.streamType).stopStream,
                peerId: this.peerId,
                data: {
                    peerId: this.peerId,
                    mediaSessionId: this.streamManager.getMediaSessionId(),
                },
            };
            this.streamManager.sendWebSocketMessage(JSON.stringify(jsonPayload));
            this.peerConnection.close();
            this.peerConnection = null;
        }

        // Cleanup peer connection manager (includes WebRTC Issue Detector)
        if (this.peerConnectionManager) {
            this.peerConnectionManager.cleanup();
        }

        // Clear video element
        const videoElement = document.getElementById(this.streamManager.getConfig().inboundStreamVideoElementId) as HTMLVideoElement | null;
        if (videoElement) {
            videoElement.srcObject = null;
            videoElement.load();
        }

        this.isSDPAnswerProcessed = false;
        this.earlyCandidates = [];
        this.earlyCandidatesFromServer = [];
        this.isConnected = false;
        this.isUserInitiatedStop = false;

        logger.info('[INBOUND_STREAM] Cleanup completed.');
    }

    public setUserInitiatedStop(isUserInitiated: boolean): void {
        this.isUserInitiatedStop = isUserInitiated;
    }

    public updateRetryConfig(config: Partial<RetryConfig>): void {
        this.retryConfig = { ...this.retryConfig, ...config };
    }

    public resetRetryState(): void {
        if (this.retryState.retryTimer) {
            clearTimeout(this.retryState.retryTimer);
            this.retryState.retryTimer = null;
        }
        this.retryState.currentRetryCount = 0;
        this.retryState.isRetrying = false;
        this.retryState.lastRetryTime = 0;
    }

    public notifyWebRTCRestart(reason: string): void {
        const restartInfo: StreamRestartInfo = {
            type: 'webrtc',
            reason,
            retryAttempt: this.retryState.currentRetryCount,
            timestamp: Date.now(),
            previousFailureTime: this.retryState.lastRetryTime,
        };
        this.streamManager.notifyStreamRestart(restartInfo);
    }

    private calculateRetryDelay(): number {
        const baseDelay = this.retryConfig.retryDelayMs;
        const multiplier = Math.pow(this.retryConfig.backoffMultiplier, this.retryState.currentRetryCount);
        const calculatedDelay = baseDelay * multiplier;
        return Math.min(calculatedDelay, this.retryConfig.maxRetryDelayMs);
    }

    public async attemptRetry(reason: string): Promise<void> {
        if (this.isUserInitiatedStop) {
            logger.info('[INBOUND_STREAM] User initiated stop, skipping retry');
            return;
        }

        // Check if WebSocket is retrying - if so, let WebSocket handle the full restart
        if (this.streamManager.isWebSocketRetrying()) {
            logger.info('[INBOUND_STREAM] WebSocket is retrying, skipping WebRTC retry (will be handled by WebSocket retry)');
            return;
        }

        if (this.retryState.currentRetryCount >= this.retryConfig.maxRetries) {
            logger.error('[INBOUND_STREAM] Max retry attempts reached, stopping retry');
            this.resetRetryState();
            await this.streamManager.handleAppCleanup('Max retry attempts reached');
            return;
        }

        if (this.retryState.isRetrying) {
            logger.debug('[INBOUND_STREAM] Retry already in progress, skipping');
            return;
        }

        this.retryState.isRetrying = true;
        this.retryState.currentRetryCount++;
        this.retryState.lastRetryTime = Date.now();

        const retryDelay = this.calculateRetryDelay();
        logger.info(
            `[INBOUND_STREAM] Attempting retry ${this.retryState.currentRetryCount}/${this.retryConfig.maxRetries} after ${retryDelay}ms. Reason: ${reason}`
        );

        this.retryState.retryTimer = setTimeout(async () => {
            try {
                await this.performRetry();
            } catch (error) {
                logger.error('[INBOUND_STREAM] Retry failed:', error);
                this.retryState.isRetrying = false;
                // Try again if we haven't reached max retries
                if (this.retryState.currentRetryCount < this.retryConfig.maxRetries) {
                    await this.attemptRetry('Retry failed, attempting again');
                } else {
                    await this.streamManager.handleAppCleanup('All retry attempts failed');
                }
            }
        }, retryDelay);
    }

    private async performRetry(): Promise<void> {
        logger.info('[INBOUND_STREAM] Performing retry...');

        // Comprehensive cleanup before retry
        this.cleanupForRetry();

        // Start fresh connection
        this.startWatchDog();
        this.getVstConfig();

        this.retryState.isRetrying = false;
        logger.info('[INBOUND_STREAM] Retry attempt completed');
    }

    private cleanupForRetry(): void {
        logger.debug('[INBOUND_STREAM] Performing comprehensive cleanup for retry...');

        // Clear watchdog timer
        this.clearWatchDog();

        // Stop and clear media streams
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => {
                track.stop();
                logger.debug(`[INBOUND_STREAM] Stopped media track: ${track.kind}`);
            });
            this.mediaStream = null;
        }

        // Close peer connection with proper event handler cleanup
        if (this.peerConnection) {
            // Detach all event handlers to prevent callbacks during cleanup
            this.peerConnection.onicecandidate = null;
            this.peerConnection.oniceconnectionstatechange = null;
            this.peerConnection.onicecandidateerror = null;
            this.peerConnection.onicegatheringstatechange = null;
            this.peerConnection.onsignalingstatechange = null;
            this.peerConnection.onconnectionstatechange = null;
            this.peerConnection.ontrack = null;

            this.peerConnection.close();
            this.peerConnection = null;
            logger.debug('[INBOUND_STREAM] Peer connection closed and event handlers detached');
        }

        // Cleanup peer connection manager (includes WebRTC Issue Detector cleanup)
        if (this.peerConnectionManager) {
            this.peerConnectionManager.cleanup();
            logger.debug('[INBOUND_STREAM] Peer connection manager cleaned up');
        }

        // Clear video element
        const videoElement = document.getElementById(this.streamManager.getConfig().inboundStreamVideoElementId) as HTMLVideoElement | null;
        if (videoElement) {
            videoElement.srcObject = null;
            // Remove the data-listener-added attribute so it can be re-added
            videoElement.removeAttribute('data-listener-added');
            videoElement.load();
            logger.debug('[INBOUND_STREAM] Video element cleared and reset');
        }

        // Reset state variables
        this.isSDPAnswerProcessed = false;
        this.earlyCandidates = [];
        this.earlyCandidatesFromServer = [];
        this.isConnected = false;

        // Reset media stream to prepare for new connection
        this.mediaStream = new MediaStream();

        logger.debug('[INBOUND_STREAM] Comprehensive cleanup for retry completed');
    }
}
