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

import { IceServer } from '../../utils/interfaces';
import { logger } from '../../utils/logger';
import getAPIPath from '../../utils/apis';
import OutboundStream from './OutboundStream';
import { ErrorType, ErrorTypes } from '../../utils/error';
import { VSTWebRTCIssueDetector, WebRTCIssueDetectorCallbacks } from '../WebRTCIssueDetector';

export class OutboundPeerConnection {
    private stream: OutboundStream;
    private webRtcIssueDetector: VSTWebRTCIssueDetector | null = null;

    constructor(stream: OutboundStream) {
        this.stream = stream;
        this.initializeIssueDetector();
    }

    private initializeIssueDetector(): void {
        const config = this.stream.streamManager.getConfig();
        const callbacks: WebRTCIssueDetectorCallbacks = {
            onIssueDetected: config.onWebRTCIssueDetected,
            onNetworkScoresUpdated: config.onWebRTCNetworkScoresUpdated,
        };

        this.webRtcIssueDetector = new VSTWebRTCIssueDetector('outbound', callbacks);
    }

    public create(iceServerList: IceServer[]): void {
        const rtcConfiguration: RTCConfiguration = {
            iceServers: iceServerList,
        };
        logger.debug('[OUTBOUND_STREAM]', 'createRTCPeerConnection');
        try {
            // Start issue detection before creating peer connection
            this.webRtcIssueDetector?.start();

            this.stream.peerConnection = new RTCPeerConnection(rtcConfiguration);
            this.stream.peerConnection.onicecandidate = this.onIceCandidate.bind(this);
            this.stream.peerConnection.oniceconnectionstatechange = this.onIceConnectionStateChange.bind(this);
            this.stream.peerConnection.onicecandidateerror = this.onIceCandidateError.bind(this);
            this.stream.peerConnection.onicegatheringstatechange = this.onIceGatheringStateChange.bind(this);
            this.stream.peerConnection.onsignalingstatechange = this.onSignalingStateChange.bind(this);
            this.stream.peerConnection.onconnectionstatechange = this.onConnectionStateChange.bind(this);

            logger.info('[WEBRTC_ISSUE_DETECTOR] Outbound peer connection created, issue detection is active');
        } catch (error) {
            logger.error('[OUTBOUND_STREAM]', `Failed to create RTC peer connection.`, error);
        }
        this.createOffer();
    }

    private createOffer(): void {
        logger.debug('[OUTBOUND_STREAM]', 'Creating Offer');
        this.stream.mediaStream
            ?.getTracks()
            .forEach(track => this.stream.peerConnection?.addTrack(track, this.stream.mediaStream as MediaStream));
        this.stream.peerConnection?.addTransceiver('audio', {
            direction: 'sendonly',
        });
        this.stream.peerConnection?.addTransceiver('video', {
            direction: 'sendonly',
        });
        logger.debug('Offer to sendonly');
        this.stream.peerConnection
            ?.createOffer()
            .then(sessionDescription => {
                logger.debug('[OUTBOUND_STREAM]', 'session Description with bitrates', sessionDescription);
                this.stream.peerConnection?.setLocalDescription(sessionDescription);
                return sessionDescription;
            })
            .then(sessionDescription => {
                this.sendSessionDescriptionToVst(sessionDescription);
            })
            .catch(error => {
                logger.error('Failed to create offer', error);
            });
    }

    private sendSessionDescriptionToVst(sessionDescription: RTCSessionDescriptionInit): void {
        const sessionDescriptionPayload = {
            apiKey: getAPIPath(this.stream.streamType).startStream,
            peerId: this.stream.peerId,
            data: {
                clientIpAddr: this.stream.publicIPAddress,
                peerId: this.stream.peerId,
                isClient: true,
                options: { quality: 'auto', rtptransport: 'udp', timeout: 60 },
                sessionDescription,
            },
        };
        logger.debug('[OUTBOUND_STREAM]', 'stream/start payload: ', sessionDescriptionPayload);
        const jsonString = JSON.stringify(sessionDescriptionPayload);
        this.stream.streamManager.sendWebSocketMessage(jsonString);
    }

