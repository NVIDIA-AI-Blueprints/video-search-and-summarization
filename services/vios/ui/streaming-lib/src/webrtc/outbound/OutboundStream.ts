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
import StreamManager from '../../StreamManager';
import getAPIPath, { StreamType } from '../../utils/apis';
import { logger } from '../../utils/logger';
import { ErrorType, ErrorTypes } from '../../utils/error';
import { OutboundPeerConnection } from './OutboundPeerConnection';
import { OutboundSignaling } from './OutboundSignaling';

const WATCH_DOG_TIMER = 45000;
const HD_WIDTH = 1280;
const HD_HEIGHT = 720;
const FHD_WIDTH = 1920;
const FHD_HEIGHT = 1080;
const THIRTY_FPS = 30;

export default class OutboundStream {
    public mediaStream: MediaStream | null = null;
    public peerId: string;
    public peerConnection: RTCPeerConnection | null = null;
    public earlyCandidates: RTCIceCandidate[] = [];
    public iceServers: IceServer[] | null = null;
    public isConnected: boolean = false;
    public publicIPAddress!: string | null;
    private connectionWatchDog: NodeJS.Timeout | null = null;
    public streamManager: StreamManager;
    public streamType: StreamType;
    public degradationPreference: string | null = null;

    private peerConnectionManager: OutboundPeerConnection;
    private signalingManager: OutboundSignaling;

    constructor(streamManager: StreamManager, peerId: string) {
        this.streamManager = streamManager;
        this.peerId = peerId;
        this.streamType = this.streamManager.streamType;
        this.peerConnectionManager = new OutboundPeerConnection(this);
        this.signalingManager = new OutboundSignaling(this);
    }

