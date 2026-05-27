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
import React, { useState, useEffect } from 'react';
import {
    Paper,
    Typography,
    Box,
    Alert,
    Card,
    CardContent,
    Button,
    Grid2,
    Divider,
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    TextField,
    IconButton,
} from '@mui/material';
import { Sensors, CloudUpload, Close as CloseIcon, CameraAlt, Image, Wallpaper, Info, Description, Upload } from '@mui/icons-material';
import { Project } from './types';
import nvAxios from '../../../services/Axios';
import config from '../../../config';
import { useExportFunctions } from './hooks/useExportFunctions';
import { type HomographyMatrix } from './utils/CalibrationJsonUtils';
import { flipPointY, convertLatLngToXYMatrix, convertProjectedPoint } from './utils/calibrationMath';
import { matrix, multiply } from 'mathjs';

interface CalibrationDataManagementProps {
    project: Project | null;
    selectedSensorId?: string | null; // Make this optional since we don't need it
}

// Define calibration JSON structure
interface CalibrationSensor {
    id: string;
    type: string;
    imageCoordinates: { x: number; y: number }[];
    globalCoordinates: { x: number; y: number }[];
    coordinates: { x: number; y: number };
    origin: { lat: number; lng: number };
    geoLocation: { lat: number; lng: number };
    scaleFactor: number;
    attributes: { name: string; value: string }[];
    rois: { id: string; roiCoordinates: { x: number; y: number }[] }[];
    place: { name: string; value: string | null }[]; // Allow null values like ReactJS
    tripwires: unknown[];
}

interface CalibrationJSON {
    version: string;
    osmURL: string;
    calibrationType: string;
    sensors: CalibrationSensor[];
    corridors?: unknown[]; // Make corridors optional to match ReactJS behavior
}

