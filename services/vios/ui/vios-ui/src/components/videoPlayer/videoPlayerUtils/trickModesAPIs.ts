/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import { StreamType } from 'vst-streaming-lib';
import config from '../../../config';
import nvAxios from '../../../services/Axios';
import LOG from '../../../utils/misc/Logger';
import { VariantType } from 'notistack';

type EnqueueSnackbar = (message: string, options?: { variant: VariantType }) => void;

interface ErrorResponse {
    response?: {
        data?: {
            error_message?: string;
        };
    };
}

export const rewindOrFastforward = async (
    peerId: string,
    mediaSessionId: string,
    amount: number,
    sensorId: string,
    enqueueSnackbar: EnqueueSnackbar
) => {
    LOG.info('Called rewind or FF');

    const jsonData = {
        peerId: peerId,
        mediaSessionId: mediaSessionId,
        action: amount >= 1 ? 'fastForward' : 'rewind',
        value: amount,
    };

    try {
        const response = await nvAxios.post(`${config.replayStreamEndpoint}/api/v1/replay/stream/seek`, jsonData, {
            headers: { streamId: sensorId },
        });
        LOG.info(`Stream playback speed ${amount} successfully`, response?.data);
        console.log(`Rewind/FF ${amount} ? success`);
        return true;
    } catch (error: unknown) {
        LOG.error(`Stream playback speed ${amount} error`, error);
        const errorResponse = error as ErrorResponse;
        const errorMessage = errorResponse.response?.data?.error_message || 'Failed to change playback speed';
        enqueueSnackbar(errorMessage, { variant: 'error' });
        return false;
    }
};

export const seekToTime = async (
    peerId: string,
    mediaSessionId: string,
    ISOTime: string,
    sensorId: string,
    enqueueSnackbar: EnqueueSnackbar
) => {
    LOG.info('Called seek to time');

    const jsonData = {
        peerId: peerId,
        mediaSessionId: mediaSessionId,
        action: 'seekForward', // action key is irrelevant for ISO time seek
        value: ISOTime,
    };

    try {
        const response = await nvAxios.post(`${config.replayStreamEndpoint}/api/v1/replay/stream/seek`, jsonData, {
            headers: { streamId: sensorId },
        });
        LOG.info(`Stream seeked to ${ISOTime} successfully`, response?.data);
        return true;
    } catch (error: unknown) {
        LOG.error(`Stream seek to ${ISOTime} error`, error);
        const errorResponse = error as ErrorResponse;
        const errorMessage = errorResponse.response?.data?.error_message || 'Failed to seek to time';
        enqueueSnackbar(errorMessage, { variant: 'error' });
        return false;
    }
};

export const pause = async (
    peerId: string,
    mediaSessionId: string,
    streamType: StreamType,
    sensorId: string,
    enqueueSnackbar: EnqueueSnackbar
): Promise<boolean> => {
    try {
        // Use the appropriate endpoint based on stream type
        const endpoint =
            streamType === StreamType.Live
                ? `${config.liveStreamEndpoint}/api/v1/live/stream/pause`
                : `${config.replayStreamEndpoint}/api/v1/replay/stream/pause`;

        const response = await nvAxios.post(
            endpoint,
            {
                peerId,
                mediaSessionId,
            },
            { headers: { streamId: sensorId } }
        );
        return response.status === 200;
    } catch (error: unknown) {
        LOG.error('Failed to pause stream:', error);
        const errorResponse = error as ErrorResponse;
        const errorMessage = errorResponse.response?.data?.error_message || 'Failed to pause stream';
        enqueueSnackbar(errorMessage, { variant: 'error' });
        return false;
    }
};

export const resume = async (
    peerId: string,
    mediaSessionId: string,
    streamType: StreamType,
    sensorId: string,
    enqueueSnackbar: EnqueueSnackbar
): Promise<boolean> => {
    LOG.info('Called resume');

    const jsonData = {
        peerId: peerId,
        mediaSessionId: mediaSessionId,
    };

    try {
        // Use the appropriate endpoint based on stream type
        const endpoint =
            streamType === StreamType.Live
                ? `${config.liveStreamEndpoint}/api/v1/live/stream/resume`
                : `${config.replayStreamEndpoint}/api/v1/replay/stream/resume`;

        const response = await nvAxios.post(endpoint, jsonData, {
            headers: { streamId: sensorId },
        });
        LOG.info('Stream resumed successfully', response?.data);
        return response.status === 200;
    } catch (error: unknown) {
        LOG.error('Stream resume error', error);
        const errorResponse = error as ErrorResponse;
        const errorMessage = errorResponse.response?.data?.error_message || 'Failed to resume stream';
        enqueueSnackbar(errorMessage, { variant: 'error' });
        return false;
    }
};

export const seekForward = async (peerId: string, mediaSessionId: string, sensorId: string, enqueueSnackbar: EnqueueSnackbar) => {
    LOG.info('Called seek');

    const jsonData = {
        peerId: peerId,
        mediaSessionId: mediaSessionId,
        action: 'seekForward',
    };

    try {
        const response = await nvAxios.post(`${config.replayStreamEndpoint}/api/v1/replay/stream/seek`, jsonData, {
            headers: { streamId: sensorId },
        });
        LOG.info('Stream seeked +10 successfully', response?.data);
        console.log('+10 seek ? success');
        return true;
    } catch (error: unknown) {
        LOG.error('Stream seek +10 error', error);
        const errorResponse = error as ErrorResponse;
        const errorMessage = errorResponse.response?.data?.error_message || 'Failed to seek forward';
        enqueueSnackbar(errorMessage, { variant: 'error' });
        return false;
    }
};

export const seekBackward = async (peerId: string, mediaSessionId: string, sensorId: string, enqueueSnackbar: EnqueueSnackbar) => {
    LOG.info('Called seek');

    const jsonData = {
        peerId: peerId,
        mediaSessionId: mediaSessionId,
        action: 'seekBackward',
    };

    try {
        const response = await nvAxios.post(`${config.replayStreamEndpoint}/api/v1/replay/stream/seek`, jsonData, {
            headers: { streamId: sensorId },
        });
        LOG.info('Stream seeked -10 successfully', response?.data);
        console.log('-10 seek ? success');
        return true;
    } catch (error: unknown) {
        LOG.error('Stream seek -10 error', error);
        const errorResponse = error as ErrorResponse;
        const errorMessage = errorResponse.response?.data?.error_message || 'Failed to seek backward';
        enqueueSnackbar(errorMessage, { variant: 'error' });
        return false;
    }
};

export const swapStream = async (
    peerId: string,
    mediaSessionId: string,
    streamId: string,
    sensorId: string,
    enqueueSnackbar: EnqueueSnackbar
) => {
    LOG.info('Called swap stream');

    const jsonData = {
        peerId: peerId,
        mediaSessionId: mediaSessionId,
        streamId: streamId,
    };

    try {
        const response = await nvAxios.post(`${config.replayStreamEndpoint}/api/v1/replay/stream/swap`, jsonData, {
            headers: { streamId: sensorId },
        });
        LOG.info('Stream swap successfully', response?.data);
        return true;
    } catch (error: unknown) {
        LOG.error('Stream swap error', error);
        const errorResponse = error as ErrorResponse;
        const errorMessage = errorResponse.response?.data?.error_message || 'Failed to swap stream';
        enqueueSnackbar(errorMessage, { variant: 'error' });
        return false;
    }
};
