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
import getAPIPath from '../../utils/apis';
import OutboundStream from './OutboundStream';
import { getPublicIPAddress } from '../../utils/trickleICE';
import { VstMessage, IceServersPayload, IceCandidatePayload } from '../../utils/interfaces';

interface OutboundConfigPayload {
    webrtcInVideoDegradationPreference: 'maintain-resolution' | 'maintain-framerate' | 'balanced' | 'disabled';
}

export class OutboundSignaling {
    private stream: OutboundStream;

    constructor(stream: OutboundStream) {
        this.stream = stream;
    }

    public async handleWebSocketMessage(msg: string): Promise<void> {
        try {
            const jsonData = JSON.parse(msg) as VstMessage;
            if (typeof jsonData === 'object' && Object.prototype.hasOwnProperty.call(jsonData, 'apiKey')) {
                switch (jsonData.apiKey) {
                    case getAPIPath(this.stream.streamType).streamStatus:
                        break;
                    case getAPIPath(this.stream.streamType).streamQuery:
                        break;
                    case getAPIPath(this.stream.streamType).ping:
                        // ignore the ping message
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
                    default:
                        logger.error('Unknown apiKey:', jsonData.apiKey);
                }
            }
        } catch (error) {
            logger.error('Failed to handle WebSocket message', error);
        }
    }

    private handleSdpAnswerMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[OUTBOUND_STREAM]', 'Received SDP offer for Outbound connection');
            this.stream.setRemoteDescription(jsonData.data as RTCSessionDescriptionInit);
        }
    }

    private handleIceCandidateMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[OUTBOUND_STREAM]', 'Received ICE candidates for Outbound connection');
            this.stream.onReceiveCandidate(jsonData.data as IceCandidatePayload);
        }
    }

    private async handleIceServersMessage(jsonData: VstMessage): Promise<void> {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[OUTBOUND_STREAM]', 'Received ICE servers for Outbound connection');
            const iceServersData = jsonData.data as IceServersPayload;
            if (iceServersData.iceServers) {
                logger.debug('[OUTBOUND_STREAM]', 'ICE servers list: ', iceServersData.iceServers);
                logger.debug('[OUTBOUND_STREAM]', 'Getting public IP address');
                this.stream.publicIPAddress = await getPublicIPAddress(iceServersData);
                logger.debug('[OUTBOUND_STREAM]', 'Public IP address', this.stream.publicIPAddress);
                this.stream.iceServers = iceServersData.iceServers;
                this.stream.createRTCPeerConnection(iceServersData.iceServers);
            }
        }
    }

    private handleConfigurationMessage(jsonData: VstMessage): void {
        if (jsonData.peerId === this.stream.peerId) {
            logger.debug('[OUTBOUND_STREAM]', 'Received configuration for Outbound connection');
            const data = jsonData.data as OutboundConfigPayload;
            if (data.webrtcInVideoDegradationPreference) {
                this.stream.degradationPreference = data.webrtcInVideoDegradationPreference;
                logger.debug('[OUTBOUND_STREAM]', 'degradationPreference', this.stream.degradationPreference);
                this.stream.setContentHint(this.stream.mediaStream);
                this.stream.getIceServers();
            }
        }
    }
}
