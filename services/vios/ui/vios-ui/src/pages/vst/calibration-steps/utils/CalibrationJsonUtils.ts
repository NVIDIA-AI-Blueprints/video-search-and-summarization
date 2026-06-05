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
import { matrix, multiply, Matrix } from 'mathjs';
import { flipPointY } from './calibrationMath';

// Interface for JSON coordinates
interface JsonCoordinate {
    x: number;
    y: number;
}

// Interface for internal point format
interface Point {
    lat: number;
    lng: number;
}

// Interface for homography transformation
export interface HomographyMatrix extends Array<number[]> {}

// Interface for imported JSON data structure
interface ImportedCalibrationJson {
    version?: string;
    osmURL?: string;
    calibrationType?: string;
    sensors?: ImportedSensor[];
    corridors?: ImportedCorridor[];
    metadata?: {
        exportDate?: string;
        projectName?: string;
        [key: string]: unknown;
    };
    [key: string]: unknown;
}

// Interface for imported sensor data
interface ImportedSensor {
    id?: string;
    type?: string;
    imageCoordinates?: JsonCoordinate[];
    globalCoordinates?: JsonCoordinate[];
    coordinates?: JsonCoordinate;
    origin?: { lat: number; lng: number };
    geoLocation?: { lat: number; lng: number };
    scaleFactor?: number;
    attributes?: ImportedAttribute[];
    rois?: ImportedROI[];
    place?: ImportedPlace[];
    tripwires?: ImportedTripwire[];
    [key: string]: unknown;
}

// Interface for imported attributes
interface ImportedAttribute {
    name?: string;
    value?: string | number | boolean;
    [key: string]: unknown;
}

// Interface for imported ROI
interface ImportedROI {
    id?: string;
    roiCoordinates?: JsonCoordinate[];
    [key: string]: unknown;
}

// Interface for imported place
interface ImportedPlace {
    name?: string;
    value?: string;
    [key: string]: unknown;
}

// Interface for imported tripwire
interface ImportedTripwire {
    id?: string;
    tripwireCoordinates?: JsonCoordinate[];
    direction?: string;
    [key: string]: unknown;
}

// Interface for imported corridor
interface ImportedCorridor {
    directions?: string[];
    length?: number;
    name?: string;
    sensors?: string[];
    [key: string]: unknown;
}

// Interface for preview data
interface PreviewData {
    calibrationType: string;
    version: string;
    sensorCount: number;
    exportDate: string;
    projectName: string;
    firstSensor?: {
        id: string;
        imagePoints: number;
        globalPoints: number;
        rois: number;
        attributes: number;
    };
}

/**
 * Convert JSON coordinates (x, y) to internal point format (lat, lng)
 * Note: lng = x, lat = y (following Leaflet convention)
 */
export function convertJsonCoordinatesToPoints(coordinates: JsonCoordinate[]): Point[] {
    return coordinates.map(coord => ({
        lat: coord.y,
        lng: coord.x,
    }));
}

/**
 * Convert internal points to JSON coordinates
 */
export function convertPointsToJsonCoordinates(points: Point[]): JsonCoordinate[] {
    return points.map(point => ({
        x: point.lng,
        y: point.lat,
    }));
}

/**
 * Apply homography transformation to a set of coordinates
 * This is used for transforming ROIs and tripwires in cartesian calibration
 */
