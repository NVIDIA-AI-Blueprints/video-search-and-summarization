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

import { logger } from '../../utils/logger';
import getAPIPath, { StreamType } from '../../utils/apis';
import InboundStream from './InboundStream';
import { ErrorType } from '../../utils/error';
import {
    StreamQueryResponse,
    StreamStatusResponse,
    VstMessage,
    SdpOfferPayload,
    IceServersPayload,
    IceCandidatePayload,
    WebRTCConfig,
} from '../../utils/interfaces';

export class InboundSignaling {
    private stream: InboundStream;

    constructor(stream: InboundStream) {
        this.stream = stream;
    }

    public async handleWebSocketMessage(msg: string): Promise<void> {
        try {
            const jsonData = JSON.parse(msg) as VstMessage;
            if (typeof jsonData === 'object' && Object.prototype.hasOwnProperty.call(jsonData, 'apiKey')) {
                switch (jsonData.apiKey) {
                    case getAPIPath(this.stream.streamType).ping:
                        // ignore the ping message
                        break;
                    case getAPIPath(this.stream.streamType).startStream:
                        this.handleSdpOfferMessage(jsonData);
                        break;
                    case getAPIPath(this.stream.streamType).setAnswer:
                        this.handleSdpAnswerMessage(jsonData);
                        break;
                    case getAPIPath(this.stream.streamType).iceCandidate:
                        this.handleIceCandidateMessage(jsonData);
                        break;
                    case getAPIPath(this.stream.streamType).iceServers:
                        await this.handleIceServersMessage(jsonData);
                        break;
                    case getAPIPath(this.stream.streamType).configuration:
                        this.handleConfigurationMessage(jsonData);
                        break;
                    case getAPIPath(this.stream.streamType).streamQuery:
                        this.handleStreamQueryMessage(jsonData);
                        break;
                    case getAPIPath(this.stream.streamType).streamStatus:
                        this.handleStreamStatusMessage(jsonData);
                        break;
                    default:
                        logger.error('Unknown apiKey:', jsonData.apiKey);
                }
            }
        } catch (error) {
            logger.error('Failed to handle WebSocket message', error);
        }
    }

    private handleSdpOfferMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[INBOUND_STREAM]', 'Received SDP offer for Inbound connection');
            this.stream.handleSDPOffer(jsonData.data as SdpOfferPayload);
        }
    }

    private handleSdpAnswerMessage(jsonData: VstMessage): void {
        const data = jsonData.data as Record<string, unknown>;
        // Unreal Engine Case
        if (data.wait_for_offer) {
            logger.info('[INBOUND_STREAM] VST is waiting for offer. Re-initializing connection.');
            // perform webrtcCleanup of old connections if exist
            logger.debug('Cleanup inbound connections started');
            if (this.stream.peerConnection) {
                this.stream.peerConnection.close();
                this.stream.peerConnection = null;
            }
            this.stream.earlyCandidates = [];
            logger.debug('Cleanup inbound connections success, return');
            return;
        }
        if (jsonData.peerId === this.stream.peerId) {
            // Check for error response
            if (
                data.error_code &&
                data.error_message &&
                (this.stream.streamType === StreamType.Live ||
                    this.stream.streamType === StreamType.Replay ||
                    this.stream.streamType === StreamType.VideoWall)
            ) {
                logger.error('[INBOUND_STREAM] Received error response:', jsonData.data);
                // Create custom error with server's error message
                const errorType: ErrorType = {
                    code: this.stream.generateErrorCode(data.error_message as string),
                    message: data.error_message as string,
                };
                this.stream.streamManager.handleWebRTCError(errorType);
                return;
            }

            // this is response of stream/start call
            logger.debug('[INBOUND_STREAM]', 'Received SDP answer for Inbound connection');
            this.stream.addStreamDataListener();
            this.stream.updateMediaSessionId(jsonData.data as { mediaSessionId?: string });
            this.stream.setRemoteDescription(jsonData.data as RTCSessionDescriptionInit);
        }
    }

    private handleIceCandidateMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[INBOUND_STREAM]', 'Received ICE candidates for Inbound connection');
            if (this.stream.isSDPAnswerProcessed) {
                this.stream.processReceivedICECandidate(jsonData.data as IceCandidatePayload);
            } else {
                const candidates = jsonData.data as IceCandidatePayload;
                if (candidates && candidates.length > 0) {
                    logger.debug('[INBOUND_STREAM]', 'SDP Answer not received yet, caching VST candidate');
                    const newCandidates = candidates.map(c => new RTCIceCandidate(c));
                    this.stream.earlyCandidatesFromServer.push(...newCandidates);
                    logger.debug('[INBOUND_STREAM]', 'Cached VST candidates so far: ', this.stream.earlyCandidatesFromServer);
                } else {
                    logger.error('[INBOUND_STREAM]', 'Unable to cache, ICE Caniddates received in invalid format');
                }
            }
        }
    }

    private async handleIceServersMessage(jsonData: VstMessage): Promise<void> {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[INBOUND_STREAM]', 'Received ICE servers for Inbound connection');
            const iceServersData = jsonData.data as IceServersPayload;
            if (iceServersData.iceServers) {
                logger.debug('[INBOUND_STREAM]', 'ICE servers list: ', iceServersData.iceServers);
                this.stream.iceServers = iceServersData.iceServers;
                logger.debug('[INBOUND_STREAM]', 'Getting public IP address ');
                await this.stream.getPublicAddress(iceServersData);
                this.stream.createRTCPeerConnection(iceServersData.iceServers);
            }
        }
    }

    private handleConfigurationMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[INBOUND_STREAM]', 'Received configuration for Inbound connection');
            const { bitrate_min, bitrate_max, bitrate_start } = this.stream.getBitrateSettings(jsonData.data as WebRTCConfig);
            logger.debug('[INBOUND_STREAM]', 'bitrate min: ', bitrate_min);
            logger.debug('[INBOUND_STREAM]', 'bitrate max: ', bitrate_max);
            logger.debug('[INBOUND_STREAM]', 'bitrate start: ', bitrate_start);
            this.stream.minBitrate = bitrate_min;
            this.stream.maxBitrate = bitrate_max;
            this.stream.startBitrate = bitrate_start;
            this.stream.getIceServers();
        }
    }

    private handleStreamQueryMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            const statusResponse = jsonData.data as StreamQueryResponse;

            if (this.stream.streamManager.getConfig().onPlaybackUpdate) {
                this.stream.streamManager.getConfig().onPlaybackUpdate!(statusResponse.ts);
            }
        }
    }

    private handleStreamStatusMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            const statusResponse = jsonData.data as StreamStatusResponse;
            if (this.stream.streamManager.getConfig().onStreamStatusUpdate) {
                this.stream.streamManager.getConfig().onStreamStatusUpdate!({
                    error: statusResponse.error,
                    state: statusResponse.state,
                });
            }
        }
    }
}
