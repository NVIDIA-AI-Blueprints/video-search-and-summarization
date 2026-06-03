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

export const ErrorTypes = {
    GET_USER_MEDIA_ERROR: {
        code: 1,
        message: 'Failed to access microphone or camera',
    },
    INBOUND_STREAM_ERROR: {
        code: 2,
        message: 'Failed to start inbound stream',
    },
    INBOUND_STREAM_REMOTE_DESCRIPTION_ERROR: {
        code: 3,
        message: 'Failed to set remote description for inbound stream',
    },
    OUTBOUND_STREAM_ERROR: {
        code: 4,
        message: 'Failed to start outbound stream',
    },
    WEBSOCKET_ERROR: {
        code: 5,
        message: 'WebSocket connection failed',
    },
    BUSY_ERROR: {
        code: 6,
        message: 'Previous streaming request is under processing',
    },
    INVALID_PARAMETER_ERROR: {
        code: 7,
        message: 'One or more invalid parameter provided',
    },
    WEBSOCKET_TIMEOUT: {
        code: 8,
        message: 'Websocket connection timed out',
    },
} as const;

// Base error type from ErrorTypes
export type BaseErrorType = (typeof ErrorTypes)[keyof typeof ErrorTypes];

// Extended error type that allows custom messages
export type ErrorType =
    | BaseErrorType
    | {
          code: number;
          message: string;
      };

export type ErrorCode = ErrorType['code'];
export type ErrorMessage = ErrorType['message'];