export function applyHomographyToCoordinates(
    coordinates: JsonCoordinate[],
    homographyMatrix: HomographyMatrix,
    imageHeight: number
): JsonCoordinate[] {
    if (!homographyMatrix || homographyMatrix.length !== 3) {
        console.warn('Invalid homography matrix provided');
        return coordinates;
    }

    try {
        const homography = matrix(homographyMatrix);

        return coordinates.map(coord => {
            // Convert to point format for transformation
            const point: Point = { lat: coord.y, lng: coord.x };

            // Apply flipPointY to convert to top-left origin for homography
            const flippedPoint = flipPointY(point, imageHeight);

            // Create homogeneous coordinates
            const homogeneousCoords = matrix([flippedPoint.lng, flippedPoint.lat, 1]);

            // Apply homography transformation
            const transformedCoords = multiply(homography, homogeneousCoords) as Matrix;

            // Extract coordinates and normalize by the homogeneous coordinate
            const coords = transformedCoords.toArray() as number[];
            const scale = coords[2];

            const transformedPoint: Point = {
                lat: coords[1] / scale,
                lng: coords[0] / scale,
            };

            // Convert back to JSON coordinate format
            return {
                x: transformedPoint.lng,
                y: transformedPoint.lat,
            };
        });
    } catch (error) {
        console.error('Error applying homography transformation:', error);
        return coordinates;
    }
}

/**
 * Scale coordinates by a given factor
 * React UI typically uses a scale factor of 100 for cartesian coordinates
 */
export function scaleCoordinates(coordinates: JsonCoordinate[], scaleFactor: number): JsonCoordinate[] {
    return coordinates.map(coord => ({
        x: coord.x / scaleFactor,
        y: coord.y / scaleFactor,
    }));
}

/**
 * Remove padding from coordinates
 * Used to reverse the padding applied during export
 */
export function removePadding(coordinates: JsonCoordinate[], xPad: number, yPad: number): JsonCoordinate[] {
    return coordinates.map(coord => ({
        x: coord.x - xPad,
        y: coord.y - yPad,
    }));
}

/**
 * Type guard to check if data is ImportedCalibrationJson
 */
function isImportedCalibrationJson(data: unknown): data is ImportedCalibrationJson {
    return typeof data === 'object' && data !== null;
}

/**
 * Validate imported calibration JSON structure
 */
export function validateCalibrationJson(jsonData: unknown): { isValid: boolean; errors: string[] } {
    const errors: string[] = [];

    // Check basic structure
    if (!jsonData) {
        errors.push('JSON data is empty or null');
        return { isValid: false, errors };
    }

    if (!isImportedCalibrationJson(jsonData)) {
        errors.push('JSON data is not a valid object');
        return { isValid: false, errors };
    }

    if (!jsonData.calibrationType) {
        errors.push('Missing calibrationType field');
    } else if (jsonData.calibrationType !== 'image' && jsonData.calibrationType !== 'cartesian') {
        errors.push(`Unsupported calibration type: ${jsonData.calibrationType}`);
    }

    if (!jsonData.sensors || !Array.isArray(jsonData.sensors)) {
        errors.push('Missing or invalid sensors array');
    } else if (jsonData.sensors.length === 0) {
        errors.push('No sensors found in JSON data');
    } else {
        // Validate each sensor
        jsonData.sensors.forEach((sensor: unknown, index: number) => {
            if (!sensor || typeof sensor !== 'object') {
                errors.push(`Sensor ${index + 1}: Invalid sensor data`);
                return;
            }

            const sensorData = sensor as ImportedSensor;
            if (!sensorData.id) {
                errors.push(`Sensor ${index + 1}: Missing sensor ID`);
            }
            if (!sensorData.imageCoordinates || !Array.isArray(sensorData.imageCoordinates)) {
                errors.push(`Sensor ${index + 1}: Missing or invalid imageCoordinates`);
            }
            if (!sensorData.globalCoordinates || !Array.isArray(sensorData.globalCoordinates)) {
                errors.push(`Sensor ${index + 1}: Missing or invalid globalCoordinates`);
            }
        });
    }

    return { isValid: errors.length === 0, errors };
}

/**
 * Process imported calibration JSON for a specific calibration type
 */
