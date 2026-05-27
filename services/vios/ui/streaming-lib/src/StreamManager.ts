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
import NvWebsocket from './websocket/WebSocket';
import NvWebRTC from './webrtc/WebRTC';
import { generateUUID } from './utils/misc';
import { logger } from './utils/logger';
import getAPIPath, { StreamType } from './utils/apis';
import { StreamConfig, StreamState, RetryConfig, RetryState, StreamRestartInfo } from './utils/interfaces';
import { ErrorType, ErrorTypes } from './utils/error';
import { WebRTCIssue, WebRTCNetworkScores } from './webrtc/WebRTCIssueDetector';

const DEFAULT_INBOUND_VIDEO_ELEMENT_ID = 'tokkio-avatar-stream';
const DEFAULT_WEBSOCKET_ENDPOINT = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
const DEFAULT_WEBSOCKET_RETRY_CONFIG: RetryConfig = {
    maxRetries: 5,
    retryDelayMs: 1000,
    backoffMultiplier: 2,
    maxRetryDelayMs: 8000,
};

/** Define the App config. */
export interface AppConfig {
    /** HTML video element IDs for inbound and outbound streams. */
    inboundStreamVideoElementId: string;
    outboundStreamVideoElementId?: string;
    /**
     * Connection ID for websocket connection. Same ID is used in tokkio use-case
     * for inbound and outbound streams.
     */
    connectionId?: string;

    /** Query parameters for webSocket connection. It should be in format param1=<>&param2=<> */
    queryParams?: string;
    /**
     * Enable websSocket ping functionality. Set webSocket ping interval.
     * A ping message will be sent every few second.
     */
    enableWebsocketPing?: boolean;
    websocketPingInterval?: number;
    /** Set VST webSocket endpoint. It should be format ws(s)://<ip>:<port>/<path> */
    vstWebsocketEndpoint: string;

    /** Enable-disable console logs for library. */
    enableLogs?: boolean;

    /**
     * Enable-Disable camera and microphone. If both are disabled then
     * outbound stream will not be started.
     */
    enableMicrophone?: boolean;
    enableCamera?: boolean;
    /**
     * Websocket timeout. If no message is received within this duration then
     * websocket connection will be closed.
     */
    websocketTimeoutMS?: number;

    streamType: StreamType;

    enableDummyUDPCall: boolean;

    /** WebSocket and WebRTC retry configuration */
    webSocketRetryConfig?: RetryConfig;
    webRtcRetryConfig?: RetryConfig;

    /** Callback functions */
    sendCustomWebsocketMessage?: (msg: string) => boolean;
    firstFrameReceivedCallback?: () => void;
    errorCallback?: (error: ErrorType) => void;
    successCallback?: (inboundPeerId: string, mediaSessionId: string) => void;
    closeCallback?: () => void;
    onPlaybackUpdate?: (ts: number) => void;
    onStreamStatusUpdate?: (status: { error: boolean; state: StreamState }) => void;
    onStreamRestart?: (info: StreamRestartInfo) => void;

    /** WebRTC Issue Detector Callbacks */
    onWebRTCIssueDetected?: (issue: WebRTCIssue) => void;
    onWebRTCNetworkScoresUpdated?: (scores: WebRTCNetworkScores) => void;
}

/** Stream manager class. Inbound and Outbound streams are controlled through this class */
export default class StreamManager {
    private websocket: NvWebsocket | null = null;
    private webrtc: NvWebRTC | null = null;
    private timerInterval: NodeJS.Timeout | null = null;
    private webSocketPingInterval: NodeJS.Timeout | null = null;
    private errorCallbackFired: boolean = false;
    private closeCallbackFired: boolean = false;
    private outboundStreamPeerId: string | null = null;
    private publicIPAddress: string | null = null;
    private inboundStreamPeerId: string | null = null;
    private isInboundConnectionSuccess: boolean = false;
    private isOutboundConnectionSuccess: boolean = false;
    private isProcessing: boolean = false;
    private inboundMediaSessionId: string | null = null;
    private isCleanupInProgress: boolean = false;
    private cleanupPromise: Promise<void> | null = null;
    private isUserInitiatedStop: boolean = false;
    private webSocketRetryState: RetryState = {
        currentRetryCount: 0,
        isRetrying: false,
        lastRetryTime: 0,
        retryTimer: null,
    };