    public onIceCandidate(event: RTCPeerConnectionIceEvent): void {
        if (!event.candidate) {
            logger.debug('[OUTBOUND_STREAM]', 'received null candidate, that means its the last candidate - client is chromium based');
            return;
        }
        if (event.candidate.candidate.length === 0) {
            logger.debug('[OUTBOUND_STREAM]', 'Received empty string for candidate - client is firefox');
            return;
        }
        logger.debug('[OUTBOUND_STREAM]', 'received candidate inside onIceCandidate callback: ', event.candidate.candidate);
        if (event.candidate.type === 'srflx') {
            logger.debug('[OUTBOUND_STREAM]', 'The STUN server is reachable for this candidate!');
            logger.debug('[OUTBOUND_STREAM]', `Public IP Address is: ${event.candidate.address}`);
        }
        if (event.candidate.type === 'relay') {
            logger.debug('[OUTBOUND_STREAM]', 'The TURN server is reachable for this candidate!');
        }
        if (this.stream.peerConnection && this.stream.peerConnection.currentRemoteDescription) {
            this.addIceCandidate(event.candidate);
        } else {
            this.stream.earlyCandidates.push(event.candidate);
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
        logger.debug('[OUTBOUND_STREAM]', 'calling /v1/iceCandidate with ', jsonData);
        const jsonString = JSON.stringify(jsonData);
        this.stream.streamManager.sendWebSocketMessage(jsonString);
    }

    private onIceConnectionStateChange(): void {
        logger.debug('[OUTBOUND_STREAM] ICE connection state change:', this.stream.peerConnection?.iceConnectionState || '');
        if (this.stream.peerConnection?.iceConnectionState === 'new') {
            // Candidates will come async over websocket
        }
        if (this.stream.peerConnection?.iceConnectionState === 'connected') {
            // Handle connected state
            this.stream.isConnected = true;

            // Clear the watchdog timer since connection is now successful
            this.stream.clearWatchDog();

            this.stream.streamManager.setOutboundStreamConnectionStatus(true);
        }
        if (this.stream.peerConnection?.iceConnectionState === 'disconnected') {
            logger.warn('[OUTBOUND_STREAM] ICE connection disconnected.');
            this.webRtcIssueDetector?.stop();
            const errorType: ErrorType = ErrorTypes.OUTBOUND_STREAM_ERROR;
            this.stream.streamManager.handleWebRTCError(errorType);
        }
        if (this.stream.peerConnection?.iceConnectionState === 'failed') {
            logger.error('[OUTBOUND_STREAM] ICE connection failed.');
            this.webRtcIssueDetector?.stop();
            const errorType: ErrorType = ErrorTypes.OUTBOUND_STREAM_ERROR;
            this.stream.streamManager.handleWebRTCError(errorType);
        }
    }

    private onIceCandidateError(event: RTCPeerConnectionIceErrorEvent): void {
        logger.warn('[OUTBOUND_STREAM] ICE candidate error:', event);
        if (event.errorCode !== 701) {
            logger.error(`${event.errorText} error code ${event.errorCode} and url ${event.url}`, 'onIceCandidateError');
        }
        if (event.errorCode === 701) {
            logger.debug('[OUTBOUND_STREAM]', 'error code is 701 that means DNS failed for ipv6, harmless error');
        }
    }

    private onIceGatheringStateChange(): void {
        logger.debug('[OUTBOUND_STREAM] ICE gathering state change:', this.stream.peerConnection?.iceGatheringState || '');
    }

    private onSignalingStateChange(): void {
        logger.debug('[OUTBOUND_STREAM] Signaling state change:', this.stream.peerConnection?.signalingState || '');
    }

    private onConnectionStateChange(): void {
        logger.debug('[OUTBOUND_STREAM] Peer connection state change:', this.stream.peerConnection?.connectionState || '');
        if (this.stream.peerConnection?.connectionState === 'disconnected') {
            logger.warn('[OUTBOUND_STREAM] Peer connection disconnected.');
        }
        if (this.stream.peerConnection?.connectionState === 'closed') {
            logger.debug('[OUTBOUND_STREAM] Peer connection closed.');
            this.cleanup();
        }
    }

    public cleanup(): void {
        logger.debug('[OUTBOUND_STREAM] Cleaning up OutboundPeerConnection');
        this.webRtcIssueDetector?.cleanup();
        this.webRtcIssueDetector = null;
    }
}
