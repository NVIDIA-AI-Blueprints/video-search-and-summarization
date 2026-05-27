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
import { IceServer } from '../../utils/interfaces';
import { logger } from '../../utils/logger';
import getAPIPath from '../../utils/apis';
import InboundStream from './InboundStream';
import { VSTWebRTCIssueDetector, WebRTCIssueDetectorCallbacks } from '../WebRTCIssueDetector';

export class InboundPeerConnection {
    private stream: InboundStream;
    private webRtcIssueDetector: VSTWebRTCIssueDetector | null = null;
    private disconnectionTimer: NodeJS.Timeout | null = null;
    private readonly DISCONNECTION_TIMEOUT = 10000; // 10 seconds to wait for natural recovery

    constructor(stream: InboundStream) {
        this.stream = stream;
        this.initializeIssueDetector();
    }

    private initializeIssueDetector(): void {
        const config = this.stream.streamManager.getConfig();
        const callbacks: WebRTCIssueDetectorCallbacks = {
            onIssueDetected: config.onWebRTCIssueDetected,
            onNetworkScoresUpdated: config.onWebRTCNetworkScoresUpdated,
        };

        this.webRtcIssueDetector = new VSTWebRTCIssueDetector('inbound', callbacks);
    }

    public create(iceServerList: IceServer[] | null, flag: boolean = true): void {
        const rtcConfiguration: RTCConfiguration = {
            iceServers: iceServerList && iceServerList.length > 0 ? iceServerList : [],
        };
        logger.debug('[INBOUND_STREAM]', 'createRTCPeerConnection');
        try {
            // Start issue detection before creating peer connection
            this.webRtcIssueDetector?.start();

            this.stream.peerConnection = new RTCPeerConnection(rtcConfiguration);
            this.stream.peerConnection.onicecandidate = this.onICECandidate.bind(this);
            this.stream.peerConnection.oniceconnectionstatechange = this.onICEConnectionStateChange.bind(this);
            this.stream.peerConnection.onicecandidateerror = this.onICECandidateError.bind(this);
            this.stream.peerConnection.onicegatheringstatechange = this.onICEGatheringStateChange.bind(this);
            this.stream.peerConnection.onsignalingstatechange = this.onSignalingStateChange.bind(this);
            this.stream.peerConnection.onconnectionstatechange = this.onConnectionStateChange.bind(this);
            this.stream.peerConnection.ontrack = this.onTrack.bind(this);

            logger.info('[WEBRTC_ISSUE_DETECTOR] Inbound peer connection created, issue detection is active');
        } catch (error) {
            logger.error('[INBOUND_STREAM]', `Failed to create RTC peer connection.`, error);
        }
        if (flag) {
            this.stream.addStreamDataListener();
            this.createOffer();
        }
    }

    public createOffer(): void {
        logger.debug('[INBOUND_STREAM]', 'Creating Offer');
        this.stream.peerConnection?.addTransceiver('audio', {
            direction: 'recvonly',
        });
        this.stream.peerConnection?.addTransceiver('video', {
            direction: 'recvonly',
        });
        logger.debug('Offer to recvonly');
        this.stream.peerConnection
            ?.createOffer()
            .then(sessionDescription => {
                logger.debug('[INBOUND_STREAM]', 'session Description with bitrates', sessionDescription);
                this.stream.peerConnection?.setLocalDescription(sessionDescription);
                sessionDescription = this.stream.rewriteSdp(sessionDescription);
                return sessionDescription;
            })
            .then(sessionDescription => {
                this.stream.sendSessionDescriptionToVst(sessionDescription);
            })
            .catch(error => {
                logger.error('Failed to create offer', error);
            });
    }

    public onICECandidate(event: RTCPeerConnectionIceEvent): void {
        if (!event.candidate) {
            logger.debug('[INBOUND_STREAM]', 'received null candidate, that means its the last candidate - client is chromium based');
            return;
        }
        if (event.candidate.candidate.length === 0) {
            logger.debug('[INBOUND_STREAM]', 'Received empty string for candidate - client is firefox');
            return;
        }
        logger.debug('[INBOUND_STREAM]', 'received candidate inside onIceCandidate callback: ', event.candidate.candidate);
        if (event.candidate.type === 'srflx') {
            logger.debug('[INBOUND_STREAM]', 'The STUN server is reachable for this candidate!');
            logger.debug('[INBOUND_STREAM]', `Public IP Address is: ${event.candidate.address}`);
        }
        if (event.candidate.type === 'relay') {
            logger.debug('[INBOUND_STREAM]', 'The TURN server is reachable for this candidate!');
        }
        if (this.stream.peerConnection && this.stream.peerConnection.currentRemoteDescription) {
            this.addIceCandidate(event.candidate);
        } else {
            this.stream.earlyCandidates.push(event.candidate);
        }
    }

