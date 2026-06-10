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
import { useState, useCallback, useEffect } from 'react';
import { StreamType } from 'vst-streaming-lib';
import { Calibration, ReverseCalibration, transform2DWorldToImage } from '../../../utils/maths/translation';
import { DrawingMode, CoordinatePoint, TripwireCoordinates, CalibrationData } from './analytics/AnalyticsTypes';
import { fetchCalibrationData } from './analytics/AnalyticsCalibration';
import { transformROICoordinates, transformTripwireCoordinates } from './analytics/AnalyticsTransformation';
import LOG from '../../../utils/misc/Logger';
import nvAxios from '../../../services/Axios';
import config from '../../../config';

interface UseAnalyticsProps {
    sensor?: { sensorId: string; name?: string };
    streamType: StreamType;
    enqueueSnackbar?: (message: string, options: { variant: 'info' | 'success' | 'error' | 'warning' }) => void;
}

interface ExistingROIDisplay {
    id: string;
    imageCoords: CoordinatePoint[];
}

interface ExistingTripwireDisplay {
    id: string;
    wire: TripwireCoordinates;
    direction?: TripwireCoordinates;
}

export const useAnalytics = ({ sensor, streamType, enqueueSnackbar }: UseAnalyticsProps) => {
    // Drawing state
    const [drawingMode, setDrawingMode] = useState<DrawingMode>('none');
    const [roiPoints, setROIPoints] = useState<CoordinatePoint[]>([]);
    const [tripwirePoints, setTripwirePoints] = useState<TripwireCoordinates | null>(null);
    const [directionPoints, setDirectionPoints] = useState<TripwireCoordinates | null>(null);
    const [tempTripwireStart, setTempTripwireStart] = useState<CoordinatePoint | null>(null);
    const [tempDirectionStart, setTempDirectionStart] = useState<CoordinatePoint | null>(null);
    const [lastClickTime, setLastClickTime] = useState<number>(0);
    const [lastClickPoint, setLastClickPoint] = useState<CoordinatePoint | null>(null);

    // Calibration state
    const [calibrationData, setCalibrationData] = useState<CalibrationData | null>(null);
    const [calibrationInstance, setCalibrationInstance] = useState<Calibration | null>(null);
    const [reverseCalibrationInstance, setReverseCalibrationInstance] = useState<ReverseCalibration | null>(null);
    const [isLoadingCalibration, setIsLoadingCalibration] = useState<boolean>(false);
    const [hasCalibrationError, setHasCalibrationError] = useState<boolean>(false);

    // Existing items selection state
    const [selectedROIIds, setSelectedROIIds] = useState<string[]>([]);
    const [selectedTripwireIds, setSelectedTripwireIds] = useState<string[]>([]);
    const [existingROIsForDisplay, setExistingROIsForDisplay] = useState<ExistingROIDisplay[]>([]);
    const [existingTripwiresForDisplay, setExistingTripwiresForDisplay] = useState<ExistingTripwireDisplay[]>([]);

    // Name dialog state
    const [showNameDialog, setShowNameDialog] = useState<boolean>(false);
    const [roiName, setRoiName] = useState<string>('');
    const [tripwireName, setTripwireName] = useState<string>('');

    // Check if calibration data is empty or invalid
    const isCalibrationDataEmpty = useCallback(
        (calibrationData: CalibrationData | null) => {
            if (!calibrationData) return true;

            const sensorData = calibrationData.sensors.find(s => s.id === sensor?.sensorId);
            if (!sensorData) return true;

            // For image calibration type, no coordinate transformation is needed, so it's always valid
            if (calibrationData.calibrationType === 'image') {
                return false;
            }

            // For cartesian calibration types, check if imageCoordinates and globalCoordinates are empty
            const hasEmptyImageCoords = !sensorData.imageCoordinates || sensorData.imageCoordinates.length === 0;
            const hasEmptyGlobalCoords = !sensorData.globalCoordinates || sensorData.globalCoordinates.length === 0;

            return hasEmptyImageCoords && hasEmptyGlobalCoords;
        },
        [sensor?.sensorId]
    );

    // Fetch calibration data
    const handleFetchCalibrationData = useCallback(async () => {
        if (!sensor?.sensorId || streamType === StreamType.VideoWall) {
            return;
        }

        setIsLoadingCalibration(true);
        setHasCalibrationError(false);

        try {
            const result = await fetchCalibrationData(sensor, streamType);

            // Check if API returned empty calibration data
            if (isCalibrationDataEmpty(result.calibrationData)) {
                LOG.warn('Calibration data is empty for sensor:', sensor.sensorId);
                setHasCalibrationError(true);
                setCalibrationData(null);
                setCalibrationInstance(null);
                setReverseCalibrationInstance(null);
                return;
            }

            setCalibrationData(result.calibrationData);
            setCalibrationInstance(result.calibrationInstance);

            // Create reverse calibration instance for 3D calibration
            if (result.calibrationInstance && result.calibrationData) {
                const sensorData = result.calibrationData.sensors.find(s => s.id === sensor.sensorId);
                if (sensorData?.homography) {
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
                    const reverseCalibInstance = new ReverseCalibration(sensorMap);
                    setReverseCalibrationInstance(reverseCalibInstance);
                }
            }
        } catch (error) {
            LOG.error('Failed to fetch calibration data:', error);
            setHasCalibrationError(true);
            setCalibrationData(null);
            setCalibrationInstance(null);
            setReverseCalibrationInstance(null);
            enqueueSnackbar?.('Failed to load calibration data', {
                variant: 'error',
            });
        } finally {
            setIsLoadingCalibration(false);
        }
    }, [sensor, streamType, enqueueSnackbar, isCalibrationDataEmpty]);

    // Transform coordinates
    const handleTransformROI = useCallback(
        (imageCoords: CoordinatePoint[]) => {
            return transformROICoordinates(imageCoords, sensor, calibrationData, calibrationInstance, enqueueSnackbar);
        },
        [sensor, calibrationData, calibrationInstance, enqueueSnackbar]
    );

    const handleTransformTripwire = useCallback(
        (imageCoords: TripwireCoordinates) => {
            return transformTripwireCoordinates(imageCoords, sensor, calibrationData, calibrationInstance, enqueueSnackbar);
        },
        [sensor, calibrationData, calibrationInstance, enqueueSnackbar]
    );

    // Handle canvas clicks for drawing
    const handleCanvasClick = useCallback(
        (event: React.MouseEvent<HTMLCanvasElement>, videoRef: React.RefObject<HTMLVideoElement>) => {
            const canvas = event.currentTarget;
            const video = videoRef.current;
            if (!canvas || !video) return;

            const rect = canvas.getBoundingClientRect();
            const canvasX = event.clientX - rect.left;
            const canvasY = event.clientY - rect.top;

            // Convert to video coordinates
            // Since canvas now matches video content area, scaling is straightforward
            const scaleX = video.videoWidth / canvas.width;
            const scaleY = video.videoHeight / canvas.height;
            const videoX = Math.round(canvasX * scaleX);
            const videoY = Math.round(canvasY * scaleY);

            const clickPoint = { x: videoX, y: videoY };
            const currentTime = Date.now();

            // Check for double-click
            const isDoubleClick =
                currentTime - lastClickTime < 300 &&
                lastClickPoint &&
                Math.abs(clickPoint.x - lastClickPoint.x) < 10 &&
                Math.abs(clickPoint.y - lastClickPoint.y) < 10;

            if (drawingMode === 'roi') {
                if (isDoubleClick && roiPoints.length >= 3) {
                    // Close the ROI polygon on double-click
                    LOG.info('ROI polygon completed with points:', roiPoints);
                    enqueueSnackbar?.('ROI polygon completed', { variant: 'success' });
                    setDrawingMode('none');
                } else {
                    // Add new ROI point
                    const newPoints = [...roiPoints, clickPoint];
                    setROIPoints(newPoints);
                }
            } else if (drawingMode === 'tripwire-line') {
                if (!tempTripwireStart) {
                    // If tripwire line already exists, clear it and start new line
                    if (tripwirePoints) {
                        setTripwirePoints(null);
                        LOG.info('Cleared existing tripwire line, starting new line');
                    }
                    // First point of new tripwire line
                    setTempTripwireStart(clickPoint);
                } else {
                    // Second point of tripwire - complete the line
                    const newTripwire = { p1: tempTripwireStart, p2: clickPoint };
                    setTripwirePoints(newTripwire);
                    LOG.info('Tripwire line drawn:', newTripwire);
                    setTempTripwireStart(null);
                    // Stay in tripwire-line mode for continuous editing
                }
            } else if (drawingMode === 'tripwire-direction') {
                if (!tempDirectionStart) {
                    // If direction already exists, clear it and start new direction
                    if (directionPoints) {
                        setDirectionPoints(null);
                        LOG.info('Cleared existing direction, starting new direction');
                    }
                    // First point of new direction
                    setTempDirectionStart(clickPoint);
                } else {
                    // Second point of direction - complete the direction
                    const newDirection = { p1: tempDirectionStart, p2: clickPoint };
                    setDirectionPoints(newDirection);
                    LOG.info('Tripwire direction drawn:', newDirection);
                    setTempDirectionStart(null);
                    // Stay in tripwire-direction mode for continuous editing
                }
            }

            setLastClickTime(currentTime);
            setLastClickPoint(clickPoint);
        },
        [
            drawingMode,
            roiPoints,
            tempTripwireStart,
            tempDirectionStart,
            lastClickTime,
            lastClickPoint,
            handleTransformROI,
            handleTransformTripwire,
            enqueueSnackbar,
        ]
    );

    // Submit drawing data to API
    const submitDrawingData = useCallback(async () => {
        if (!sensor || !calibrationData) {
            enqueueSnackbar?.('Please select a sensor and load calibration data first', {
                variant: 'warning',
            });
            return;
        }

        try {
            // Find the sensor data from calibration response
            const sensorData = calibrationData.sensors.find(s => s.id === sensor.sensorId);
            if (!sensorData) {
                throw new Error(`Sensor data not found for ${sensor.sensorId}`);
            }

            let newROI = null;
            let newTripwire = null;

            // Transform and create ROI if available
            if (roiPoints.length >= 3) {
                const worldCoords = handleTransformROI(roiPoints);
                if (worldCoords) {
                    newROI = {
                        id: roiName || `new_roi_${Date.now()}`,
                        roiCoordinates: worldCoords.map(coord => ({
                            x: coord.x,
                            y: coord.y,
                            z: coord.z || 0,
                        })),
                    };
                }
            }

            // Transform and create Tripwire if available
            if (tripwirePoints && directionPoints) {
                const transformedTripwire = handleTransformTripwire(tripwirePoints);
                const transformedDirection = handleTransformTripwire(directionPoints);

                if (transformedTripwire && transformedDirection) {
                    newTripwire = {
                        id: tripwireName || `new_tripwire_${Date.now()}`,
                        wire: {
                            p1: {
                                x: transformedTripwire.p1.x,
                                y: transformedTripwire.p1.y,
                            },
                            p2: {
                                x: transformedTripwire.p2.x,
                                y: transformedTripwire.p2.y,
                            },
                        },
                        direction: {
                            p1: {
                                x: transformedDirection.p1.x,
                                y: transformedDirection.p1.y,
                            },
                            p2: {
                                x: transformedDirection.p2.x,
                                y: transformedDirection.p2.y,
                            },
                        },
                    };
                }
            }

            if (!newROI && !newTripwire) {
                enqueueSnackbar?.('Please draw at least ROI or complete Tripwire (line + direction) first', {
                    variant: 'warning',
                });
                return;
            }

            // Create updated sensors array with new items added to the selected sensor
            const updatedSensors = calibrationData.sensors.map(s => {
                if (s.id === sensor.sensorId) {
                    const updatedSensor = { ...s };

                    // Add ROI if available
                    if (newROI) {
                        updatedSensor.rois = [...(s.rois || []), newROI];
                    }

                    // Add tripwire if available
                    if (newTripwire) {
                        updatedSensor.tripwires = [...(s.tripwires || []), newTripwire];
                    }

                    return updatedSensor;
                }
                return s;
            });

            const submissionData = {
                version: calibrationData.version,
                osmURL: calibrationData.osmURL || '',
                calibrationType: calibrationData.calibrationType,
                sensors: updatedSensors,
            };

            const response = await nvAxios.post(`${config.mdatWebApiEndpoint}/config/calibration/upsert`, submissionData, {
                headers: { streamId: sensor.sensorId },
            });

            LOG.info('Drawing data submitted successfully:', response.data);

            // Create a more specific success message
            const submittedItems = [];
            if (newROI) submittedItems.push('ROI');
            if (newTripwire) submittedItems.push('Tripwire');

            enqueueSnackbar?.(`${submittedItems.join(' and ')} submitted successfully`, {
                variant: 'success',
            });

            // Clear the drawn coordinates and exit drawing mode
            clearAllDrawing();
            setDrawingMode('none');

            // Refetch calibration data to get the updated data
            handleFetchCalibrationData();
        } catch (error) {
            LOG.error('Failed to submit drawing data:', error);
            enqueueSnackbar?.(`Failed to submit drawing data: ${error}`, {
                variant: 'error',
            });
        }
    }, [
        sensor,
        calibrationData,
        roiPoints,
        tripwirePoints,
        directionPoints,
        roiName,
        tripwireName,
        handleTransformROI,
        handleTransformTripwire,
        enqueueSnackbar,
        handleFetchCalibrationData,
    ]);

    // Clear all drawing data
    const clearAllDrawing = useCallback(() => {
        setROIPoints([]);
        setTripwirePoints(null);
        setDirectionPoints(null);
        setTempTripwireStart(null);
        setTempDirectionStart(null);
        setRoiName('');
        setTripwireName('');
        // Note: Don't reset drawingMode here, let user continue drawing
    }, []);

    // Clear in-progress drawing data (keep completed drawings)
    const clearInProgressDrawing = useCallback(() => {
        setROIPoints([]);
        setTempTripwireStart(null);
        setTempDirectionStart(null);
        // Keep tripwirePoints and directionPoints if they're complete
        // Keep roiName and tripwireName
    }, []);

    // Handle name dialog submission
    const handleNameDialogSubmit = useCallback(() => {
        // Validate names
        if (roiPoints.length >= 3 && !roiName.trim()) {
            enqueueSnackbar?.('Please enter a name for the ROI', { variant: 'warning' });
            return;
        }
        if (tripwirePoints && directionPoints && !tripwireName.trim()) {
            enqueueSnackbar?.('Please enter a name for the Tripwire', { variant: 'warning' });
            return;
        }

        setShowNameDialog(false);
        submitDrawingData();
    }, [roiPoints.length, tripwirePoints, directionPoints, roiName, tripwireName, enqueueSnackbar, submitDrawingData]);

    // Handle existing ROI selection change
    const handleROISelectionChange = useCallback(
        (selectedIds: string[]) => {
            setSelectedROIIds(selectedIds);

            if (!calibrationData || !sensor) {
                setExistingROIsForDisplay([]);
                return;
            }

            const sensorData = calibrationData.sensors.find(s => s.id === sensor.sensorId);
            if (!sensorData?.rois) {
                setExistingROIsForDisplay([]);
                return;
            }

            // Convert selected ROIs from world coordinates to image coordinates
            const displayROIs: ExistingROIDisplay[] = [];

            selectedIds.forEach(roiId => {
                const roi = sensorData.rois?.find(r => r.id === roiId);
                if (!roi) {
                    return;
                }

                try {
                    let imageCoords: CoordinatePoint[];

                    // Check calibration type
                    const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;
                    const isImageCalibration = calibrationData.calibrationType === 'image';

                    if (isImageCalibration) {
                        // For image calibration, coordinates might have negative Y values that need transformation
                        const rawCoords = roi.roiCoordinates.map(coord => ({
                            x: coord.x,
                            y: coord.y,
                        }));

                        // Check if coordinates need transformation (negative Y values)
                        const needsTransform = rawCoords.some(coord => coord.y < 0);

                        if (needsTransform) {
                            // Find frame height from sensor attributes
                            const sensorWithAttrs = sensorData as typeof sensorData & {
                                attributes?: Array<{ name: string; value: string }>;
                            };
                            const frameHeightAttr = sensorWithAttrs.attributes?.find(attr => attr.name === 'frameHeight');
                            const frameHeight = frameHeightAttr?.value ? parseInt(frameHeightAttr.value, 10) : 1080;

                            // Transform coordinates: y = frameHeight + originalY (convert from bottom-left to top-left origin)
                            imageCoords = rawCoords.map(coord => ({
                                x: coord.x,
                                y: frameHeight + coord.y,
                            }));

                            console.log('[VST_ANALYTICS_DEBUG] Transformed image calibration ROI coordinates:', {
                                roiId: roi.id,
                                frameHeight,
                                original: rawCoords,
                                transformed: imageCoords,
                            });
                        } else {
                            imageCoords = rawCoords;
                        }
                    } else if (is2DCalibration) {
                        // Use 2D reverse transformation
                        if (!sensorData.imageCoordinates || !sensorData.globalCoordinates) {
                            LOG.warn('2D calibration data missing for reverse transformation');
                            return;
                        }

                        imageCoords = transform2DWorldToImage(
                            roi.roiCoordinates,
                            sensorData.imageCoordinates,
                            sensorData.globalCoordinates
                        );
                    } else {
                        // Use 3D reverse transformation
                        if (!reverseCalibrationInstance) {
                            LOG.warn('Reverse calibration instance not available for 3D transformation');
                            return;
                        }

                        imageCoords = reverseCalibrationInstance.transformROIToImage(roi.roiCoordinates, sensor.sensorId);
                    }

                    displayROIs.push({
                        id: roi.id,
                        imageCoords: imageCoords,
                    });
                } catch (error) {
                    LOG.error(`Failed to reverse transform ROI ${roiId}:`, error);
                }
            });

            setExistingROIsForDisplay(displayROIs);
        },
        [calibrationData, sensor, reverseCalibrationInstance]
    );

    // Handle existing Tripwire selection change
    const handleTripwireSelectionChange = useCallback(
        (selectedIds: string[]) => {
            setSelectedTripwireIds(selectedIds);

            if (!calibrationData || !sensor) {
                setExistingTripwiresForDisplay([]);
                return;
            }

            const sensorData = calibrationData.sensors.find(s => s.id === sensor.sensorId);
            if (!sensorData?.tripwires) {
                setExistingTripwiresForDisplay([]);
                return;
            }

            // Convert selected Tripwires from world coordinates to image coordinates
            const displayTripwires: ExistingTripwireDisplay[] = [];

            selectedIds.forEach(tripwireId => {
                const tripwire = sensorData.tripwires?.find(t => t.id === tripwireId);
                if (!tripwire) return;

                try {
                    let wireImageCoords: TripwireCoordinates;
                    let directionImageCoords: TripwireCoordinates | undefined;

                    // Check calibration type
                    const is2DCalibration = calibrationData.calibrationType === 'cartesian' && !sensorData.homography;
                    const isImageCalibration = calibrationData.calibrationType === 'image';

                    if (isImageCalibration) {
                        // For image calibration, coordinates might have negative Y values that need transformation
                        const rawWireCoords = {
                            p1: { x: tripwire.wire.p1.x, y: tripwire.wire.p1.y },
                            p2: { x: tripwire.wire.p2.x, y: tripwire.wire.p2.y },
                        };

                        // Check if coordinates need transformation (negative Y values)
                        const needsTransform = [rawWireCoords.p1, rawWireCoords.p2].some(coord => coord.y < 0);

                        if (needsTransform) {
                            // Find frame height from sensor attributes
                            const sensorWithAttrs = sensorData as typeof sensorData & {
                                attributes?: Array<{ name: string; value: string }>;
                            };
                            const frameHeightAttr = sensorWithAttrs.attributes?.find(attr => attr.name === 'frameHeight');
                            const frameHeight = frameHeightAttr?.value ? parseInt(frameHeightAttr.value, 10) : 1080;

                            // Transform wire coordinates
                            wireImageCoords = {
                                p1: { x: rawWireCoords.p1.x, y: frameHeight + rawWireCoords.p1.y },
                                p2: { x: rawWireCoords.p2.x, y: frameHeight + rawWireCoords.p2.y },
                            };

                            console.log('[VST_ANALYTICS_DEBUG] Transformed image calibration tripwire coordinates:', {
                                tripwireId: tripwire.id,
                                frameHeight,
                                originalWire: rawWireCoords,
                                transformedWire: wireImageCoords,
                            });
                        } else {
                            wireImageCoords = rawWireCoords;
                        }

                        // Direction coordinates if available
                        if (tripwire.direction) {
                            const rawDirectionCoords = {
                                p1: { x: tripwire.direction.p1.x, y: tripwire.direction.p1.y },
                                p2: { x: tripwire.direction.p2.x, y: tripwire.direction.p2.y },
                            };

                            if (needsTransform) {
                                const sensorWithAttrs = sensorData as typeof sensorData & {
                                    attributes?: Array<{ name: string; value: string }>;
                                };
                                const frameHeightAttr = sensorWithAttrs.attributes?.find(attr => attr.name === 'frameHeight');
                                const frameHeight = frameHeightAttr?.value ? parseInt(frameHeightAttr.value, 10) : 1080;

                                directionImageCoords = {
                                    p1: { x: rawDirectionCoords.p1.x, y: frameHeight + rawDirectionCoords.p1.y },
                                    p2: { x: rawDirectionCoords.p2.x, y: frameHeight + rawDirectionCoords.p2.y },
                                };
                            } else {
                                directionImageCoords = rawDirectionCoords;
                            }
                        }
                    } else if (is2DCalibration) {
                        // Use 2D reverse transformation
                        if (!sensorData.imageCoordinates || !sensorData.globalCoordinates) {
                            LOG.warn('2D calibration data missing for reverse transformation');
                            return;
                        }

                        // Transform wire coordinates
                        const tripwireWorldPoints = [
                            { x: tripwire.wire.p1.x, y: tripwire.wire.p1.y, z: 0 },
                            { x: tripwire.wire.p2.x, y: tripwire.wire.p2.y, z: 0 },
                        ];

                        const transformedWirePoints = transform2DWorldToImage(
                            tripwireWorldPoints,
                            sensorData.imageCoordinates,
                            sensorData.globalCoordinates
                        );

                        wireImageCoords = {
                            p1: transformedWirePoints[0],
                            p2: transformedWirePoints[1],
                        };

                        // Transform direction coordinates if available
                        if (tripwire.direction) {
                            const directionWorldPoints = [
                                { x: tripwire.direction.p1.x, y: tripwire.direction.p1.y, z: 0 },
                                { x: tripwire.direction.p2.x, y: tripwire.direction.p2.y, z: 0 },
                            ];

                            const transformedDirectionPoints = transform2DWorldToImage(
                                directionWorldPoints,
                                sensorData.imageCoordinates,
                                sensorData.globalCoordinates
                            );

                            directionImageCoords = {
                                p1: transformedDirectionPoints[0],
                                p2: transformedDirectionPoints[1],
                            };
                        }
                    } else {
                        // Use 3D reverse transformation
                        if (!reverseCalibrationInstance) {
                            LOG.warn('Reverse calibration instance not available for 3D transformation');
                            return;
                        }

                        const transformedTripwire = reverseCalibrationInstance.transformTripwireToImage(
                            {
                                p1: { x: tripwire.wire.p1.x, y: tripwire.wire.p1.y, z: 0 },
                                p2: { x: tripwire.wire.p2.x, y: tripwire.wire.p2.y, z: 0 },
                            },
                            sensor.sensorId
                        );

                        if (transformedTripwire) {
                            wireImageCoords = transformedTripwire;
                        } else {
                            LOG.warn(`Failed to transform tripwire ${tripwireId}`);
                            return;
                        }

                        // Transform direction coordinates if available
                        if (tripwire.direction) {
                            const transformedDirection = reverseCalibrationInstance.transformTripwireToImage(
                                {
                                    p1: { x: tripwire.direction.p1.x, y: tripwire.direction.p1.y, z: 0 },
                                    p2: { x: tripwire.direction.p2.x, y: tripwire.direction.p2.y, z: 0 },
                                },
                                sensor.sensorId
                            );

                            if (transformedDirection) {
                                directionImageCoords = transformedDirection;
                            } else {
                                LOG.warn(`Failed to transform tripwire direction ${tripwireId}`);
                            }
                        }
                    }

                    displayTripwires.push({
                        id: tripwire.id,
                        wire: wireImageCoords,
                        direction: directionImageCoords,
                    });
                } catch (error) {
                    LOG.error(`Failed to reverse transform Tripwire ${tripwireId}:`, error);
                }
            });

            setExistingTripwiresForDisplay(displayTripwires);
        },
        [calibrationData, sensor, reverseCalibrationInstance]
    );

    // Fetch calibration data when component mounts or sensor changes (for Live and Replay streams only)
    useEffect(() => {
        if (sensor && (streamType === StreamType.Live || streamType === StreamType.Replay)) {
            // Only fetch if we don't already have calibration data for this sensor
            if (!calibrationData || calibrationData.sensors.find(s => s.id === sensor.sensorId) === undefined) {
                handleFetchCalibrationData();
            }
        }
    }, [sensor, streamType, handleFetchCalibrationData, calibrationData]);

    // Handle window resize - reset in-progress drawing to avoid coordinate misalignment
    useEffect(() => {
        const handleResize = () => {
            // Only reset if user is actively drawing something
            const hasInProgressDrawing =
                roiPoints.length > 0 ||
                tempTripwireStart !== null ||
                tempDirectionStart !== null ||
                (drawingMode !== 'none' && drawingMode !== 'roi' && !tripwirePoints) ||
                (drawingMode === 'tripwire-direction' && !directionPoints);

            if (hasInProgressDrawing) {
                clearInProgressDrawing();
                enqueueSnackbar?.('Drawing reset due to screen resize. Please restart drawing.', {
                    variant: 'warning',
                });
            }
        };

        // Add resize listener with debounce to avoid too frequent calls
        let resizeTimeout: NodeJS.Timeout;
        const debouncedResize = () => {
            clearTimeout(resizeTimeout);
            resizeTimeout = setTimeout(handleResize, 300);
        };

        window.addEventListener('resize', debouncedResize);
        return () => {
            window.removeEventListener('resize', debouncedResize);
            clearTimeout(resizeTimeout);
        };
    }, [
        roiPoints.length,
        tempTripwireStart,
        tempDirectionStart,
        drawingMode,
        tripwirePoints,
        directionPoints,
        clearInProgressDrawing,
        enqueueSnackbar,
    ]);

    return {
        // State
        drawingMode,
        roiPoints,
        tripwirePoints,
        directionPoints,
        tempTripwireStart,
        tempDirectionStart,
        calibrationData,
        calibrationInstance,
        reverseCalibrationInstance,
        isLoadingCalibration,
        hasCalibrationError,
        showNameDialog,
        roiName,
        tripwireName,
        selectedROIIds,
        selectedTripwireIds,
        existingROIsForDisplay,
        existingTripwiresForDisplay,

        // Actions
        setDrawingMode,
        setShowNameDialog,
        setRoiName,
        setTripwireName,
        handleCanvasClick,
        handleFetchCalibrationData,
        submitDrawingData,
        clearAllDrawing,
        clearInProgressDrawing,
        handleNameDialogSubmit,

        // Transform functions
        handleTransformROI,
        handleTransformTripwire,

        // Existing items selection handlers
        handleROISelectionChange,
        handleTripwireSelectionChange,
    };
};
