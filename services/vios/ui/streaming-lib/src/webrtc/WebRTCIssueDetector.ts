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

import WebRTCIssueDetector from 'webrtc-issue-detector';
import { logger } from '../utils/logger';

export interface WebRTCIssue {
    type: 'network' | 'cpu' | 'server' | 'stream';
    reason: string;
    statsSample: Record<string, unknown>;
    ssrc?: number;
    iceCandidate?: string;
    trackIdentifier?: string;
    timestamp: number;
    streamDirection: 'inbound' | 'outbound';
}

export interface WebRTCNetworkScores {
    inbound: number;
    outbound: number;
    statsSamples: Record<string, unknown>;
    timestamp: number;
}

export interface WebRTCIssueDetectorCallbacks {
    onIssueDetected?: (issue: WebRTCIssue) => void;
    onNetworkScoresUpdated?: (scores: WebRTCNetworkScores) => void;
}

// Interface for raw issues from the detector library
interface RawWebRTCIssue {
    type: 'network' | 'cpu' | 'server' | 'stream';
    reason: string;
    statsSample?: Record<string, unknown>;
    ssrc?: number;
    iceCandidate?: string;
    trackIdentifier?: string;
}

// Interface for raw network scores from the detector library
interface RawNetworkScores {
    inbound?: number;
    outbound?: number;
    statsSamples?: Record<string, unknown>;
}

export class VSTWebRTCIssueDetector {
    private detector: WebRTCIssueDetector | null = null;
    private callbacks: WebRTCIssueDetectorCallbacks;
    private streamDirection: 'inbound' | 'outbound';
    private isActive: boolean = false;

    constructor(streamDirection: 'inbound' | 'outbound', callbacks?: WebRTCIssueDetectorCallbacks) {
        this.streamDirection = streamDirection;
        this.callbacks = callbacks || {};
        this.initializeDetector();
    }

    private initializeDetector(): void {
        try {
            this.detector = new WebRTCIssueDetector({
                onIssues: (issues: RawWebRTCIssue[]): void => {
                    this.handleIssues(issues);
                },
                onNetworkScoresUpdated: (scores: RawNetworkScores): void => {
                    this.handleNetworkScores(scores);
                },
                getStatsInterval: 15000, // Check every 15 seconds
            });

            logger.info(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} detector initialized`);
        } catch (error) {
            logger.error(`[WEBRTC_ISSUE_DETECTOR] Failed to initialize ${this.streamDirection} detector:`, error);
        }
    }

    private handleIssues(issues: RawWebRTCIssue[]): void {
        issues.forEach((issue: RawWebRTCIssue): void => {
            const formattedIssue: WebRTCIssue = {
                type: issue.type,
                reason: issue.reason,
                statsSample: issue.statsSample || {},
                ssrc: issue.ssrc,
                iceCandidate: issue.iceCandidate,
                trackIdentifier: issue.trackIdentifier,
                timestamp: Date.now(),
                streamDirection: this.streamDirection,
            };

            // Log the issue
            logger.info(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Issue detected:`, {
                type: formattedIssue.type,
                reason: formattedIssue.reason,
                statsSample: formattedIssue.statsSample,
                ssrc: formattedIssue.ssrc || 'N/A',
                iceCandidate: formattedIssue.iceCandidate || 'N/A',
                trackIdentifier: formattedIssue.trackIdentifier || 'N/A',
            });

            // Print detailed information based on issue type
            if (formattedIssue.type === 'network') {
                logger.info(
                    `[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Network Issue: ${formattedIssue.reason}`,
                    formattedIssue.statsSample
                );
            } else if (formattedIssue.type === 'cpu') {
                logger.info(
                    `[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} CPU Issue: ${formattedIssue.reason}`,
                    formattedIssue.statsSample
                );
            } else if (formattedIssue.type === 'server') {
                logger.info(
                    `[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Server Issue: ${formattedIssue.reason}`,
                    formattedIssue.statsSample
                );
            } else if (formattedIssue.type === 'stream') {
                logger.info(
                    `[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Stream Issue: ${formattedIssue.reason}`,
                    formattedIssue.statsSample
                );
            }

            // Call client callback if provided
            if (this.callbacks.onIssueDetected) {
                try {
                    this.callbacks.onIssueDetected(formattedIssue);
                } catch (error) {
                    logger.error(`[WEBRTC_ISSUE_DETECTOR] Error in client issue callback:`, error);
                }
            }
        });
    }

    private handleNetworkScores(scores: RawNetworkScores): void {
        const formattedScores: WebRTCNetworkScores = {
            inbound: scores.inbound || 0,
            outbound: scores.outbound || 0,
            statsSamples: scores.statsSamples || {},
            timestamp: Date.now(),
        };

        logger.debug(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Network scores updated:`, {
            inboundScore: formattedScores.inbound,
            outboundScore: formattedScores.outbound,
            statsSamples: formattedScores.statsSamples,
        });

        logger.debug(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Inbound network score: ${formattedScores.inbound}`);
        logger.debug(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Outbound network score: ${formattedScores.outbound}`);
        logger.debug(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} Network stats samples:`, formattedScores.statsSamples);

        // Call client callback if provided
        if (this.callbacks.onNetworkScoresUpdated) {
            try {
                this.callbacks.onNetworkScoresUpdated(formattedScores);
            } catch (error) {
                logger.error(`[WEBRTC_ISSUE_DETECTOR] Error in client network scores callback:`, error);
            }
        }
    }

    public start(): void {
        if (this.detector && !this.isActive) {
            try {
                this.detector.watchNewPeerConnections();
                this.isActive = true;
                logger.info(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} detector started`);
                logger.info(
                    `[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} issue detection started - watching for WebRTC issues...`
                );
            } catch (error) {
                logger.error(`[WEBRTC_ISSUE_DETECTOR] Failed to start ${this.streamDirection} detector:`, error);
            }
        }
    }

    public stop(): void {
        if (this.detector && this.isActive) {
            try {
                this.detector.stopWatchingNewPeerConnections();
                this.isActive = false;
                logger.info(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} detector stopped`);
                logger.info(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} issue detection stopped`);
            } catch (error) {
                logger.error(`[WEBRTC_ISSUE_DETECTOR] Failed to stop ${this.streamDirection} detector:`, error);
            }
        }
    }

    public updateCallbacks(callbacks: WebRTCIssueDetectorCallbacks): void {
        this.callbacks = { ...this.callbacks, ...callbacks };
        logger.debug(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} callbacks updated`);
    }

    public isRunning(): boolean {
        return this.isActive;
    }

    public cleanup(): void {
        this.stop();
        this.detector = null;
        logger.debug(`[WEBRTC_ISSUE_DETECTOR] ${this.streamDirection.toUpperCase()} detector cleaned up`);
    }
}
