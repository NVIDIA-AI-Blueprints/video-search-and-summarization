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

import { logger } from './logger';
import { IceServer } from './interfaces';

interface IceServerConfig {
    iceServers: IceServer[];
}

export const getPublicIPAddress = async (iceServerList: IceServerConfig): Promise<string | null> => {
    if (iceServerList) {
        const iceServerObj = iceServerList;
        const { iceServers } = iceServerObj;

        for (const server of iceServers) {
            let url: string | null = null;
            let credential: string | undefined;
            let username: string | undefined;

            if (server.urls && server.urls.length > 0) {
                logger.info('[TRICKLE_ICE]', 'URL:', server.urls[0]);
                url = server.urls[0];
            }
            if (server.credential) {
                logger.info('[TRICKLE_ICE]', 'Credential:', server.credential);
                credential = server.credential;
            }
            if (server.username) {
                logger.info('[TRICKLE_ICE]', 'Username:', server.username);
                username = server.username;
            }

            const ipAddress = await trickleICE(url, credential, username);
            if (ipAddress !== null) {
                return ipAddress;
            }
        }
    } else {
        throw new Error('No data received from the server');
    }
    return null;
};

const trickleICE = async (url: string | null, credential?: string, username?: string): Promise<string | null> => {
    if (!url) {
        logger.info('[TRICKLE_ICE]', `No valid TURN or STUN URL found, prefix url with either stun, turn, or turns`);
        return null;
    }
    const isStun = url.includes('stun');
    const isTurn = url.includes('turn');
    let iceServers: RTCIceServer[];

    if (isStun) {
        iceServers = [
            {
                urls: url,
            },
        ];
    } else if (isTurn) {
        iceServers = [
            {
                urls: url,
                username,
                credential,
            },
        ];
    } else {
        logger.info('[TRICKLE_ICE]', 'No valid STUN or TURN server found');
        return null;
    }

    logger.info('[TRICKLE_ICE]', iceServers);
    let pc: RTCPeerConnection | null = null;
    try {
        pc = new RTCPeerConnection({
            iceServers,
        });
    } catch (error) {
        logger.info('[TRICKLE_ICE]', error);
        return null;
    }

    pc.createDataChannel('random-data');
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    return new Promise(resolve => {
        const timeoutMs = 10000; // Set a timeout of 10 seconds
        const timeoutId = setTimeout(() => {
            logger.info('[TRICKLE_ICE]', 'Timed out waiting for ICE candidate');
            resolve(null);
            logger.info('[TRICKLE_ICE]', 'Closing peer connection');
            pc?.close();
        }, timeoutMs);

        pc.onicecandidate = (e: RTCPeerConnectionIceEvent): void => {
            logger.info('[TRICKLE_ICE]', 'candidate: ', e.candidate);
            if (!e.candidate) {
                logger.info('[TRICKLE_ICE]', 'Received null candidate');
                resolve(null);
                clearTimeout(timeoutId);
                logger.info('[TRICKLE_ICE]', 'Closing peer connection');
                pc?.close();
            } else if (e.candidate.type === 'srflx') {
                logger.info('[TRICKLE_ICE]', 'The STUN server is reachable!');
                logger.info('[TRICKLE_ICE]', `Public IP Address from trickle ICE: ${e.candidate.address}`);
                resolve(e.candidate.address);
                clearTimeout(timeoutId);
                logger.info('[TRICKLE_ICE]', 'Closing peer connection');
                pc?.close();
            }
        };
        pc.onicecandidateerror = (e: RTCPeerConnectionIceErrorEvent): void => {
            logger.error(e);
            resolve(null);
            clearTimeout(timeoutId);
            logger.info('[TRICKLE_ICE]', 'Closing peer connection');
            pc?.close();
        };
    });
};