    public streamType: StreamType = StreamType.Streambridge;
    public streamConfig: StreamConfig | null = null;
    private appConfig: AppConfig = StreamManager.getDefaultAppConfig();

    constructor() {
        this.streamConfig = StreamManager.getDefaultStreamConfig();
    }

    /**
     *  Start the streaming. The function starts the webSocket connection. Once the
     * websocket goes into connected state onWebSocketConnected() will be triggered.
     *
     */

    public setMediaSessionId(mediaSessionId: string): void {
        this.inboundMediaSessionId = mediaSessionId;
    }

    public startStreaming(streamConfig: StreamConfig): void {
        logger.info('[STREAM_MANAGER] Start streaming process initiated.');
        // TODO: Read verserion from package.json
        this.streamConfig = streamConfig;
        if (streamConfig) {
            this.streamConfig = {
                ...this.streamConfig,
                ...streamConfig,
            };
        }
        /** Return error if stream ID is not provided and camera-microphone is disabled. */
        if (
            !this.appConfig.enableCamera &&
            !this.appConfig.enableMicrophone &&
            !this.streamConfig.streamId &&
            !this.streamConfig.mainStreamId &&
            this.streamType !== StreamType.VideoWall
        ) {
            const errorType: ErrorType = ErrorTypes.INVALID_PARAMETER_ERROR;
            this.notifyError(errorType);
            return;
        }
        logger.debug('Lib Version: 1.3.0-25.01.3-x86_64');

        /** To handle case where callbacks can be triggered from multiple errors. */
        this.errorCallbackFired = true;
        this.closeCallbackFired = true;

        /** If a request on this instance is already in process then throw error. */
        if (this.isProcessing) {
            logger.error('A request is already in progress');
            const errorType: ErrorType = ErrorTypes.BUSY_ERROR;
            this.notifyError(errorType);
            return;
        }
        this.isProcessing = true;

        logger.debug('new streaming process started');

        /** Try unmuting the video player. */
        this.unmuteVideoPlayer();
        /** Generate connectionId if not provided. */
        this.outboundStreamPeerId = this.appConfig.connectionId || generateUUID();

        /** Always print connection ID for tracking and debug purpose. */
        // eslint-disable-next-line no-console
        console.log('Connection ID: ', this.outboundStreamPeerId);

        /**
         * If both camera and microphone are disabled that means outbound stream
         * will not be instantiated. In that case no need to use different peer ID
         * for inbound peer connection.
         */
        if (this.appConfig.enableCamera || this.appConfig.enableMicrophone) {
            this.inboundStreamPeerId = `${this.outboundStreamPeerId}_1`;
        } else {
            this.inboundStreamPeerId = this.outboundStreamPeerId;
            this.outboundStreamPeerId = null;
        }

        /** Start WebSocket connection and wait for onWebSocketConnected() to trigger. */
        try {
            this.websocket = new NvWebsocket(this, this.outboundStreamPeerId || this.inboundStreamPeerId, this.appConfig.queryParams);
        } catch (error) {
            logger.error('[STREAM_MANAGER] Failed to create WebSocket during startup:', error);
            const errorType: ErrorType = ErrorTypes.WEBSOCKET_ERROR;
            this.notifyError(errorType);
            return;
        }
    }

    /** Stop streaming and do cleanup. */
    public async stopStreaming(): Promise<void> {
        logger.info('[STREAM_MANAGER] Stop streaming called by application.');
        this.isUserInitiatedStop = true;

        // Cancel any pending retries
        this.resetWebSocketRetryState();
        if (this.webrtc) {
            this.webrtc.setUserInitiatedStop(true);
        }

        await this.handleAppCleanup('Cleanup called from stop streaming function in stream manager.');
    }