    public async initiate(): Promise<void> {
        logger.info('[OUTBOUND_STREAM] Initiating...');
        this.startWatchDog();
        try {
            const constraints: MediaStreamConstraints = {};
            if (this.streamManager.getConfig().enableMicrophone) {
                constraints.audio = { echoCancellation: true };
            }
            if (this.streamManager.getConfig().enableCamera) {
                constraints.video = {
                    width: { min: HD_WIDTH, ideal: HD_WIDTH, max: FHD_WIDTH },
                    height: {
                        min: HD_HEIGHT,
                        ideal: HD_HEIGHT,
                        max: FHD_HEIGHT,
                    },
                    frameRate: { ideal: THIRTY_FPS, max: THIRTY_FPS },
                };
            }
            logger.debug('[OUTBOUND_STREAM]', 'getting user media with constraints', constraints);
            const stream = await navigator.mediaDevices.getUserMedia(constraints);
            this.mediaStream = stream;
            const outboundStreamVideoElement = this.streamManager.getConfig().outboundStreamVideoElementId;
            if (outboundStreamVideoElement) {
                const videoElement = document.getElementById(outboundStreamVideoElement) as HTMLVideoElement | null;
                if (videoElement) {
                    videoElement.srcObject = stream;
                    logger.debug('[OUTBOUND_STREAM]', 'MediaStream: ', this.mediaStream);
                }
            }
        } catch (error) {
            if (error instanceof DOMException) {
                switch (error.name) {
                    case 'NotFoundError':
                        logger.error('Error: No camera and/or microphone found.');
                        break;
                    case 'NotAllowedError':
                        logger.error('Error: Permission to use camera and/or microphone was denied.');
                        break;
                    case 'NotReadableError':
                        logger.error('Error: Could not access camera and/or microphone. The device may be in use by another application.');
                        break;
                    case 'OverconstrainedError':
                        logger.error('Error: The requested media settings are not supported by the device.');
                        break;
                    case 'AbortError':
                        logger.error('Error: The operation was aborted.');
                        break;
                    case 'SecurityError':
                        logger.error('Error: The operation is insecure or the page is not allowed to access media devices.');
                        break;
                    default:
                        logger.error(`Error accessing media devices: ${error.name}`);
                }
            } else {
                logger.error('An unexpected error occurred while trying to access media devices:', error);
            }
            const errorType: ErrorType = ErrorTypes.GET_USER_MEDIA_ERROR;
            this.streamManager.handleWebRTCError(errorType);
            return;
        }
        this.getVstConfig();
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

    public toggleMicrophone = (): boolean | undefined => {
        if (this.mediaStream) {
            logger.debug('[OUTBOUND_STREAM]', 'Toggling microphone');
            try {
                const audoTrack = this.mediaStream.getAudioTracks()[0];
                if (audoTrack) {
                    if (audoTrack.enabled) {
                        audoTrack.enabled = false;
                        logger.debug('[OUTBOUND_STREAM]', 'microphone disabled');
                        return false;
                    } else {
                        audoTrack.enabled = true;
                        logger.debug('[OUTBOUND_STREAM]', 'microphone enabled');
                        return true;
                    }
                }
            } catch (error) {
                logger.error('[OUTBOUND_STREAM]', 'Failed to toggle microphone', error);
            }
        }
        logger.error('[OUTBOUND_STREAM]', 'Media Stream not found');
        return undefined;
    };

    public startWatchDog(): void {
        // Clear any existing watchdog timer first to prevent multiple timers
        if (this.connectionWatchDog) {
            clearTimeout(this.connectionWatchDog);
            this.connectionWatchDog = null;
        }

        this.connectionWatchDog = setTimeout(() => {
            if (this.isConnected == false) {
                const errorType: ErrorType = ErrorTypes.OUTBOUND_STREAM_ERROR;
                this.streamManager.handleWebRTCError(errorType);
            }
        }, WATCH_DOG_TIMER);
    }

    public clearWatchDog(): void {
        if (this.connectionWatchDog) {
            clearTimeout(this.connectionWatchDog);
            this.connectionWatchDog = null;
            logger.debug('[OUTBOUND_STREAM] Watchdog timer cleared on successful connection.');
        }
    }

    public getVstConfig(): void {
        logger.debug('[OUTBOUND_STREAM]', 'Calling getVstConfig');
        const jsonData = {
            apiKey: getAPIPath(this.streamType).configuration,
            data: null,
            peerId: this.peerId,
        };
        const jsonString = JSON.stringify(jsonData);
        this.streamManager.sendWebSocketMessage(jsonString);
    }

    public onReceiveCandidate(candidates: RTCIceCandidateInit[]): void {
        logger.debug('[OUTBOUND_STREAM]', `Received candidates from VMS: ${JSON.stringify(candidates)}`);
        if (candidates) {
            logger.debug('[OUTBOUND_STREAM]', 'Creating RTCIceCandidate from each received candidate..');
            for (let i = 0; i < candidates.length; i += 1) {
                const candidate = candidates[i];
                logger.debug('[OUTBOUND_STREAM]', `Adding ICE candidate - ${i} :${JSON.stringify(candidate)}`);
                this.peerConnection
                    ?.addIceCandidate(candidate)
                    .then(() => {
                        logger.debug('[OUTBOUND_STREAM]', `addIceCandidate OK - ${i}`);
                    })
                    .catch(error => {
                        logger.debug('[OUTBOUND_STREAM]', `addIceCandidate error - ${i}`, error);
                    });
            }
        }
    }

    public setRemoteDescription(sessionDescriptionAnswer: RTCSessionDescriptionInit): void {
        if (this.peerConnection) {
            this.peerConnection
                .setRemoteDescription(new RTCSessionDescription(sessionDescriptionAnswer))
                .then(() => {
                    this.earlyCandidates.forEach(this.peerConnectionManager.addIceCandidate.bind(this.peerConnectionManager));
                })
                .catch(e => {
                    logger.error('Failed to set remote description', e);
                });
        } else {
            logger.error('Failed to set remote description, no peer connection found');
        }
    }

    public createRTCPeerConnection(iceServerList: IceServer[]): void {
        this.peerConnectionManager.create(iceServerList);
    }

    public getIceServers(): void {
        logger.debug('[OUTBOUND_STREAM]', 'Calling getIceServers', this.peerId);
        const jsonData = {
            apiKey: getAPIPath(this.streamType).iceServers,
            peerId: this.peerId,
            data: { peerId: this.peerId },
        };
        const jsonString = JSON.stringify(jsonData);
        this.streamManager.sendWebSocketMessage(jsonString);
    }

    public setContentHint(stream: MediaStream | null): void {
        if (!stream) return;

        const videoTracks = stream.getVideoTracks();
        const audioTracks = stream.getAudioTracks();

        if (videoTracks.length > 0) {
            logger.debug('[OUTBOUND_STREAM]', `Using video device: ${videoTracks[0].label}`);
            videoTracks.forEach(track => {
                if (this.degradationPreference != null) {
                    logger.debug('[OUTBOUND_STREAM]', 'setting degradation preference: ', this.degradationPreference);
                    track.contentHint = this.degradationPreference === 'resolution' ? 'motion' : 'detail';
                } else {
                    logger.debug('[OUTBOUND_STREAM]', 'setting default degradation preference');
                    track.contentHint = 'detail';
                }
                logger.debug('[OUTBOUND_STREAM]', 'video track: ', track);
            });
        }

        if (audioTracks.length > 0) {
            logger.debug('[OUTBOUND_STREAM]', `Using audio device: ${audioTracks[0].label}`);
            audioTracks.forEach(track => {
                track.contentHint = 'speech';
                logger.debug('[OUTBOUND_STREAM]', 'audio track: ', track);
            });
        }

        this.mediaStream = stream;
    }

    public doCleanup(): void {
        logger.info('[OUTBOUND_STREAM] Cleanup process started.');

        if (this.connectionWatchDog) {
            clearTimeout(this.connectionWatchDog);
            this.connectionWatchDog = null;
        }

        // Stop and clear media streams
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
            this.mediaStream = null;
        }

        // Close peer connection
        if (this.peerConnection) {
            // Detach event handlers to prevent re-triggering cleanup
            this.peerConnection.onicecandidate = null;
            this.peerConnection.oniceconnectionstatechange = null;
            this.peerConnection.onicecandidateerror = null;
            this.peerConnection.onicegatheringstatechange = null;
            this.peerConnection.onsignalingstatechange = null;
            this.peerConnection.onconnectionstatechange = null;

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
        const outboundStreamVideoElementId = this.streamManager.getConfig().outboundStreamVideoElementId;
        if (outboundStreamVideoElementId) {
            const videoElement = document.getElementById(outboundStreamVideoElementId) as HTMLVideoElement | null;
            if (videoElement) {
                videoElement.srcObject = null;
                videoElement.load();
            }
        }

        this.earlyCandidates = [];
        this.isConnected = false;

        logger.info('[OUTBOUND_STREAM] Cleanup completed.');
    }
}