const CalibrationDataManagement: React.FC<CalibrationDataManagementProps> = ({ project }) => {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);
    const [webApiDialogOpen, setWebApiDialogOpen] = useState(false);
    const [webApiUrl, setWebApiUrl] = useState('');
    const [submittingWebApi, setSubmittingWebApi] = useState(false);
    const [loadingWebApiUrl, setLoadingWebApiUrl] = useState(false);
    const [upsertingCalibration, setUpsertingCalibration] = useState(false);

    // Use export functions hook
    const exportFunctions = useExportFunctions();

    // Helper function to replicate ReactJS getHomographyPolygon transformation exactly
    const transformPointsWithHomography = (
        points: { lat: number; lng: number }[],
        homographyMatrix: number[][],
        invImHeight: number
    ): { lat: number; lng: number }[] => {
        const homography = matrix(homographyMatrix);

        return points.map(point => {
            // Exactly replicate ReactJS getHomographyPolygon sequence:
            // 1. flipPointY(point, invImHeight)
            const flippedPoint = flipPointY(point, invImHeight);
            // 2. convertLatLngToXYMatrix(flippedPoint, invImHeight) - which does flipPointY again internally
            const matrixPoint = convertLatLngToXYMatrix(flippedPoint, invImHeight);
            // 3. Apply homography and convertProjectedPoint
            const projectedPoint = convertProjectedPoint(multiply(homography, matrixPoint));

            return projectedPoint;
        });
    };

    // Calculate sensor statistics
    const sensors = project?.sensor_set || [];
    const totalSensors = sensors.length;
    const calibratedSensors = sensors.filter(sensor => sensor.isCalibrated).length;
    const validatedSensors = sensors.filter(sensor => sensor.isCalibrated && sensor.isValidated).length;

    // Calculate intersection statistics (for geo calibration type)
    const projectWithIntersections = project as Project & {
        intersection_set?: Array<{ linksAreDrawn?: boolean; linksAreValid?: boolean }>;
    };
    const intersections = projectWithIntersections?.intersection_set || [];
    const totalIntersections = intersections.length;
    const completedIntersections = intersections.filter(intersection => intersection.linksAreDrawn && intersection.linksAreValid).length;

    useEffect(() => {
        setLoading(false);
    }, [project]);

    // Merge error and success states
    useEffect(() => {
        if (exportFunctions.error) {
            setError(exportFunctions.error);
            exportFunctions.setError(null); // Clear the hook's error after displaying
        }
    }, [exportFunctions.error]);

    useEffect(() => {
        if (exportFunctions.success) {
            setSuccess(exportFunctions.success);
            exportFunctions.setSuccess(null); // Clear the hook's success after displaying
        }
    }, [exportFunctions.success]);

    // Fetch webApiUrl from project data when dialog opens
    const handleOpenWebApiDialog = async () => {
        setWebApiDialogOpen(true);

        if (!project?.id) {
            setWebApiUrl(''); // fallback default
            return;
        }

        setLoadingWebApiUrl(true);
        try {
            const response = await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`);
            const projectData = response.data;

            // Set webApiUrl from project data, or use default if not found
            setWebApiUrl(projectData.webApiUrl || '');
        } catch (error) {
            console.error('Failed to fetch project data:', error);
            setWebApiUrl(''); // fallback default
        } finally {
            setLoadingWebApiUrl(false);
        }
    };

    // Generate calibration JSON for all calibrated sensors (like ReactJS version)
    const generateCalibrationJson = () => {
        if (!project) {
            setError('No project selected');
            return null;
        }

        const calibrationType = project.calibrationType as 'image' | 'cartesian' | 'geo';

        const calibrationJSON: CalibrationJSON = {
            version: '1.0',
            osmURL: project.mapFile || '',
            calibrationType,
            sensors: [] as CalibrationSensor[],
            // corridors omitted to match ReactJS behavior
        };

        // Process all calibrated and validated sensors (matching ReactJS logic)
        sensors.forEach(sensor => {
            if (sensor.isCalibrated && sensor.isValidated) {
                const { sensorId, originLat, originLng, scaleFactor } = sensor;
                const id = sensorId;

                // For cartesian calibration, (0,0) coordinates are valid as origin point
                // Only skip if coordinates are undefined/null, not if they're zero
                if (originLat === undefined || originLng === undefined || originLat === null || originLng === null) {
                    console.warn(`Skipping sensor ${sensorId} due to undefined coordinates: lat=${originLat}, lng=${originLng}`);
                    return;
                }

                const geoLocation = { lat: originLat, lng: originLng };

                if (calibrationType === 'image') {
                    // Image calibration logic
                    const imageCoordinates: { x: number; y: number }[] = [];
                    const globalCoordinates: { x: number; y: number }[] = [];

                    // Parse ROIs
                    const rois: { id: string; roiCoordinates: { x: number; y: number }[] }[] = [];
                    try {
                        const roiPolygons = JSON.parse(sensor.roiPolygon || '[]');
                        roiPolygons.forEach((roi: { points?: { lat: number; lng: number }[] }, index: number) => {
                            if (roi.points && Array.isArray(roi.points)) {
                                rois.push({
                                    id: `roi-id-${index + 1}`,
                                    roiCoordinates: roi.points.map((point: { lat: number; lng: number }) => ({
                                        x: point.lng,
                                        y: point.lat,
                                    })),
                                });
                            }
                        });
                    } catch (e) {
                        console.warn('Failed to parse ROI polygon:', e);
                    }

                    // Parse tripwires for image calibration
                    const tripwires: Array<{
                        id: string;
                        wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                        direction: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                    }> = [];
                    try {
                        const tripwireLines = JSON.parse(sensor.tripwireLines || '[]');
                        const tripDirLines = JSON.parse(sensor.tripDirLines || '[]');

                        tripwireLines.forEach((tripwire: { points?: { lat: number; lng: number }[] }, index: number) => {
                            if (tripwire.points && Array.isArray(tripwire.points) && tripwire.points.length >= 2) {
                                const direction = tripDirLines[index];
                                if (direction && direction.points && direction.points.length >= 2) {
                                    // For image calibration, use coordinates directly (lng -> x, lat -> y)
                                    tripwires.push({
                                        id: `tripwire-id-${index + 1}`,
                                        wire: {
                                            p1: { x: tripwire.points[0].lng, y: tripwire.points[0].lat },
                                            p2: { x: tripwire.points[1].lng, y: tripwire.points[1].lat },
                                        },
                                        direction: {
                                            p1: { x: direction.points[0].lng, y: direction.points[0].lat },
                                            p2: { x: direction.points[1].lng, y: direction.points[1].lat },
                                        },
                                    });
                                }
                            }
                        });
                    } catch (e) {
                        console.warn('Failed to parse tripwires:', e);
                    }

                    const attributes = [
                        { name: 'fps', value: sensor.fps && sensor.fps !== '0.0' && sensor.fps !== '0' ? sensor.fps : '30' },
                        { name: 'depth', value: sensor.depth || '' },
                        { name: 'fieldOfView', value: sensor.fieldOfView || '' },
                        { name: 'direction', value: sensor.direction || '' },
                        { name: 'source', value: sensor.mmsInfo_type || '' },
                        { name: 'frameWidth', value: sensor.width?.toString() || '0' },
                        { name: 'frameHeight', value: sensor.height?.toString() || '0' },
                    ];

                    const place = [
                        { name: 'city', value: project.cityPlace || 'Unknown' },
                        { name: 'building', value: project.name || 'Unknown' },
                        { name: 'room', value: project.roomPlace || 'Unknown' },
                    ];

                    // Ensure project has valid origin coordinates, fallback to sensor coordinates
                    const projectOrigin = {
                        lat: project.originLat && project.originLat !== 0 ? project.originLat : originLat,
                        lng: project.originLng && project.originLng !== 0 ? project.originLng : originLng,
                    };

                    calibrationJSON.sensors.push({
                        id,
                        type: 'camera',
                        imageCoordinates,
                        globalCoordinates,
                        coordinates: { x: 0, y: 0 },
                        origin: projectOrigin,
                        geoLocation,
                        scaleFactor: scaleFactor || 1,
                        attributes,
                        rois,
                        place,
                        tripwires,
                    });
                } else if (calibrationType === 'cartesian') {
                    // Cartesian calibration logic (matching ReactJS exactly)
                    let imageCoordinates: { x: number; y: number }[] = [];
                    let globalCoordinates: { x: number; y: number }[] = [];

                    // Parse sensor polygon for image coordinates
                    try {
                        const sensorPolygons = JSON.parse(sensor.sensorPolygon || '[]');
                        if (sensorPolygons.length > 0 && sensorPolygons[0].points) {
                            // Convert lat/lng to x/y (matching ReactJS convertLatLngToXY)
                            imageCoordinates = sensorPolygons[0].points.map((point: { lat: number; lng: number }) => ({
                                x: point.lng, // lng becomes x
                                y: point.lat, // lat becomes y
                            }));
                        }
                    } catch (e) {
                        console.warn('Failed to parse sensor polygon:', e);
                    }

                    // Parse edge lengths for global coordinates (matching ReactJS exactly)
                    try {
                        const edgeLengths = JSON.parse(sensor.edgeLengths || '[]');
                        if (Array.isArray(edgeLengths) && edgeLengths.length > 0) {
                            // Step 1: Convert lat/lng to x/y (ReactJS convertLatLngToXY function)
                            const tempCoordinates = edgeLengths.map((point: { lat: number; lng: number }) => ({
                                x: point.lng, // lng becomes x
                                y: point.lat, // lat becomes y
                            }));

                            // Step 2: Apply padding (ReactJS padCoordinates function)
                            const tempGlobalCoordinates = tempCoordinates.map((point: { x: number; y: number }) => ({
                                x: point.x + (sensor.invertImXPad || 0),
                                y: point.y + (sensor.invertImYPad || 0),
                            }));

                            // Step 3: Scale coordinates (ReactJS scaleCoordinates function - DIVISION by 100)
                            globalCoordinates = tempGlobalCoordinates.map((point: { x: number; y: number }) => ({
                                x: point.x / 100, // Note: DIVISION, not multiplication
                                y: point.y / 100, // Note: DIVISION, not multiplication
                            }));
                        } else {
                            console.warn(`Sensor ${sensorId} has no edge lengths data`);
                        }
                    } catch (e) {
                        console.warn('Failed to parse edge lengths:', e);
                    }

                    // Parse ROIs (matching ReactJS getCartesianPolygons)
                    const rois: { id: string; roiCoordinates: { x: number; y: number }[] }[] = [];
                    try {
                        const roiPolygons = JSON.parse(sensor.roiPolygon || '[]');

                        // Apply homography transformation (matching ReactJS behavior)
                        if (sensor.homography && sensor.invertImHeight) {
                            const homographyMatrix: HomographyMatrix = JSON.parse(sensor.homography) as number[][];

                            roiPolygons.forEach((roi: { points?: { lat: number; lng: number }[] }, index: number) => {
                                if (roi.points && Array.isArray(roi.points)) {
                                    // Apply exact ReactJS homography transformation sequence
                                    const transformedPoints = transformPointsWithHomography(
                                        roi.points,
                                        homographyMatrix,
                                        sensor.invertImHeight
                                    );

                                    // Scale by 100 (matching ReactJS scaleCoordinates function)
                                    const roiCoordinates = transformedPoints.map(point => ({
                                        x: point.lng / 100,
                                        y: point.lat / 100,
                                    }));

                                    rois.push({
                                        id: `roi-id-${index + 1}`,
                                        roiCoordinates,
                                    });
                                }
                            });
                        } else {
                            console.warn(`Sensor ${sensorId} missing homography or invertImHeight for ROI transformation`);
                        }
                    } catch (e) {
                        console.warn('Failed to parse ROI polygon:', e);
                    }

                    // Parse tripwires (matching ReactJS getCartesianPolygons)
                    const tripwires: Array<{
                        id: string;
                        wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                        direction: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                    }> = [];
                    try {
                        const tripwireLines = JSON.parse(sensor.tripwireLines || '[]');
                        const tripDirLines = JSON.parse(sensor.tripDirLines || '[]');

                        // Apply homography transformation to tripwires (matching ReactJS behavior)
                        if (sensor.homography && sensor.invertImHeight) {
                            const homographyMatrix: HomographyMatrix = JSON.parse(sensor.homography) as number[][];

                            tripwireLines.forEach((tripwire: { points?: { lat: number; lng: number }[] }, index: number) => {
                                if (tripwire.points && Array.isArray(tripwire.points) && tripwire.points.length >= 2) {
                                    const direction = tripDirLines[index];
                                    if (direction && direction.points && direction.points.length >= 2) {
                                        // Transform tripwire coordinates
                                        const transformedTripwire = transformPointsWithHomography(
                                            tripwire.points,
                                            homographyMatrix,
                                            sensor.invertImHeight
                                        );

                                        // Transform direction coordinates
                                        const transformedDirection = transformPointsWithHomography(
                                            direction.points,
                                            homographyMatrix,
                                            sensor.invertImHeight
                                        );

                                        tripwires.push({
                                            id: `tripwire-id-${index + 1}`,
                                            wire: {
                                                p1: { x: transformedTripwire[0].lng / 100, y: transformedTripwire[0].lat / 100 },
                                                p2: { x: transformedTripwire[1].lng / 100, y: transformedTripwire[1].lat / 100 },
                                            },
                                            direction: {
                                                p1: { x: transformedDirection[0].lng / 100, y: transformedDirection[0].lat / 100 },
                                                p2: { x: transformedDirection[1].lng / 100, y: transformedDirection[1].lat / 100 },
                                            },
                                        });
                                    }
                                }
                            });
                        } else {
                            console.warn(`Sensor ${sensorId} missing homography or invertImHeight for tripwire transformation`);
                        }
                    } catch (e) {
                        console.warn('Failed to parse tripwires:', e);
                    }

                    const attributes = [
                        { name: 'fps', value: sensor.fps || '30' }, // Don't override original fps value
                        { name: 'depth', value: sensor.depth || '' },
                        { name: 'fieldOfView', value: sensor.fieldOfView || '' },
                        { name: 'direction', value: sensor.direction || '' },
                        { name: 'source', value: sensor.mmsInfo_type || '' },
                        { name: 'frameWidth', value: sensor.width?.toString() || '0' },
                        { name: 'frameHeight', value: sensor.height?.toString() || '0' },
                    ];

                    // Fix place values to match ReactJS (use actual project values, not defaults)
                    const place = [
                        { name: 'city', value: project.cityPlace || 'Unknown' },
                        { name: 'building', value: project.name || 'Unknown' },
                        { name: 'room', value: project.roomPlace || 'Unknown' },
                    ];

                    // Ensure project has valid origin coordinates, fallback to sensor coordinates
                    const projectOrigin = {
                        lat: project.originLat && project.originLat !== 0 ? project.originLat : originLat,
                        lng: project.originLng && project.originLng !== 0 ? project.originLng : originLng,
                    };

                    calibrationJSON.sensors.push({
                        id,
                        type: 'camera',
                        imageCoordinates,
                        globalCoordinates,
                        origin: projectOrigin,
                        geoLocation,
                        coordinates: { x: 0, y: 0 },
                        scaleFactor: 100,
                        rois,
                        place,
                        tripwires,
                        attributes,
                    });
                }
            }
        });

        return calibrationJSON;
    };

    // Generate calibration JSON for upsert operation (specific format)
    const generateUpsertCalibrationJson = () => {
        if (!project) {
            setError('No project selected');
            return null;
        }

        const calibrationType = project.calibrationType as 'image' | 'cartesian' | 'geo';

        interface UpsertSensor {
            id: string;
            type: string;
            imageCoordinates: { x: number; y: number }[];
            globalCoordinates: { x: number; y: number }[];
            origin: { lat: number; lng: number };
            geoLocation: { lat: number; lng: number };
            coordinates: { x: number; y: number };
            scaleFactor: number;
            rois: { id: string; roiCoordinates: { x: number; y: number }[] }[];
            place: { name: string; value: string }[];
            tripwires: Array<{
                id: string;
                wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                direction: { p1: { x: number; y: number }; p2: { x: number; y: number } };
            }>;
            attributes: { name: string; value: string }[];
        }

        const upsertCalibrationJSON = {
            version: '1.0',
            osmURL: project.mapFile || '',
            calibrationType,
            sensors: [] as UpsertSensor[],
        };

        // Process all calibrated and validated sensors
        sensors.forEach(sensor => {
            if (sensor.isCalibrated && sensor.isValidated) {
                const { sensorId, originLat, originLng } = sensor;
                const id = sensorId;

                // For cartesian calibration, (0,0) coordinates are valid as origin point
                // Only skip if coordinates are undefined/null, not if they're zero
                if (originLat === undefined || originLng === undefined || originLat === null || originLng === null) {
                    console.warn(`Skipping sensor ${sensorId} due to undefined coordinates: lat=${originLat}, lng=${originLng}`);
                    return;
                }

                const geoLocation = { lat: originLat, lng: originLng };

                if (calibrationType === 'cartesian') {
                    // Cartesian calibration logic for upsert format
                    let imageCoordinates: { x: number; y: number }[] = [];
                    let globalCoordinates: { x: number; y: number }[] = [];

                    // Parse sensor polygon for image coordinates
                    try {
                        const sensorPolygons = JSON.parse(sensor.sensorPolygon || '[]');
                        if (sensorPolygons.length > 0 && sensorPolygons[0].points) {
                            imageCoordinates = sensorPolygons[0].points.map((point: { lat: number; lng: number }) => ({
                                x: point.lng,
                                y: point.lat,
                            }));
                        }
                    } catch (e) {
                        console.warn('Failed to parse sensor polygon:', e);
                    }

                    // Parse edge lengths for global coordinates
                    try {
                        const edgeLengths = JSON.parse(sensor.edgeLengths || '[]');
                        if (Array.isArray(edgeLengths) && edgeLengths.length > 0) {
                            const tempCoordinates = edgeLengths.map((point: { lat: number; lng: number }) => ({
                                x: point.lng,
                                y: point.lat,
                            }));

                            const tempGlobalCoordinates = tempCoordinates.map((point: { x: number; y: number }) => ({
                                x: point.x + (sensor.invertImXPad || 0),
                                y: point.y + (sensor.invertImYPad || 0),
                            }));

                            globalCoordinates = tempGlobalCoordinates.map((point: { x: number; y: number }) => ({
                                x: point.x / 100,
                                y: point.y / 100,
                            }));
                        } else {
                            console.warn(`Sensor ${sensorId} has no edge lengths data`);
                        }
                    } catch (e) {
                        console.warn('Failed to parse edge lengths:', e);
                    }

                    // Parse ROIs (matching ReactJS getCartesianPolygons)
                    const rois: { id: string; roiCoordinates: { x: number; y: number }[] }[] = [];
                    try {
                        const roiPolygons = JSON.parse(sensor.roiPolygon || '[]');

                        // Apply homography transformation (matching ReactJS behavior)
                        if (sensor.homography && sensor.invertImHeight) {
                            const homographyMatrix: HomographyMatrix = JSON.parse(sensor.homography) as number[][];

                            roiPolygons.forEach((roi: { points?: { lat: number; lng: number }[] }, index: number) => {
                                if (roi.points && Array.isArray(roi.points)) {
                                    // Apply exact ReactJS homography transformation sequence
                                    const transformedPoints = transformPointsWithHomography(
                                        roi.points,
                                        homographyMatrix,
                                        sensor.invertImHeight
                                    );

                                    // Scale by 100 (matching ReactJS scaleCoordinates function)
                                    const roiCoordinates = transformedPoints.map(point => ({
                                        x: point.lng / 100,
                                        y: point.lat / 100,
                                    }));

                                    rois.push({
                                        id: `roi-id-${index + 1}`,
                                        roiCoordinates,
                                    });
                                }
                            });
                        } else {
                            console.warn(`Sensor ${sensorId} missing homography or invertImHeight for ROI transformation`);
                        }
                    } catch (e) {
                        console.warn('Failed to parse ROI polygon:', e);
                    }

                    // Parse tripwires (matching ReactJS getCartesianPolygons)
                    const tripwires: Array<{
                        id: string;
                        wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                        direction: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                    }> = [];
                    try {
                        const tripwireLines = JSON.parse(sensor.tripwireLines || '[]');
                        const tripDirLines = JSON.parse(sensor.tripDirLines || '[]');

                        // Apply homography transformation to tripwires (matching ReactJS behavior)
                        if (sensor.homography && sensor.invertImHeight) {
                            const homographyMatrix: HomographyMatrix = JSON.parse(sensor.homography) as number[][];

                            tripwireLines.forEach((tripwire: { points?: { lat: number; lng: number }[] }, index: number) => {
                                if (tripwire.points && Array.isArray(tripwire.points) && tripwire.points.length >= 2) {
                                    const direction = tripDirLines[index];
                                    if (direction && direction.points && direction.points.length >= 2) {
                                        // Transform tripwire coordinates
                                        const transformedTripwire = transformPointsWithHomography(
                                            tripwire.points,
                                            homographyMatrix,
                                            sensor.invertImHeight
                                        );

                                        // Transform direction coordinates
                                        const transformedDirection = transformPointsWithHomography(
                                            direction.points,
                                            homographyMatrix,
                                            sensor.invertImHeight
                                        );

                                        tripwires.push({
                                            id: `tripwire-id-${index + 1}`,
                                            wire: {
                                                p1: { x: transformedTripwire[0].lng / 100, y: transformedTripwire[0].lat / 100 },
                                                p2: { x: transformedTripwire[1].lng / 100, y: transformedTripwire[1].lat / 100 },
                                            },
                                            direction: {
                                                p1: { x: transformedDirection[0].lng / 100, y: transformedDirection[0].lat / 100 },
                                                p2: { x: transformedDirection[1].lng / 100, y: transformedDirection[1].lat / 100 },
                                            },
                                        });
                                    }
                                }
                            });
                        } else {
                            console.warn(`Sensor ${sensorId} missing homography or invertImHeight for tripwire transformation`);
                        }
                    } catch (e) {
                        console.warn('Failed to parse tripwires:', e);
                    }

                    const attributes = [
                        { name: 'fps', value: sensor.fps || '30' }, // Don't override original fps value
                        { name: 'depth', value: sensor.depth || '' },
                        { name: 'fieldOfView', value: sensor.fieldOfView || '' },
                        { name: 'direction', value: sensor.direction || '' },
                        { name: 'source', value: sensor.mmsInfo_type || 'vst' },
                        { name: 'frameWidth', value: sensor.width?.toString() || '1920' },
                        { name: 'frameHeight', value: sensor.height?.toString() || '1080' },
                    ];

                    const place = [
                        { name: 'city', value: project.cityPlace || 'Unknown' },
                        { name: 'building', value: project.name || 'Unknown' },
                        { name: 'room', value: project.roomPlace || 'Unknown' },
                    ];

                    // Ensure project has valid origin coordinates, fallback to sensor coordinates
                    const projectOrigin = {
                        lat: project.originLat && project.originLat !== 0 ? project.originLat : originLat,
                        lng: project.originLng && project.originLng !== 0 ? project.originLng : originLng,
                    };

                    upsertCalibrationJSON.sensors.push({
                        id,
                        type: 'camera',
                        imageCoordinates,
                        globalCoordinates,
                        origin: projectOrigin,
                        geoLocation,
                        coordinates: { x: 0, y: 0 },
                        scaleFactor: 100,
                        rois,
                        place,
                        tripwires,
                        attributes,
                    });
                } else if (calibrationType === 'image') {
                    // Image calibration logic for upsert format
                    const imageCoordinates: { x: number; y: number }[] = [];
                    const globalCoordinates: { x: number; y: number }[] = [];

                    // Parse ROIs
                    const rois: { id: string; roiCoordinates: { x: number; y: number }[] }[] = [];
                    try {
                        const roiPolygons = JSON.parse(sensor.roiPolygon || '[]');
                        roiPolygons.forEach((roi: { points?: { lat: number; lng: number }[] }, index: number) => {
                            if (roi.points && Array.isArray(roi.points)) {
                                rois.push({
                                    id: `roi-id-${index + 1}`,
                                    roiCoordinates: roi.points.map((point: { lat: number; lng: number }) => ({
                                        x: point.lng,
                                        y: point.lat,
                                    })),
                                });
                            }
                        });
                    } catch (e) {
                        console.warn('Failed to parse ROI polygon:', e);
                    }

                    // Parse tripwires for image calibration (upsert format)
                    const tripwires: Array<{
                        id: string;
                        wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                        direction: { p1: { x: number; y: number }; p2: { x: number; y: number } };
                    }> = [];
                    try {
                        const tripwireLines = JSON.parse(sensor.tripwireLines || '[]');
                        const tripDirLines = JSON.parse(sensor.tripDirLines || '[]');

                        tripwireLines.forEach((tripwire: { points?: { lat: number; lng: number }[] }, index: number) => {
                            if (tripwire.points && Array.isArray(tripwire.points) && tripwire.points.length >= 2) {
                                const direction = tripDirLines[index];
                                if (direction && direction.points && direction.points.length >= 2) {
                                    // For image calibration, use coordinates directly (lng -> x, lat -> y)
                                    tripwires.push({
                                        id: `tripwire-id-${index + 1}`,
                                        wire: {
                                            p1: { x: tripwire.points[0].lng, y: tripwire.points[0].lat },
                                            p2: { x: tripwire.points[1].lng, y: tripwire.points[1].lat },
                                        },
                                        direction: {
                                            p1: { x: direction.points[0].lng, y: direction.points[0].lat },
                                            p2: { x: direction.points[1].lng, y: direction.points[1].lat },
                                        },
                                    });
                                }
                            }
                        });
                    } catch (e) {
                        console.warn('Failed to parse tripwires:', e);
                    }

                    const attributes = [
                        { name: 'fps', value: sensor.fps && sensor.fps !== '0.0' && sensor.fps !== '0' ? sensor.fps : '30' },
                        { name: 'depth', value: sensor.depth || '' },
                        { name: 'fieldOfView', value: sensor.fieldOfView || '' },
                        { name: 'direction', value: sensor.direction || '' },
                        { name: 'source', value: sensor.mmsInfo_type || 'vst' },
                        { name: 'frameWidth', value: sensor.width?.toString() || '1920' },
                        { name: 'frameHeight', value: sensor.height?.toString() || '1080' },
                    ];

                    const place = [
                        { name: 'city', value: project.cityPlace || 'Unknown' },
                        { name: 'building', value: project.name || 'Unknown' },
                        { name: 'room', value: project.roomPlace || 'Unknown' },
                    ];

                    // Ensure project has valid origin coordinates, fallback to sensor coordinates
                    const projectOrigin = {
                        lat: project.originLat && project.originLat !== 0 ? project.originLat : originLat,
                        lng: project.originLng && project.originLng !== 0 ? project.originLng : originLng,
                    };

                    upsertCalibrationJSON.sensors.push({
                        id,
                        type: 'camera',
                        imageCoordinates,
                        globalCoordinates,
                        coordinates: { x: 0, y: 0 },
                        origin: projectOrigin,
                        geoLocation,
                        scaleFactor: sensor.scaleFactor || 1,
                        attributes,
                        rois,
                        place,
                        tripwires,
                    });
                } else {
                    // For other calibration types, use similar logic but adjust as needed
                    console.warn(`Upsert calibration not fully implemented for calibration type: ${calibrationType}`);
                }
            }
        });

        return upsertCalibrationJSON;
    };

    // Save JSON to backend
    const saveJsonToBackend = async (calibrationJson: CalibrationJSON) => {
        if (!project?.id) return;

        try {
            await nvAxios.patch(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`, {
                calibrationJson: JSON.stringify(calibrationJson),
            });
        } catch (error) {
            console.error('Error saving calibration JSON to backend:', error);
        }
    };

    // Download JSON file
    const downloadJson = (jsonObject: CalibrationJSON, filename: string) => {
        const blob = new Blob([JSON.stringify(jsonObject, null, 2)], {
            type: 'application/json',
        });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;

        const clickHandler = () => {
            setTimeout(() => {
                URL.revokeObjectURL(url);
                a.removeEventListener('click', clickHandler);
            }, 150);
        };

        a.addEventListener('click', clickHandler, false);
        a.click();
    };

    // Handle export (matching ReactJS getCalibrationJSON)
    const handleExportCalibrations = async () => {
        setError(null);
        setSuccess(null);

        const calibrationJson = generateCalibrationJson();

        if (calibrationJson) {
            if (calibrationJson.sensors.length === 0) {
                setError('No valid sensors found for export. Please ensure sensors have valid coordinates and calibration data.');
                return;
            }

            try {
                // Save to backend
                await saveJsonToBackend(calibrationJson);

                // Download the file
                downloadJson(calibrationJson, 'calibration.json');

                setSuccess(`Calibration JSON exported and downloaded successfully! (${calibrationJson.sensors.length} sensors exported)`);
            } catch (error) {
                setError('Export successful, but failed to save to backend. Check console for details.');

                // Still download the file even if backend save fails
                downloadJson(calibrationJson, 'calibration.json');
            }
        }
    };

    // Handle save web API URL
    const handleSaveWebApiUrl = async () => {
        if (!project?.id) {
            setError('No project ID available');
            return;
        }

        setSubmittingWebApi(true);
        setError(null);
        setSuccess(null);
        try {
            await nvAxios.patch(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`, {
                webApiUrl: webApiUrl,
            });
            setSuccess('Web API URL saved successfully!');
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
            setError(`Failed to save Web API URL: ${errorMessage}`);
        } finally {
            setSubmittingWebApi(false);
        }
    };

    // Handle upload data workflow
    const handleUploadData = async () => {
        if (!project?.id) {
            setError('No project ID available');
            return;
        }

        setSubmittingWebApi(true);
        setError(null);
        setSuccess(null);
        try {
            // Step 1: Save web API URL
            await nvAxios.patch(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`, {
                webApiUrl: webApiUrl,
            });

            // Step 2: Get project data
            await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/projects/${project.id}/`);

            // Step 3: Upload web API
            await nvAxios.get(`${config.analyticsUIServerEndpoint}/api/uploadWebApi/${project.id}/`);

            setSuccess('Calibration data uploaded successfully via Web API!');
            setWebApiDialogOpen(false);
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
            setError(`Failed to upload data: ${errorMessage}`);
        } finally {
            setSubmittingWebApi(false);
        }
    };

    // Handle upsert calibration
    const handleUpsertCalibration = async () => {
        if (!project?.id) {
            setError('No project ID available');
            return;
        }

        setUpsertingCalibration(true);
        setError(null);
        setSuccess(null);

        try {
            const calibrationJson = generateUpsertCalibrationJson();

            if (!calibrationJson) {
                setError('Failed to generate calibration JSON');
                return;
            }

            if (calibrationJson.sensors.length === 0) {
                setError('No valid sensors found for upload. Please ensure sensors have valid coordinates and calibration data.');
                return;
            }

            // Create FormData to match the curl request format
            const formData = new FormData();
            const jsonBlob = new Blob([JSON.stringify(calibrationJson, null, 2)], {
                type: 'application/json',
            });
            formData.append('configFiles', jsonBlob, 'calibration.json');
            console.log('Calibration Json: ', calibrationJson);
            // Safely print the calibration JSON object
            try {
                console.log('Calibration JSON (formatted):', JSON.stringify(calibrationJson, null, 2));
            } catch (error) {
                console.error('Error printing calibration JSON:', error);
                console.log('Calibration JSON (raw):', calibrationJson);
            }
            // Send to the MDat Web API endpoint using nvAxios
            await nvAxios.post(`${config.mdatWebApiEndpoint}/config/upload-file/calibration`, formData, {
                headers: {
                    'Content-Type': 'multipart/form-data',
                },
            });

            setSuccess(`Calibration data upserted successfully Web API! (${calibrationJson.sensors.length} sensors uploaded)`);
        } catch (error) {
            const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
            setError(`Failed to upsert calibration: ${errorMessage}`);
        } finally {
            setUpsertingCalibration(false);
        }
    };

    if (loading) {
        return (
            <Paper sx={{ p: 3 }}>
                <Typography>Loading project data...</Typography>
            </Paper>
        );
    }

    if (!project) {
        return (
            <Paper sx={{ p: 3 }}>
                <Alert severity='warning'>Please select a project to export calibration data.</Alert>
            </Paper>
        );
    }

    return (
        <Box sx={{ maxWidth: 1200, mx: 'auto', p: 3 }}>
            {/* Header */}
            <Paper sx={{ p: 3, mb: 3 }}>
                <Typography variant='h4' gutterBottom>
                    Export Calibration Data
                </Typography>
                <Typography variant='body1' color='text.secondary'>
                    Project: <strong>{project.name}</strong> | Calibration Type: <strong>{project.calibrationType}</strong>
                </Typography>
            </Paper>

            {/* Success/Error Messages */}
            {error && (
                <Alert severity='error' onClose={() => setError(null)} sx={{ mb: 3 }}>
                    {error}
                </Alert>
            )}
            {success && (
                <Alert severity='success' onClose={() => setSuccess(null)} sx={{ mb: 3 }}>
                    {success}
                </Alert>
            )}

            {/* Actions Grid - Matching ReactJS ProjectPage layout */}
            <Paper sx={{ p: 3 }}>
                <Typography variant='h5' gutterBottom>
                    Actions
                </Typography>
                <Divider sx={{ mb: 3 }} />

                {/* Export Information First */}
                <Box sx={{ mb: 4, p: 3, bgcolor: 'background.default', borderRadius: 1 }}>
                    <Typography variant='h6' gutterBottom>
                        Export Information
                    </Typography>
                    <Grid2 container spacing={2}>
                        <Grid2 size={{ xs: 12, sm: 6, md: 3 }}>
                            <Typography variant='body2' color='text.secondary'>
                                Total Sensors:
                            </Typography>
                            <Typography variant='h6'>{totalSensors}</Typography>
                        </Grid2>
                        <Grid2 size={{ xs: 12, sm: 6, md: 3 }}>
                            <Typography variant='body2' color='text.secondary'>
                                Calibrated:
                            </Typography>
                            <Typography variant='h6' color='warning.main'>
                                {calibratedSensors}
                            </Typography>
                        </Grid2>
                        <Grid2 size={{ xs: 12, sm: 6, md: 3 }}>
                            <Typography variant='body2' color='text.secondary'>
                                Validated:
                            </Typography>
                            <Typography variant='h6' color='success.main'>
                                {validatedSensors}
                            </Typography>
                        </Grid2>
                        <Grid2 size={{ xs: 12, sm: 6, md: 3 }}>
                            <Typography variant='body2' color='text.secondary'>
                                Calibration Type:
                            </Typography>
                            <Typography variant='h6'>{project.calibrationType}</Typography>
                        </Grid2>
                    </Grid2>
                </Box>

                {/* First Row of Export Cards */}
                <Grid2 container spacing={3} sx={{ mb: 3 }}>
                    {/* Sensors Export */}
                    <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                        <Card sx={{ height: '100%', textAlign: 'center' }}>
                            <CardContent sx={{ p: 3 }}>
                                <Sensors sx={{ fontSize: 40, color: 'primary.main', mb: 2 }} />
                                <Typography variant='h5' gutterBottom>
                                    SENSORS
                                </Typography>
                                <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                    {totalSensors} sensors, {validatedSensors} completed
                                </Typography>

                                <Button
                                    variant='contained'
                                    size='large'
                                    fullWidth
                                    color='success'
                                    onClick={handleExportCalibrations}
                                    disabled={validatedSensors === 0}
                                    sx={{ py: 1.5 }}
                                >
                                    Export Sensor Calibrations
                                </Button>

                                {validatedSensors === 0 && (
                                    <Typography variant='body2' color='text.secondary' sx={{ mt: 2 }}>
                                        No calibrated and validated sensors found
                                    </Typography>
                                )}
                            </CardContent>
                        </Card>
                    </Grid2>

                    {/* Cameras Export */}
                    <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                        <Card sx={{ height: '100%', textAlign: 'center' }}>
                            <CardContent sx={{ p: 3 }}>
                                <CameraAlt sx={{ fontSize: 40, color: 'secondary.main', mb: 2 }} />
                                <Typography variant='h5' gutterBottom>
                                    CAMERAS
                                </Typography>
                                <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                    {totalSensors} sensors, {validatedSensors} completed
                                </Typography>

                                <Button
                                    variant='contained'
                                    size='large'
                                    fullWidth
                                    color='success'
                                    onClick={() => project && exportFunctions.exportSensorDetails(project)}
                                    disabled={validatedSensors === 0 || exportFunctions.loading}
                                    sx={{ py: 1.5 }}
                                >
                                    Export Sensor Details
                                </Button>

                                {validatedSensors === 0 && (
                                    <Typography variant='body2' color='text.secondary' sx={{ mt: 2 }}>
                                        No calibrated sensors to export
                                    </Typography>
                                )}
                            </CardContent>
                        </Card>
                    </Grid2>

                    {/* Intersections Export - Only for geo calibration */}
                    {project.calibrationType === 'geo' && (
                        <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                            <Card sx={{ height: '100%', textAlign: 'center' }}>
                                <CardContent sx={{ p: 3 }}>
                                    <Info sx={{ fontSize: 40, color: 'info.main', mb: 2 }} />
                                    <Typography variant='h5' gutterBottom>
                                        INTERSECTIONS
                                    </Typography>
                                    <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                        {totalIntersections} intersections, {completedIntersections} completed
                                    </Typography>

                                    <Button
                                        variant='contained'
                                        size='large'
                                        fullWidth
                                        color='success'
                                        onClick={() => project && exportFunctions.exportNetworkJSON(project)}
                                        disabled={completedIntersections === 0 || exportFunctions.loading}
                                        sx={{ py: 1.5 }}
                                    >
                                        Export Intersection Road Networks
                                    </Button>

                                    {completedIntersections === 0 && (
                                        <Typography variant='body2' color='text.secondary' sx={{ mt: 2 }}>
                                            No completed intersections to export
                                        </Typography>
                                    )}
                                </CardContent>
                            </Card>
                        </Grid2>
                    )}

                    {/* Warped Images Export - Only for cartesian calibration */}
                    {project.calibrationType === 'cartesian' && (
                        <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                            <Card sx={{ height: '100%', textAlign: 'center' }}>
                                <CardContent sx={{ p: 3 }}>
                                    <Wallpaper sx={{ fontSize: 40, color: 'warning.main', mb: 2 }} />
                                    <Typography variant='h5' gutterBottom>
                                        Warped Images
                                    </Typography>
                                    <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                        {totalSensors} sensors, {validatedSensors} completed
                                    </Typography>

                                    <Button
                                        variant='contained'
                                        size='large'
                                        fullWidth
                                        color='success'
                                        onClick={() => project && exportFunctions.exportWarpedImages(project)}
                                        disabled={validatedSensors === 0 || exportFunctions.loading}
                                        sx={{ py: 1.5 }}
                                    >
                                        Download Warped
                                    </Button>

                                    {validatedSensors === 0 && (
                                        <Typography variant='body2' color='text.secondary' sx={{ mt: 2 }}>
                                            No warped images to download
                                        </Typography>
                                    )}
                                </CardContent>
                            </Card>
                        </Grid2>
                    )}
                </Grid2>

                {/* Second Row of Export Cards */}
                <Grid2 container spacing={3}>
                    {/* Get Images */}
                    <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                        <Card sx={{ height: '100%', textAlign: 'center' }}>
                            <CardContent sx={{ p: 3 }}>
                                <Image sx={{ fontSize: 40, color: 'info.main', mb: 2 }} />
                                <Typography variant='h5' gutterBottom>
                                    Get Images
                                </Typography>
                                <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                    &nbsp;
                                </Typography>

                                <Button
                                    variant='contained'
                                    size='large'
                                    fullWidth
                                    color='success'
                                    onClick={() => project && exportFunctions.exportImages(project)}
                                    disabled={exportFunctions.loading}
                                    sx={{ py: 1.5 }}
                                >
                                    Download Images
                                </Button>
                            </CardContent>
                        </Card>
                    </Grid2>

                    {/* Get Image Metadata */}
                    <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                        <Card sx={{ height: '100%', textAlign: 'center' }}>
                            <CardContent sx={{ p: 3 }}>
                                <Description sx={{ fontSize: 40, color: 'error.main', mb: 2 }} />
                                <Typography variant='h5' gutterBottom>
                                    Get Image Metadata
                                </Typography>
                                <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                    &nbsp;
                                </Typography>

                                <Button
                                    variant='contained'
                                    size='large'
                                    fullWidth
                                    color='success'
                                    onClick={() => project && exportFunctions.exportImageMetadata(project)}
                                    disabled={exportFunctions.loading}
                                    sx={{ py: 1.5 }}
                                >
                                    Download Image Metadata
                                </Button>
                            </CardContent>
                        </Card>
                    </Grid2>

                    {/* Export to MDX WEB/API */}
                    <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                        <Card sx={{ height: '100%', textAlign: 'center' }}>
                            <CardContent sx={{ p: 3 }}>
                                <CloudUpload sx={{ fontSize: 40, color: 'success.main', mb: 2 }} />
                                <Typography variant='h5' gutterBottom>
                                    Export to MDX WEB/API
                                </Typography>
                                <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                    &nbsp;
                                </Typography>

                                <Button
                                    variant='contained'
                                    size='large'
                                    fullWidth
                                    color='success'
                                    onClick={handleOpenWebApiDialog}
                                    disabled={validatedSensors === 0}
                                    sx={{ py: 1.5 }}
                                >
                                    Upload to Web/API
                                </Button>

                                {validatedSensors === 0 && (
                                    <Typography variant='body2' color='text.secondary' sx={{ mt: 2 }}>
                                        No calibrated sensors to submit
                                    </Typography>
                                )}
                            </CardContent>
                        </Card>
                    </Grid2>

                    {/* Upsert Calibration */}
                    <Grid2 size={{ xs: 12, md: 6, lg: 4 }}>
                        <Card sx={{ height: '100%', textAlign: 'center' }}>
                            <CardContent sx={{ p: 3 }}>
                                <Upload sx={{ fontSize: 40, color: 'secondary.main', mb: 2 }} />
                                <Typography variant='h5' gutterBottom>
                                    Upsert Calibration
                                </Typography>
                                <Typography variant='h6' color='text.secondary' sx={{ mb: 3 }}>
                                    Send calibration data to MDat Web API
                                </Typography>

                                <Button
                                    variant='contained'
                                    size='large'
                                    fullWidth
                                    color='primary'
                                    onClick={handleUpsertCalibration}
                                    disabled={validatedSensors === 0 || upsertingCalibration}
                                    sx={{ py: 1.5 }}
                                >
                                    {upsertingCalibration ? 'Upserting...' : 'Upsert Calibration'}
                                </Button>

                                {validatedSensors === 0 && (
                                    <Typography variant='body2' color='text.secondary' sx={{ mt: 2 }}>
                                        No calibrated sensors to upsert
                                    </Typography>
                                )}
                            </CardContent>
                        </Card>
                    </Grid2>
                </Grid2>
            </Paper>

            {/* Web API Submission Dialog */}
            <Dialog
                open={webApiDialogOpen}
                onClose={() => setWebApiDialogOpen(false)}
                maxWidth='sm'
                fullWidth
                PaperProps={{
                    sx: {
                        borderRadius: 2,
                        boxShadow: theme => theme.shadows[8],
                    },
                }}
            >
                <DialogTitle
                    sx={{
                        pb: 1,
                        borderBottom: theme => `1px solid ${theme.palette.divider}`,
                        bgcolor: 'background.paper',
                    }}
                >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                        <CloudUpload sx={{ color: 'primary.main', fontSize: 28 }} />
                        <Typography variant='h6' component='div' sx={{ fontWeight: 600 }}>
                            Metropolis Web API Server Configuration
                        </Typography>
                    </Box>
                    <IconButton
                        aria-label='close'
                        onClick={() => setWebApiDialogOpen(false)}
                        sx={{
                            position: 'absolute',
                            right: 12,
                            top: 12,
                            color: 'grey.500',
                            '&:hover': {
                                color: 'grey.700',
                                bgcolor: 'grey.100',
                            },
                        }}
                    >
                        <CloseIcon />
                    </IconButton>
                </DialogTitle>
                <DialogContent sx={{ pt: 3, pb: 2 }}>
                    <Box sx={{ mb: 2 }}>
                        <Typography variant='body2' color='text.secondary' sx={{ mb: 3 }}>
                            Configure the Metropolis Web API server address to upload calibration data. You can save the URL for future use
                            or upload data directly to the server.
                        </Typography>
                    </Box>

                    <TextField
                        label='Metropolis Web API Server Address'
                        fullWidth
                        margin='normal'
                        value={webApiUrl}
                        onChange={e => setWebApiUrl(e.target.value)}
                        helperText={
                            loadingWebApiUrl ? 'Loading current Web API URL...' : 'Enter the server address (e.g., http://<ip>:8081)'
                        }
                        disabled={loadingWebApiUrl}
                        variant='outlined'
                        sx={{
                            mb: 2,
                            '& .MuiOutlinedInput-root': {
                                borderRadius: 1,
                            },
                        }}
                    />

                    <Alert
                        severity='info'
                        sx={{
                            mt: 2,
                            borderRadius: 1,
                            '& .MuiAlert-message': {
                                fontSize: '0.875rem',
                            },
                        }}
                    >
                        <Typography variant='body2'>
                            <strong>Save Web API URL:</strong> Store the server address in project settings
                            <br />
                            <strong>Upload Data:</strong> Send calibration data to the configured server
                        </Typography>
                    </Alert>
                </DialogContent>
                <DialogActions
                    sx={{
                        px: 3,
                        pb: 3,
                        pt: 1,
                        gap: 1,
                        borderTop: theme => `1px solid ${theme.palette.divider}`,
                        bgcolor: 'background.default',
                    }}
                >
                    <Button
                        onClick={handleSaveWebApiUrl}
                        variant='outlined'
                        disabled={submittingWebApi || loadingWebApiUrl}
                        sx={{
                            minWidth: 140,
                            borderRadius: 1,
                        }}
                    >
                        {submittingWebApi ? 'Saving...' : 'Save Web API URL'}
                    </Button>
                    <Button
                        onClick={handleUploadData}
                        variant='contained'
                        color='primary'
                        disabled={submittingWebApi || loadingWebApiUrl || !webApiUrl.trim()}
                        sx={{
                            minWidth: 120,
                            borderRadius: 1,
                            fontWeight: 600,
                        }}
                    >
                        {submittingWebApi ? 'Uploading...' : 'Upload Data'}
                    </Button>
                    <Button
                        onClick={() => setWebApiDialogOpen(false)}
                        variant='text'
                        disabled={submittingWebApi || loadingWebApiUrl}
                        sx={{
                            minWidth: 80,
                            color: 'text.secondary',
                            '&:hover': {
                                bgcolor: 'action.hover',
                            },
                        }}
                    >
                        Close
                    </Button>
                </DialogActions>
            </Dialog>
        </Box>
    );
};

export default CalibrationDataManagement;