    private static getDefaultAppConfig(): AppConfig {
        return {
            inboundStreamVideoElementId: DEFAULT_INBOUND_VIDEO_ELEMENT_ID,
            outboundStreamVideoElementId: undefined,
            connectionId: undefined,
            queryParams: '',
            enableWebsocketPing: true,
            websocketPingInterval: 2000,
            vstWebsocketEndpoint: DEFAULT_WEBSOCKET_ENDPOINT,
            enableLogs: true,
            enableMicrophone: true,
            enableCamera: true,
            websocketTimeoutMS: 5000,
            streamType: StreamType.Streambridge,
            enableDummyUDPCall: false,
            webSocketRetryConfig: DEFAULT_WEBSOCKET_RETRY_CONFIG,
            webRtcRetryConfig: {
                maxRetries: 3,
                retryDelayMs: 2000,
                backoffMultiplier: 2,
                maxRetryDelayMs: 10000,
            },
            errorCallback: (): void => {
                // Default implementation does nothing with the error
            },
            successCallback: (): void => {
                // Default implementation does nothing with the parameters
            },
            firstFrameReceivedCallback: () => void {},
        };
    }

    private static getDefaultStreamConfig(): StreamConfig {
        return {
            streamId: undefined,
            startTime: undefined,
            endTime: undefined,
            options: {
                rtptransport: 'udp',
                timeout: 60,
                quality: 'auto',
            },
        };
    }

    public getConfig(): AppConfig {
        return { ...this.appConfig };
    }

    public getStreamConfig(): StreamConfig | null {
        return this.streamConfig;
    }

    public getInboundPeerConnectionObject(): RTCPeerConnection | null {
        if (this.webrtc && this.isInboundConnectionSuccess) {
            return this.webrtc.getInboundPeerConnectionObject();
        }
        return null;
    }

    public getOutboundPeerConnectionObject(): RTCPeerConnection | null {
        if (this.webrtc && this.isOutboundConnectionSuccess) {
            return this.webrtc.getOutboundPeerConnectionObject();
        }
        return null;
    }

    public getInboundStreamPeerId(): string | null {
        return this.inboundStreamPeerId;
    }

    public getOutboundStreamPeerId(): string | null {
        return this.outboundStreamPeerId;
    }

    /** Clients can send any custom message over websocket connection. */
    public sendCustomWebsocketMessage(msg: string): boolean {
        if (this.websocket) {
            this.websocket.sendMessage(msg);
            return true;
        }
        return false;
    }

    /** Toggle microphone if Outbound Stream is connected. */
    public toggleMicrophone(): boolean | undefined {
        if (this.webrtc) {
            return this.webrtc.toggleMicrophone();
        }
        return undefined;
    }

    public setPublicIPAddress(ipAddress: string): void {
        this.publicIPAddress = ipAddress;
    }

    public getPublicIPAddress(): string | null {
        return this.publicIPAddress;
    }

    public getMediaSessionId(): string | null {
        return this.inboundMediaSessionId;
    }

    public getVSTWebSocketEndpoint(): string {
        return this.appConfig.vstWebsocketEndpoint;
    }

    public isWebSocketRetrying(): boolean {
        return this.webSocketRetryState.isRetrying;
    }

    public notifyStreamRestart(info: StreamRestartInfo): void {
        if (this.appConfig.onStreamRestart) {
            try {
                logger.info(`[STREAM_MANAGER] Notifying client of ${info.type} restart: ${info.reason}`);
                this.appConfig.onStreamRestart(info);
            } catch (error) {
                logger.error('[STREAM_MANAGER] Error in stream restart callback:', error);
            }
        }
    }

    private unmuteVideoPlayer(): void {
        const videoElement = document.getElementById(this.appConfig.inboundStreamVideoElementId || '');
        if (videoElement) {
            logger.debug('Unmute success');
            (videoElement as HTMLVideoElement).muted = false;
        } else {
            logger.error('Failed to unmute, video element not found');
        }
    }

    public updateConfig(newConfig: Partial<AppConfig>): void {
        this.appConfig = {
            ...this.appConfig,
            ...newConfig,
        };
        this.streamType = this.appConfig.streamType;

        // Update WebRTC retry config if provided
        if (this.webrtc && newConfig.webRtcRetryConfig) {
            this.webrtc.updateRetryConfig(newConfig.webRtcRetryConfig);
        }

        logger.debug('Updated config: ', this.appConfig);
    }

