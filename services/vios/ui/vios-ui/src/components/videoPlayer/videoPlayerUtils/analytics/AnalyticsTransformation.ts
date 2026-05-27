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
import {
    Calibration,
    transformImageROIToWorld,
    transformImageTripwireToWorld,
    transform2DImageToWorld,
    transform2DWorldToImage,
} from '../../../../utils/maths/translation';
import { CoordinatePoint, TripwireCoordinates, CalibrationData } from './AnalyticsTypes';
import LOG from '../../../../utils/misc/Logger';

export const transformROICoordinates = (
    imageCoords: CoordinatePoint[],
    sensor?: { sensorId: string; name?: string },
    calibrationData?: CalibrationData | null,
    calibrationInstance?: Calibration | null,
    enqueueSnackbar?: (message: string, options: { variant: 'info' | 'success' | 'error' | 'warning' }) => void
): { x: number; y: number; z: number }[] | null => {
    if (!sensor || !calibrationData || imageCoords.length < 3) return null;

    try {
        const sensorData = calibrationData.sensors.find(s => s.id === sensor.sensorId);
        if (!sensorData) {
            throw new Error('Sensor data not found');
        }

        let worldCoords: { x: number; y: number; z: number }[];

        // Check calibration type
        const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;
        const isImageCalibration = calibrationData.calibrationType === 'image';

        if (isImageCalibration) {
            // For image calibration, coordinates are already in image space - no transformation needed
            worldCoords = imageCoords.map(coord => ({
                x: coord.x,
                y: coord.y,
                z: 0, // Default z value for image calibration
            }));
        } else if (is2DCalibration) {
            // Use 2D transformation
            if (!sensorData.imageCoordinates || !sensorData.globalCoordinates) {
                throw new Error('2D calibration requires imageCoordinates and globalCoordinates');
            }

            worldCoords = transform2DImageToWorld(imageCoords, sensorData.imageCoordinates, sensorData.globalCoordinates);
        } else {
            // Use 3D transformation with homography
            if (!calibrationInstance) {
                throw new Error('Calibration instance required for 3D transformation');
            }

            const transformedCoords = transformImageROIToWorld(
                imageCoords.map(coord => ({ ...coord, z: 0 })),
                sensor.sensorId,
                calibrationInstance
            );
            worldCoords = transformedCoords.map(coord => ({
                x: coord.x,
                y: coord.y,
                z: coord.z ?? 0,
            }));
        }

        return worldCoords;
    } catch (error) {
        LOG.error('Failed to transform ROI coordinates:', error);
        enqueueSnackbar?.(`Failed to transform ROI coordinates: ${error}`, {
            variant: 'error',
        });
        return null;
    }
};

export const transformTripwireCoordinates = (
    imageCoords: TripwireCoordinates,
    sensor?: { sensorId: string; name?: string },
    calibrationData?: CalibrationData | null,
    calibrationInstance?: Calibration | null,
    enqueueSnackbar?: (message: string, options: { variant: 'info' | 'success' | 'error' | 'warning' }) => void
): {
    p1: { x: number; y: number; z: number };
    p2: { x: number; y: number; z: number };
} | null => {
    if (!sensor || !calibrationData) return null;

    try {
        const sensorData = calibrationData.sensors.find(s => s.id === sensor.sensorId);
        if (!sensorData) {
            throw new Error('Sensor data not found');
        }

        let worldCoords: {
            p1: { x: number; y: number; z: number };
            p2: { x: number; y: number; z: number };
        };

        // Check calibration type
        const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;
        const isImageCalibration = calibrationData.calibrationType === 'image';

        if (isImageCalibration) {
            // For image calibration, coordinates are already in image space - no transformation needed
            worldCoords = {
                p1: {
                    x: imageCoords.p1.x,
                    y: imageCoords.p1.y,
                    z: 0, // Default z value for image calibration
                },
                p2: {
                    x: imageCoords.p2.x,
                    y: imageCoords.p2.y,
                    z: 0, // Default z value for image calibration
                },
            };
        } else if (is2DCalibration) {
            // Use 2D transformation for tripwire
            if (!sensorData.imageCoordinates || !sensorData.globalCoordinates) {
                throw new Error('2D calibration requires imageCoordinates and globalCoordinates');
            }

            const tripwirePoints = [imageCoords.p1, imageCoords.p2];
            const transformedPoints = transform2DImageToWorld(tripwirePoints, sensorData.imageCoordinates, sensorData.globalCoordinates);

            worldCoords = {
                p1: transformedPoints[0],
                p2: transformedPoints[1],
            };
        } else {
            // Use 3D transformation with homography
            if (!calibrationInstance) {
                throw new Error('Calibration instance required for 3D transformation');
            }

            const transformedCoords = transformImageTripwireToWorld(imageCoords, sensor.sensorId, calibrationInstance);

            worldCoords = {
                p1: {
                    x: transformedCoords.p1.x,
                    y: transformedCoords.p1.y,
                    z: transformedCoords.p1.z ?? 0,
                },
                p2: {
                    x: transformedCoords.p2.x,
                    y: transformedCoords.p2.y,
                    z: transformedCoords.p2.z ?? 0,
                },
            };
        }

        return worldCoords;
    } catch (error) {
        LOG.error('Failed to transform tripwire coordinates:', error);
        enqueueSnackbar?.(`Failed to transform tripwire coordinates: ${error}`, {
            variant: 'error',
        });
        return null;
    }
};

