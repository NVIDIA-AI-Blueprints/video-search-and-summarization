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
import config from '../../../config';
import { LiveStreamConfig, ROI, ROIJSON, StreamList, Tripwire, TripwireJSON } from '../../../interfaces/interfaces';
import nvAxios from '../../../services/Axios';
import LOG from '../../../utils/misc/Logger';

export const getEMDXEndpoint = async () => {
    try {
        const response = await nvAxios.get(`${config.liveStreamEndpoint}/api/v1/live/configuration`);
        const data: LiveStreamConfig = response.data;
        const emdxEndpoint = data.analyticServerAddress;
        return emdxEndpoint;
    } catch (error) {
        LOG.error(`Failed to get live settings`, error);
        return '';
    }
};

export const getROIData = async (endpoint: string, sensorName: string): Promise<ROI[] | undefined> => {
    LOG.info('Called get ROI data');
    if (endpoint != '') {
        try {
            const response = await nvAxios.get(`${endpoint}/api/v2/config/tripwire?sensorId=${sensorName}`, {
                headers: { streamId: sensorName },
            });
            return response.data.rois;
        } catch (error) {
            LOG.error(`Failed to get ROI data`, error);
        }
    } else {
        LOG.error('Invalid emdx endpoint');
    }
    return undefined;
};

export const getTripwireData = async (endpoint: string, sensorName: string): Promise<Tripwire[] | undefined> => {
    LOG.info('Called get tripwire data');
    if (endpoint != '') {
        try {
            const response = await nvAxios.get(`${endpoint}/api/v2/config/tripwire?sensorId=${sensorName}`, {
                headers: { streamId: sensorName },
            });
            return response.data.tripwires;
        } catch (error) {
            LOG.error(`Failed to get tripwire data`, error);
        }
    } else {
        LOG.error('Invalid emdx endpoint');
    }
    return undefined;
};

export const postROIData = async (endpoint: string, roiDATA: ROIJSON) => {
    LOG.info('Called post ROI data');
    if (endpoint != '') {
        try {
            const response = await nvAxios.post(`${endpoint}/api/v2/config/roi`, roiDATA, {
                headers: { 'User-Type': 'Admin', streamId: roiDATA.sensorId },
            });
            LOG.info('Post ROI response: ', response.data);
        } catch (error) {
            LOG.error(`Failed to post ROI data`, error);
        }
    } else {
        LOG.error('Invalid emdx endpoint');
    }
};

export const postTripwireData = async (endpoint: string, tripwireData: TripwireJSON) => {
    LOG.info('Called post tripwire data');
    if (endpoint != '') {
        try {
            const response = await nvAxios.post(`${endpoint}/api/v2/config/tripwire`, tripwireData, {
                headers: { 'User-Type': 'Admin', streamId: tripwireData.sensorId },
            });
            LOG.info('Post tripwire response: ', response.data);
        } catch (error) {
            LOG.error(`Failed to post tripwire data`, error);
        }
    } else {
        LOG.error('Invalid emdx endpoint');
    }
};

export const deleteTripwireData = async (endpoint: string, sensorName: string, tripwireData: Tripwire[]) => {
    LOG.info('Called delete tripwire data');

    if (endpoint != '') {
        for (let i = 0; i < tripwireData.length; i += 1) {
            try {
                const response = await nvAxios.delete(
                    `${endpoint}/api/v2/config/tripwire?sensorId=${sensorName}&tripwireId=${tripwireData[i].id}`,
                    { headers: { 'User-Type': 'Admin', streamId: sensorName } }
                );
                LOG.info('Delete tripwire response: ', response.data);
            } catch (error) {
                LOG.error(`Failed to get tripwire data`, error);
            }
        }
    } else {
        LOG.error('Invalid emdx endpoint');
    }
};

export const deleteROIData = async (endpoint: string, sensorName: string, ROIData: ROI[]) => {
    LOG.info('Called delete ROI data');

    if (endpoint != '') {
        for (let i = 0; i < ROIData.length; i += 1) {
            try {
                const response = await nvAxios.delete(`${endpoint}/api/v2/config/roi?sensorId=${sensorName}&tripwireId=${ROIData[i].id}`, {
                    headers: { 'User-Type': 'Admin', streamId: sensorName },
                });
                LOG.info('Delete ROI response: ', response.data);
            } catch (error) {
                LOG.error(`Failed to get ROI data`, error);
            }
        }
    } else {
        LOG.error('Invalid emdx endpoint');
    }
};

export const getVideoDimensions = async () => {
    try {
        const response = await nvAxios.get(`${config.liveStreamEndpoint}/api/v1/live/streams`);
        const data: StreamList = response.data;

        for (const streams of Object.values(data)) {
            const mainStream = streams.find(stream => stream.isMain);

            if (mainStream) {
                LOG.info('Main stream metadata: ', mainStream.metadata);
                const { framerate, resolution } = mainStream.metadata;
                const [width, height] = resolution.split('x');
                LOG.info(`Found Width: ${width}, Height: ${height}, Framerate: ${framerate}`);

                const numWidth = Number(width) ?? 1920;
                const numHeight = Number(height) ?? 1080;
                const numFramerate = Number(framerate) ?? 30;

                LOG.info(`Using Width: ${numWidth}, Height: ${numHeight}, Framerate: ${numFramerate}`);
                return {
                    videoWidth: numWidth,
                    videoHeight: numHeight,
                    videoFramerate: numFramerate,
                };
            }
        }
    } catch (error) {
        LOG.error(`Failed to get live settings, using default values`, error);
        return { videoWidth: 1920, videoHeight: 1080, videoFramerate: 30 };
    }
};