    /**
     * Do something after inbound stream is connected like
     * starting the outbound stream.
     */
    public onInboundStreamConnection(): void {
        logger.info('[STREAM_MANAGER] Inbound stream connected.');
        if (this.appConfig.enableDummyUDPCall) {
            const dummyJson = {
                peerid: this.outboundStreamPeerId,
                apiKey: 'addDummyUdpTrack',
            };
            logger.debug('Sending Dummy UDP call to VST');
            this.websocket?.sendMessage(JSON.stringify(dummyJson));
        }
    }

    /**
     * Websocket connect callback. Start WebRTC streaming after
     * websocket has been connected.
     */
    public onWebSocketConnected(): void {
        logger.info('[STREAM_MANAGER] WebSocket connection established. Initializing WebRTC.');
        if (this.inboundStreamPeerId) {
            // Check if this is a restart (retry count > 0) before resetting
            const wasRetrying = this.webSocketRetryState.currentRetryCount > 0;
            const retryAttempt = this.webSocketRetryState.currentRetryCount;
            const lastRetryTime = this.webSocketRetryState.lastRetryTime;

            // Reset WebSocket retry state on successful connection
            this.resetWebSocketRetryState();

            this.webrtc = new NvWebRTC(this, this.inboundStreamPeerId as string, this.outboundStreamPeerId as string);

            // Update WebRTC retry config if specified
            if (this.appConfig.webRtcRetryConfig) {
                this.webrtc.updateRetryConfig(this.appConfig.webRtcRetryConfig);
            }

            if (this.appConfig.enableWebsocketPing) {
                this.startWebSocketPing();
            }

            // Notify client of WebSocket restart if this was a retry
            if (wasRetrying) {
                const restartInfo: StreamRestartInfo = {
                    type: 'websocket',
                    reason: 'WebSocket connection restored after failure',
                    retryAttempt,
                    timestamp: Date.now(),
                    previousFailureTime: lastRetryTime,
                };
                this.notifyStreamRestart(restartInfo);
            }

            logger.info('[STREAM_MANAGER] WebRTC initialized with fresh retry state after WebSocket connection');
        } else {
            logger.error('Outbound peer ID not generated');
        }
    }

    private startWebSocketPing(): void {
        this.webSocketPingInterval = setInterval(() => {
            const jsonData = {
                apiKey: getAPIPath(this.streamType).ping,
            };
            this.websocket?.sendMessage(JSON.stringify(jsonData));
        }, this.appConfig.websocketPingInterval);
    }

    private async notifyError(error: ErrorType): Promise<void> {
        if (this.isCleanupInProgress) {
            logger.debug('notifyError called during cleanup, ignoring.');
            return;
        }
        if (this.errorCallbackFired) {
            if (this.appConfig.errorCallback) {
                logger.warn('[STREAM_MANAGER] Firing error callback.');
                this.appConfig.errorCallback(error);
            }
        }
        await this.handleAppCleanup('Cleanup called from notify error function');
    }

    private notifySuccess(): void {
        if (this.appConfig.successCallback) {
            logger.info('[STREAM_MANAGER] Streaming session successfully established.');
            this.appConfig.successCallback(this.inboundStreamPeerId || '', this.inboundMediaSessionId || '');
        }
    }

    public handleWebSocketMessage(message: string): void {
        if (this.webrtc) {
            this.webrtc.handleWebSocketMessage(message);
        }
    }

    public sendWebSocketMessage(message: string): void {
        if (this.websocket) {
            this.websocket.sendMessage(message);
        }
    }

    public handleWebRTCError(error: ErrorType): void {
        this.notifyError(error);
    }

    public handleWebSocketError(error: ErrorType): void {
        if (this.isUserInitiatedStop) {
            logger.info('[STREAM_MANAGER] User initiated stop, not retrying WebSocket');
            this.notifyError(error);
            return;
        }

        // Reset WebRTC retry states since WebSocket failure requires full restart
        if (this.webrtc) {
            this.webrtc.resetRetryStates();
        }

        // Attempt WebSocket retry
        this.attemptWebSocketRetry(error);
    }

