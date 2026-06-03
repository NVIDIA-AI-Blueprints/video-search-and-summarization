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

import { ErrorType, ErrorTypes } from '../utils/error';
import { logger } from '../utils/logger';
import StreamManager from '../StreamManager';
import { StreamType } from '../utils/apis';

export default class NvWebSocket {
    private connectionId: string;
    private socket: WebSocket;
    private messageReceived: boolean;
    private messageReceivedTimeout: NodeJS.Timeout | null;
    private streamManager: StreamManager;

    constructor(streamManager: StreamManager, connectionId: string, queryParams = '') {
        this.streamManager = streamManager;
        this.connectionId = connectionId;

        const wsEndpoint = this.streamManager.getVSTWebSocketEndpoint();
        logger.debug('ws endpoint: ', wsEndpoint);
        const websocketEndpoint = new URL(wsEndpoint);

        // Set the appropriate path based on stream type
        const streamType = this.streamManager.streamType;
        let apiPath = '';
        if (streamType === StreamType.Live || streamType === StreamType.VideoWall) {
            apiPath = '/api/v1/live/ws';
        } else if (streamType === StreamType.Replay) {
            apiPath = '/api/v1/replay/ws';
        } else if (streamType === StreamType.Streambridge) {
            apiPath = '/api/v1/streambridge/ws';
        }

        // Combine the existing path with API path, handling trailing slashes
        const existingPath = websocketEndpoint.pathname.replace(/\/$/, '');
        websocketEndpoint.pathname = `${existingPath}${apiPath}`;
        websocketEndpoint.searchParams.set('connectionId', this.connectionId);

        // Add streamId as query parameter based on stream type
        const streamConfig = this.streamManager.getStreamConfig();

        if (streamConfig) {
            if (
                streamType === StreamType.VideoWall &&
                streamConfig.options?.composite?.streamIds &&
                streamConfig.options.composite.streamIds.length > 0
            ) {
                // For VideoWall, randomly select one streamId from composite configuration
                const randomIndex = Math.floor(Math.random() * streamConfig.options.composite.streamIds.length);
                websocketEndpoint.searchParams.set('streamId', streamConfig.options.composite.streamIds[randomIndex]);
                logger.debug('USing composite streamId: ', streamConfig.options.composite.streamIds[randomIndex]);
            } else if (streamConfig.mainStreamId) {
                websocketEndpoint.searchParams.set('streamId', streamConfig.mainStreamId);
                logger.debug('USing main Stream ID: ', streamConfig.mainStreamId);
            } else if (streamConfig.streamId) {
                // For Live, Replay, and Streambridge, use the single streamId
                websocketEndpoint.searchParams.set('streamId', streamConfig.streamId);
                logger.debug('USing single streamId: ', streamConfig.streamId);
            }
        }

        if (queryParams) {
            websocketEndpoint.search += `&${queryParams}`;
        }
        const finalEndpoint = websocketEndpoint.toString();
        logger.debug('ws endpoint with query params: ', finalEndpoint);

        this.messageReceived = false;
        this.messageReceivedTimeout = null;

        logger.info('[WEBSOCKET] Attempting to connect...');

        try {
            this.socket = new WebSocket(finalEndpoint);

            this.socket.onopen = this.onOpen.bind(this);
            this.socket.onclose = this.onClose.bind(this);
            this.socket.onerror = this.onError.bind(this);
            this.socket.onmessage = this.onMessage.bind(this);
        } catch (error) {
            logger.error('[WEBSOCKET] Failed to create WebSocket connection:', error);
            // Create a mock socket to prevent null reference errors
            this.socket = {
                readyState: WebSocket.CLOSED,
                close: () => {},
                send: () => {},
                addEventListener: () => {},
                removeEventListener: () => {},
                dispatchEvent: () => false,
                onopen: null,
                onclose: null,
                onerror: null,
                onmessage: null,
                binaryType: 'blob',
                bufferedAmount: 0,
                extensions: '',
                protocol: '',
                url: finalEndpoint,
                CONNECTING: WebSocket.CONNECTING,
                OPEN: WebSocket.OPEN,
                CLOSING: WebSocket.CLOSING,
                CLOSED: WebSocket.CLOSED,
            } as WebSocket;

            // Trigger retry mechanism for constructor failures
            setTimeout(() => {
                const errorType: ErrorType = ErrorTypes.WEBSOCKET_ERROR;
                this.streamManager?.handleWebSocketError(errorType);
            }, 100); // Small delay to ensure proper initialization
        }
    }