// Reverse transformation functions for displaying existing data

export const reverseTransformROICoordinates = (
    worldCoords: { x: number; y: number; z: number }[],
    sensor?: { sensorId: string; name?: string },
    calibrationData?: CalibrationData | null,
    reverseCalibrationInstance?: unknown,
    enqueueSnackbar?: (message: string, options: { variant: 'info' | 'success' | 'error' | 'warning' }) => void
): CoordinatePoint[] | null => {
    if (!sensor || !calibrationData || worldCoords.length < 3) return null;

    try {
        const sensorData = calibrationData.sensors.find(s => s.id === sensor.sensorId);
        if (!sensorData) {
            throw new Error('Sensor data not found');
        }

        let imageCoords: CoordinatePoint[];

        // Check calibration type
        const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;
        const isImageCalibration = calibrationData.calibrationType === 'image';

        if (isImageCalibration) {
            // For image calibration, coordinates are already in image space - no transformation needed
            imageCoords = worldCoords.map(coord => ({
                x: coord.x,
                y: coord.y,
            }));
        } else if (is2DCalibration) {
            // Use 2D reverse transformation
            if (!sensorData.imageCoordinates || !sensorData.globalCoordinates) {
                throw new Error('2D calibration requires imageCoordinates and globalCoordinates');
            }

            imageCoords = transform2DWorldToImage(worldCoords, sensorData.imageCoordinates, sensorData.globalCoordinates);
        } else {
            // Use 3D reverse transformation
            if (!reverseCalibrationInstance) {
                throw new Error('Reverse calibration instance required for 3D transformation');
            }

            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            imageCoords = (reverseCalibrationInstance as any).transformROIToImage(worldCoords, sensor.sensorId);
        }

        return imageCoords;
    } catch (error) {
        LOG.error('Failed to reverse transform ROI coordinates:', error);
        enqueueSnackbar?.(`Failed to reverse transform ROI coordinates: ${error}`, {
            variant: 'error',
        });
        return null;
    }
};

export const reverseTransformTripwireCoordinates = (
    worldCoords: {
        p1: { x: number; y: number; z?: number };
        p2: { x: number; y: number; z?: number };
    },
    sensor?: { sensorId: string; name?: string },
    calibrationData?: CalibrationData | null,
    reverseCalibrationInstance?: unknown,
    enqueueSnackbar?: (message: string, options: { variant: 'info' | 'success' | 'error' | 'warning' }) => void
): TripwireCoordinates | null => {
    if (!sensor || !calibrationData) return null;

    try {
        const sensorData = calibrationData.sensors.find(s => s.id === sensor.sensorId);
        if (!sensorData) {
            throw new Error('Sensor data not found');
        }

        let imageCoords: TripwireCoordinates;

        // Check calibration type
        const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;
        const isImageCalibration = calibrationData.calibrationType === 'image';

        if (isImageCalibration) {
            // For image calibration, coordinates are already in image space - no transformation needed
            imageCoords = {
                p1: { x: worldCoords.p1.x, y: worldCoords.p1.y },
                p2: { x: worldCoords.p2.x, y: worldCoords.p2.y },
            };
        } else if (is2DCalibration) {
            // Use 2D reverse transformation for tripwire
            if (!sensorData.imageCoordinates || !sensorData.globalCoordinates) {
                throw new Error('2D calibration requires imageCoordinates and globalCoordinates');
            }

            const tripwireWorldPoints = [
                { x: worldCoords.p1.x, y: worldCoords.p1.y, z: worldCoords.p1.z || 0 },
                { x: worldCoords.p2.x, y: worldCoords.p2.y, z: worldCoords.p2.z || 0 },
            ];

            const transformedPoints = transform2DWorldToImage(
                tripwireWorldPoints,
                sensorData.imageCoordinates,
                sensorData.globalCoordinates
            );

            imageCoords = {
                p1: transformedPoints[0],
                p2: transformedPoints[1],
            };
        } else {
            // Use 3D reverse transformation with homography
            if (!reverseCalibrationInstance) {
                throw new Error('Reverse calibration instance required for 3D transformation');
            }

            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const transformedCoords = (reverseCalibrationInstance as any).transformTripwireToImage(
                {
                    p1: { x: worldCoords.p1.x, y: worldCoords.p1.y, z: worldCoords.p1.z || 0 },
                    p2: { x: worldCoords.p2.x, y: worldCoords.p2.y, z: worldCoords.p2.z || 0 },
                },
                sensor.sensorId
            );

            if (!transformedCoords) {
                throw new Error('Failed to transform tripwire coordinates');
            }

            imageCoords = transformedCoords;
        }

        return imageCoords;
    } catch (error) {
        LOG.error('Failed to reverse transform tripwire coordinates:', error);
        enqueueSnackbar?.(`Failed to reverse transform tripwire coordinates: ${error}`, {
            variant: 'error',
        });
        return null;
    }
};