    public async onICEConnectionStateChange(): Promise<void> {
        logger.debug('[INBOUND_STREAM] ICE connection state change:', this.stream.peerConnection?.iceConnectionState || '');

        const iceConnectionState = this.stream.peerConnection?.iceConnectionState;

        if (iceConnectionState === 'new') {
            // Candidates will come async over websocket
        }

        if (iceConnectionState === 'connected') {
            // Clear any pending disconnection timer since we're connected
            if (this.disconnectionTimer) {
                clearTimeout(this.disconnectionTimer);
                this.disconnectionTimer = null;
            }

            // Check if this is a restart (retry count > 0) before resetting
            const wasRetrying = this.stream.retryState.currentRetryCount > 0;
            const retryAttempt = this.stream.retryState.currentRetryCount;

            // Reset retry state on successful connection
            this.stream.resetRetryState();

            const statusPayload = {
                apiKey: getAPIPath(this.stream.streamType).peerConnectionStatus,
                peerId: this.stream.peerId,
                data: { status: 'connected', peerId: this.stream.peerId },
            };
            this.stream.isConnected = true;

            // Clear the watchdog timer since connection is now successful
            this.stream.clearWatchDog();
            if (getAPIPath(this.stream.streamType).peerConnectionStatus !== '') {
                const jsonString = JSON.stringify(statusPayload);
                this.stream.streamManager.sendWebSocketMessage(jsonString);
            }
            this.stream.streamManager.onInboundStreamConnection();
            this.stream.streamManager.setInboundStreamConnectionStatus(true);

            // Notify client of WebRTC restart if this was a retry
            if (wasRetrying) {
                this.stream.notifyWebRTCRestart(`WebRTC connection restored after ${retryAttempt} retry attempt(s)`);
            }
        }

        if (iceConnectionState === 'disconnected') {
            logger.warn('[INBOUND_STREAM] ICE connection disconnected.');

            // Set a timer to wait for natural recovery before attempting retry
            // WebRTC can naturally recover from disconnected state
            if (!this.disconnectionTimer) {
                this.disconnectionTimer = setTimeout(async () => {
                    logger.warn('[INBOUND_STREAM] ICE connection remained disconnected, attempting retry');

                    const statusPayload = {
                        apiKey: getAPIPath(this.stream.streamType).peerConnectionStatus,
                        peerId: this.stream.peerId,
                        data: { status: 'disconnected', peerId: this.stream.peerId },
                    };
                    if (getAPIPath(this.stream.streamType).peerConnectionStatus !== '') {
                        const jsonString = JSON.stringify(statusPayload);
                        this.stream.streamManager.sendWebSocketMessage(jsonString);
                    }
                    this.webRtcIssueDetector?.stop();

                    // Attempt retry instead of immediate cleanup
                    await this.stream.attemptRetry('ICE connection disconnected for too long');
                }, this.DISCONNECTION_TIMEOUT);
            }
        }

        if (iceConnectionState === 'failed') {
            logger.error('[INBOUND_STREAM] ICE connection failed.');

            // Clear any pending disconnection timer
            if (this.disconnectionTimer) {
                clearTimeout(this.disconnectionTimer);
                this.disconnectionTimer = null;
            }

            this.webRtcIssueDetector?.stop();

            // Attempt retry instead of immediate cleanup
            await this.stream.attemptRetry('ICE connection failed');
        }
    }

    public onICECandidateError(e: RTCPeerConnectionIceErrorEvent): void {
        logger.warn('[INBOUND_STREAM] ICE candidate error:', e);
        if (e.errorCode !== 701) {
            logger.error(`${e.errorText} error code ${e.errorCode} and url ${e.url}`, 'onIceCandidateError');
        }
        if (e.errorCode === 701) {
            logger.debug('[INBOUND_STREAM]', 'error code is 701 that means DNS failed for ipv6, harmless error');
        }
    }

    public onICEGatheringStateChange(): void {
        logger.debug('[INBOUND_STREAM] ICE gathering state change:', this.stream.peerConnection?.iceGatheringState || '');
    }

    public onSignalingStateChange(): void {
        logger.debug('[INBOUND_STREAM] Signaling state change:', this.stream.peerConnection?.signalingState || '');
    }

    public onConnectionStateChange(): void {
        logger.debug('[INBOUND_STREAM] Peer connection state change:', this.stream.peerConnection?.connectionState || '');
        if (this.stream.peerConnection?.connectionState === 'disconnected') {
            logger.warn('[INBOUND_STREAM] Peer connection disconnected.');
        }
        if (this.stream.peerConnection?.connectionState === 'closed') {
            logger.debug('[INBOUND_STREAM] Peer connection closed.');
            this.cleanup();
        }
    }

    public cleanup(): void {
        logger.debug('[INBOUND_STREAM] Cleaning up InboundPeerConnection');

        // Clear disconnection timer
        if (this.disconnectionTimer) {
            clearTimeout(this.disconnectionTimer);
            this.disconnectionTimer = null;
        }

        this.webRtcIssueDetector?.cleanup();
        this.webRtcIssueDetector = null;
    }

    public onTrack(event: RTCTrackEvent): void {
        logger.info('[INBOUND_STREAM] Media track received.');
        const [stream] = event.streams;
        const videoElement = document.getElementById(this.stream.streamManager.getConfig().inboundStreamVideoElementId) as HTMLVideoElement;

        if (videoElement) {
            logger.debug('[INBOUND_STREAM]', 'stream received', stream);
            videoElement.srcObject = stream;

            // Attempt to play the video element
            const playPromise = videoElement.play();
            if (playPromise !== undefined) {
                playPromise
                    .then(() => {
                        logger.debug('[INBOUND_STREAM]', 'Autoplay with audio successful');
                        videoElement.muted = false;
                    })
                    .catch(() => {
                        // Handle autoplay failure
                        logger.warn('[INBOUND_STREAM] Autoplay with audio failed. Retrying muted.');
                        videoElement.muted = true; // Mute and try again
                        videoElement.play();
                    });
            }
        } else {
            logger.error('Video element not found');
        }
    }

    public addIceCandidate(candidate: RTCIceCandidateInit): void {
        const jsonData = {
            apiKey: getAPIPath(this.stream.streamType).iceCandidate,
            peerId: this.stream.peerId,
            data: {
                peerId: this.stream.peerId,
                candidate: candidate,
            },
        };
        logger.debug('[INBOUND_STREAM]', 'calling /v1/iceCandidate with ', jsonData);
        const jsonString = JSON.stringify(jsonData);
        this.stream.streamManager.sendWebSocketMessage(jsonString);
    }
}