    private onOpen(): void {
        logger.info('[WEBSOCKET] Connection established.');
        const websocketTimeoutMS = this.streamManager.getConfig().websocketTimeoutMS;
        if (websocketTimeoutMS) {
            this.messageReceivedTimeout = setTimeout(async () => {
                if (this.messageReceived === false) {
                    logger.warn('[WEBSOCKET] Message receive timeout - triggering retry mechanism');
                    this.socket.close();
                    const errorType: ErrorType = ErrorTypes.WEBSOCKET_TIMEOUT;
                    // Use retry mechanism instead of direct cleanup
                    this.streamManager?.handleWebSocketError(errorType);
                }
                if (this.messageReceivedTimeout) {
                    clearTimeout(this.messageReceivedTimeout);
                }
            }, websocketTimeoutMS);
        }

        this.streamManager?.onWebSocketConnected();
    }

    private async onClose(): Promise<void> {
        // This handler should only be called for unexpected closures.
        logger.warn('[WEBSOCKET] Connection closed unexpectedly.');
        if (this.messageReceivedTimeout) {
            clearTimeout(this.messageReceivedTimeout);
        }
        const errorType: ErrorType = ErrorTypes.WEBSOCKET_ERROR;
        if (!this.streamManager) {
            logger.error('[WEBSOCKET] Stream manager not found.');
            return;
        }
        this.streamManager?.handleWebSocketError(errorType);
    }

    private onError(error: Event): void {
        logger.error('[WEBSOCKET] Connection error:', error);
        if (this.messageReceivedTimeout) {
            clearTimeout(this.messageReceivedTimeout);
        }
        const errorType: ErrorType = ErrorTypes.WEBSOCKET_ERROR;
        this.streamManager?.handleWebSocketError(errorType);
    }

    private onMessage(event: MessageEvent): void {
        this.messageReceived = true;

        if (typeof event.data === 'string') {
            let websocketMessage: Record<string, unknown> | null = null;
            try {
                websocketMessage = JSON.parse(event.data);
            } catch (error) {
                logger.error(error);
            }
            if (websocketMessage) {
                this.streamManager?.handleWebSocketMessage(event.data);
            }
        } else {
            logger.error('webSocket error - unsupported data type received');
        }
    }

    public sendMessage(message: string): void {
        if (this.socket.readyState === WebSocket.OPEN) {
            try {
                this.socket.send(message);
            } catch (error) {
                logger.error('[WEBSOCKET] Failed to send message:', error);
                // Trigger retry mechanism for send failures
                const errorType: ErrorType = ErrorTypes.WEBSOCKET_ERROR;
                this.streamManager?.handleWebSocketError(errorType);
            }
        } else {
            logger.warn('[WEBSOCKET] WebSocket is already in CLOSING or CLOSED state.');
        }
    }

    public close(): Promise<void> {
        logger.debug('[WEBSOCKET] Initiating close with comprehensive cleanup...');

        return new Promise((resolve, reject) => {
            // Clear message timeout first
            if (this.messageReceivedTimeout) {
                clearTimeout(this.messageReceivedTimeout);
                this.messageReceivedTimeout = null;
                logger.debug('[WEBSOCKET] Message timeout cleared');
            }

            // Check if the WebSocket is already closed
            if (this.socket.readyState === WebSocket.CLOSED) {
                this.cleanupEventHandlers();
                resolve();
                return;
            }

            // Remove all event handlers to prevent callbacks during close
            this.cleanupEventHandlers();

            // Add one-time event listeners for close completion
            const closeHandler = (): void => {
                logger.debug('[WEBSOCKET] WebSocket closed successfully');
                resolve();
            };

            const errorHandler = (err: Event): void => {
                logger.error('[WEBSOCKET] Connection error during close:', err);
                reject(new Error('WebSocket encountered an error during close.'));
            };

            this.socket.addEventListener('close', closeHandler);
            this.socket.addEventListener('error', errorHandler);

            // Initiate the WebSocket close
            this.socket.close();
        });
    }

    private cleanupEventHandlers(): void {
        // Remove all event handlers to prevent any callbacks during cleanup
        this.socket.onopen = null;
        this.socket.onclose = null;
        this.socket.onerror = null;
        this.socket.onmessage = null;
        logger.debug('[WEBSOCKET] All event handlers cleared');
    }
}
