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
import { Calibration } from '../../../../utils/maths/translation';
import { CalibrationData } from './AnalyticsTypes';
import { StreamType } from 'vst-streaming-lib';
import nvAxios from '../../../../services/Axios';
import config from '../../../../config';
import LOG from '../../../../utils/misc/Logger';

export const fetchCalibrationData = async (
    sensor?: { sensorId: string; name?: string },
    streamType?: StreamType
): Promise<{
    calibrationData: CalibrationData | null;
    calibrationInstance: Calibration | null;
}> => {
    if (!sensor?.sensorId || streamType === StreamType.VideoWall) {
        console.log('Calibration fetch skipped:', {
            hasSensor: !!sensor?.sensorId,
            streamType,
            isVideoWall: streamType === StreamType.VideoWall,
        });
        return { calibrationData: null, calibrationInstance: null };
    }

    console.log('Starting calibration data fetch for sensor:', sensor.sensorId);

    try {
        const endpoint = `${config.mdatWebApiEndpoint}/config/calibration?sensorId=${sensor.sensorId}`;
        console.log('Fetching from endpoint:', endpoint);

        const response = await nvAxios.get(endpoint, {
            headers: { streamId: sensor.sensorId },
        });
        console.log('Calibration response received:', response.data);

        if (response.data) {
            const calibData = response.data as CalibrationData;
            console.log('Calibration data set:', calibData);

            // Find the sensor data for the selected sensor
            const sensorData = calibData.sensors.find(s => s.id === sensor.sensorId);
            if (!sensorData) {
                throw new Error(`No calibration data found for sensor ${sensor.sensorId}`);
            }

            // Check if this is a 2D or 3D calibration
            const is2DCalibration = calibData.calibrationType === 'cartesian' && !sensorData.homography;
            console.log('Calibration type detected:', {
                type: calibData.calibrationType,
                is2D: is2DCalibration,
                hasHomography: !!sensorData.homography,
            });

            let calibrationInstance: Calibration | null = null;

            if (calibData.calibrationType === 'image') {
                // For image calibration, coordinates are already in image space - no transformation needed
                LOG.info('Image calibration detected - using direct image coordinates');
            } else if (is2DCalibration) {
                // For 2D calibration, we don't need homography-based transformation
                LOG.info('2D calibration detected - using direct coordinate mapping');
            } else {
                // For 3D calibration with homography matrix
                if (!sensorData.homography) {
                    throw new Error('Homography matrix required for 3D calibration but not found');
                }

                // Create calibration instance
                const sensorMap = {
                    [sensor.sensorId]: {
                        origin: {
                            lat: sensorData.origin.lat,
                            lon: sensorData.origin.lng,
                            alt: 0,
                        },
                        homography: sensorData.homography,
                    },
                };
                calibrationInstance = new Calibration(sensorMap);
            }

            LOG.info('Calibration data loaded successfully');

            return { calibrationData: calibData, calibrationInstance };
        } else {
            throw new Error('No calibration data received');
        }
    } catch (error) {
        console.error('Calibration fetch error:', error);
        LOG.error('Failed to fetch calibration data:', error);
        return { calibrationData: null, calibrationInstance: null };
    }
};