export function processImportedCalibrationJson(
    jsonData: unknown,
    calibrationType: 'image' | 'cartesian',
    targetSensorId?: string
): {
    success: boolean;
    message: string;
    data?: {
        imageCoordinates: Point[];
        globalCoordinates: Point[];
        rois: { id: string; points: Point[] }[];
        attributes: { [key: string]: unknown };
    };
} {
    // Validate JSON structure
    const validation = validateCalibrationJson(jsonData);
    if (!validation.isValid) {
        return {
            success: false,
            message: `Validation errors: ${validation.errors.join(', ')}`,
        };
    }

    const data = jsonData as ImportedCalibrationJson;

    // Check calibration type compatibility
    if (data.calibrationType !== calibrationType) {
        return {
            success: false,
            message: `Calibration type mismatch. Expected '${calibrationType}', got '${data.calibrationType}'`,
        };
    }

    // Find the target sensor or use the first one
    let targetSensor = data.sensors?.[0];
    if (targetSensorId && data.sensors) {
        const foundSensor = data.sensors.find((s: ImportedSensor) => s.id === targetSensorId);
        if (foundSensor) {
            targetSensor = foundSensor;
        }
    }

    if (!targetSensor) {
        return {
            success: false,
            message: 'No target sensor found in JSON data',
        };
    }

    try {
        // Convert coordinates
        const imageCoordinates = convertJsonCoordinatesToPoints(targetSensor.imageCoordinates || []);
        let globalCoordinates = convertJsonCoordinatesToPoints(targetSensor.globalCoordinates || []);

        // For cartesian calibration, apply scaling and padding removal
        if (calibrationType === 'cartesian' && targetSensor.scaleFactor) {
            const scaledCoords = scaleCoordinates(targetSensor.globalCoordinates || [], targetSensor.scaleFactor);
            globalCoordinates = convertJsonCoordinatesToPoints(scaledCoords);
        }

        // Process ROIs
        const rois = (targetSensor.rois || []).map((roi: ImportedROI, index: number) => ({
            id: roi.id || `roi-${index + 1}`,
            points: convertJsonCoordinatesToPoints(roi.roiCoordinates || []),
        }));

        // Extract attributes
        const attributes: { [key: string]: unknown } = {};
        if (targetSensor.attributes && Array.isArray(targetSensor.attributes)) {
            targetSensor.attributes.forEach((attr: ImportedAttribute) => {
                if (attr.name && attr.value !== undefined) {
                    attributes[attr.name] = attr.value;
                }
            });
        }

        return {
            success: true,
            message: 'Calibration JSON processed successfully',
            data: {
                imageCoordinates,
                globalCoordinates,
                rois,
                attributes,
            },
        };
    } catch (error: unknown) {
        const errorMessage = error instanceof Error ? error.message : 'Unknown error occurred';
        return {
            success: false,
            message: `Error processing calibration data: ${errorMessage}`,
        };
    }
}

/**
 * Generate a preview of the imported JSON data
 */
export function generateImportPreview(jsonData: unknown): string {
    if (!jsonData) return 'No data';

    if (!isImportedCalibrationJson(jsonData)) {
        return 'Invalid JSON format';
    }

    const preview: PreviewData = {
        calibrationType: jsonData.calibrationType || 'Unknown',
        version: jsonData.version || 'Unknown',
        sensorCount: jsonData.sensors?.length || 0,
        exportDate: (jsonData.metadata?.exportDate as string) || 'Unknown',
        projectName: (jsonData.metadata?.projectName as string) || 'Unknown',
    };

    if (jsonData.sensors && jsonData.sensors.length > 0) {
        const firstSensor = jsonData.sensors[0];
        preview.firstSensor = {
            id: firstSensor.id || 'Unknown',
            imagePoints: firstSensor.imageCoordinates?.length || 0,
            globalPoints: firstSensor.globalCoordinates?.length || 0,
            rois: firstSensor.rois?.length || 0,
            attributes: firstSensor.attributes?.length || 0,
        };
    }

    return JSON.stringify(preview, null, 2);
}