    /**
     * If outbound stream is arleady connected OR outbound stream is disabled
     * then call the notifySuccess callback
     */
    public setInboundStreamConnectionStatus(status: boolean): void {
        this.isInboundConnectionSuccess = status;
        if (this.isOutboundConnectionSuccess || (!this.appConfig.enableCamera && !this.appConfig.enableMicrophone)) {
            this.notifySuccess();
        }
    }

    /** If inbound stream is already connected then call notifySuccess callback */
    public setOutboundStreamConnectionStatus(status: boolean): void {
        this.isOutboundConnectionSuccess = status;
        if (this.isInboundConnectionSuccess) {
            this.notifySuccess();
        }
    }

    public async handleAppCleanup(errorReason: string = 'default'): Promise<void> {
        // If cleanup is already in progress, return the existing promise
        if (this.cleanupPromise) {
            logger.debug('Cleanup already in progress, waiting for completion');
            return this.cleanupPromise;
        }

        // If cleanup was already completed, don't run again
        if (this.isCleanupInProgress) {
            logger.debug('Cleanup already completed, skipping');
            return;
        }

        // Create and store the cleanup promise to prevent race conditions
        this.cleanupPromise = this.performCleanup(errorReason);

        try {
            await this.cleanupPromise;
        } finally {
            // Reset the promise so future cleanups can proceed if needed
            this.cleanupPromise = null;
        }
    }

    private async performCleanup(errorReason: string): Promise<void> {
        this.isCleanupInProgress = true;

        logger.info(`[STREAM_MANAGER] Cleanup process started. Reason: ${errorReason}`);

        const closeCallback = this.closeCallbackFired && this.appConfig.closeCallback;

        try {
            /** Clear intervals and timers first */
            if (this.timerInterval) {
                clearInterval(this.timerInterval);
                this.timerInterval = null;
            }
            if (this.webSocketPingInterval) {
                clearInterval(this.webSocketPingInterval);
                this.webSocketPingInterval = null;
            }

            // Reset retry states
            this.resetWebSocketRetryState();

            /** Clear webRTC connection before websocket */
            if (this.webrtc) {
                this.webrtc.doCleanup();
                this.webrtc = null;
            }

            /** Close websocket connection */
            if (this.websocket) {
                await this.websocket.close();
                this.websocket = null;
            }

            /** Reset States */
            this.isInboundConnectionSuccess = false;
            this.isOutboundConnectionSuccess = false;
            this.outboundStreamPeerId = null;
            this.inboundStreamPeerId = null;
            this.publicIPAddress = null;
            this.isProcessing = false;
            this.errorCallbackFired = false;
            this.closeCallbackFired = false;
            this.isUserInitiatedStop = false;

            logger.info('[STREAM_MANAGER] Cleanup completed successfully.');
        } catch (error) {
            logger.error('Error during cleanup:', error);
        } finally {
            this.isCleanupInProgress = false;
            logger.debug('listening for new processes');
        }

        /** Call close callback */
        if (closeCallback) {
            logger.info('[STREAM_MANAGER] Firing close callback.');
            closeCallback();
        }
    }

    private resetWebSocketRetryState(): void {
        if (this.webSocketRetryState.retryTimer) {
            clearTimeout(this.webSocketRetryState.retryTimer);
            this.webSocketRetryState.retryTimer = null;
        }
        this.webSocketRetryState.currentRetryCount = 0;
        this.webSocketRetryState.isRetrying = false;
        this.webSocketRetryState.lastRetryTime = 0;
    }

    private calculateWebSocketRetryDelay(): number {
        const config = this.appConfig.webSocketRetryConfig || DEFAULT_WEBSOCKET_RETRY_CONFIG;
        const baseDelay = config.retryDelayMs;
        const multiplier = Math.pow(config.backoffMultiplier, this.webSocketRetryState.currentRetryCount);
        const calculatedDelay = baseDelay * multiplier;
        return Math.min(calculatedDelay, config.maxRetryDelayMs);
    }

    private async attemptWebSocketRetry(originalError: ErrorType): Promise<void> {
        const config = this.appConfig.webSocketRetryConfig || DEFAULT_WEBSOCKET_RETRY_CONFIG;

        if (this.webSocketRetryState.currentRetryCount >= config.maxRetries) {
            logger.error('[STREAM_MANAGER] Max WebSocket retry attempts reached');
            this.resetWebSocketRetryState();
            this.notifyError(originalError);
            return;
        }

        if (this.webSocketRetryState.isRetrying) {
            logger.debug('[STREAM_MANAGER] WebSocket retry already in progress');
            return;
        }

        this.webSocketRetryState.isRetrying = true;
        this.webSocketRetryState.currentRetryCount++;
        this.webSocketRetryState.lastRetryTime = Date.now();

        const retryDelay = this.calculateWebSocketRetryDelay();
        logger.info(
            `[STREAM_MANAGER] Attempting WebSocket retry ${this.webSocketRetryState.currentRetryCount}/${config.maxRetries} after ${retryDelay}ms`
        );

        this.webSocketRetryState.retryTimer = setTimeout(async () => {
            try {
                await this.performWebSocketRetry();
            } catch (error) {
                logger.error('[STREAM_MANAGER] WebSocket retry failed:', error);
                this.webSocketRetryState.isRetrying = false;

                // Try again if we haven't reached max retries
                if (this.webSocketRetryState.currentRetryCount < config.maxRetries) {
                    await this.attemptWebSocketRetry(originalError);
                } else {
                    this.notifyError(originalError);
                }
            }
        }, retryDelay);
    }

    private async performWebSocketRetry(): Promise<void> {
        logger.info('[STREAM_MANAGER] Performing WebSocket retry...');

        // Track if WebRTC was previously connected for better logging
        const wasWebRtcConnected = this.isInboundConnectionSuccess || this.isOutboundConnectionSuccess;

        // Comprehensive cleanup for WebSocket retry
        await this.cleanupForWebSocketRetry();

        // Create new WebSocket connection
        const connectionId = this.outboundStreamPeerId || this.inboundStreamPeerId;
        if (!connectionId) {
            throw new Error('No connection ID available for WebSocket retry');
        }

        try {
            this.websocket = new NvWebsocket(this, connectionId, this.appConfig.queryParams);
            // Note: WebRTC will be re-initialized in onWebSocketConnected() callback
        } catch (error) {
            logger.error('[STREAM_MANAGER] Failed to create WebSocket during retry:', error);
            // Let the retry mechanism handle this failure
            throw error;
        }

        this.webSocketRetryState.isRetrying = false;
        const action = wasWebRtcConnected ? 'restarted' : 'initialized';
        logger.info(`[STREAM_MANAGER] WebSocket retry attempt completed - WebRTC will be ${action} on connection`);
    }

    private async cleanupForWebSocketRetry(): Promise<void> {
        logger.debug('[STREAM_MANAGER] Performing comprehensive cleanup for WebSocket retry...');

        // Clear all intervals and timers
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
            logger.debug('[STREAM_MANAGER] Timer interval cleared');
        }

        if (this.webSocketPingInterval) {
            clearInterval(this.webSocketPingInterval);
            this.webSocketPingInterval = null;
            logger.debug('[STREAM_MANAGER] WebSocket ping interval cleared');
        }

        // Clean up WebRTC connections (includes all timers and resources)
        if (this.webrtc) {
            logger.debug('[STREAM_MANAGER] Cleaning up WebRTC connections due to WebSocket failure');
            this.webrtc.doCleanup();
            this.webrtc = null;
        }

        // Close WebSocket connection
        if (this.websocket) {
            await this.websocket.close();
            this.websocket = null;
            logger.debug('[STREAM_MANAGER] WebSocket connection closed');
        }

        // Reset all connection states and cached data
        this.isInboundConnectionSuccess = false;
        this.isOutboundConnectionSuccess = false;
        this.inboundMediaSessionId = null;
        this.publicIPAddress = null;

        logger.debug('[STREAM_MANAGER] Comprehensive cleanup for WebSocket retry completed');
    }
}
