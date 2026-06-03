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
import { matrix, multiply, subset, index, Matrix } from 'mathjs';
import { RealWorldCoordinate } from '../CoordinateInput';
import { CalibrationFigure } from '../Calibration';

export interface Point {
    lat: number; // y coordinate
    lng: number; // x coordinate
}

export interface PolygonPoint {
    lat: number;
    lng: number;
}

export type HomographyMatrix = number[][];

/**
 * Flips the y coordinate to convert between coordinate systems
 * Origin conversion: top-left (0,0) -> bottom-left (0,0)
 */
export function flipPointY(point: Point, height: number): Point;
export function flipPointY(points: Point[], height: number): Point[];
export function flipPointY(points: Point | Point[], height: number): Point | Point[] {
    if (Array.isArray(points)) {
        return points.map(({ lat, lng }) => ({
            lat: height - lat,
            lng,
        }));
    } else {
        return { lat: height - points.lat, lng: points.lng };
    }
}

/**
 * Convert a {lat: val, lng: val} point to a homographic coordinate vector of type matrix.
 * @param {object} point Point in the form of {lat: val, lng: val}
 * @param {number} height The height of the sensor resolution
 * @return {Matrix} Homographic coordinate vector
 */
export function convertLatLngToXYMatrix(point: Point, height: number): Matrix {
    const newPoint = flipPointY(point, height);
    // recall lng is x and lat is y
    return matrix([newPoint.lng, newPoint.lat, 1]);
}

/**
 * Converts a point projected from image coordinates to map coordinates from a
 * matrix to a {lat: val, lng: val} object
 * @param {Matrix} point Vector of projected point
 * @return {object} Projected point in the form {lat: val, lng: val}
 */
export function convertProjectedPoint(point: Matrix): Point {
    const scaleMatrix = subset(point, index(2));
    // Extract numeric value from Matrix
    const scale = Number(scaleMatrix);
    const projectedPoint = { lat: 0, lng: 0 };

    point.forEach(function (value: number, idx: number[]) {
        if (idx[0] === 1) {
            // second value is the latitude, as latitude = y
            projectedPoint.lat = value / scale;
        } else if (idx[0] === 0) {
            // first value is the longitude, as longitude = x
            projectedPoint.lng = value / scale;
        }
    });

    return projectedPoint;
}

/**
 * Convert string coordinates to numbers, handling cm to meters conversion for cartesian
 */
export function parseCoordinate(coord: RealWorldCoordinate, calibrationType: string): { x: number; y: number } {
    const x = parseFloat(coord.x);
    const y = parseFloat(coord.y);

    if (calibrationType === 'cartesian') {
        // Convert from cm to meters
        return { x: x / 100, y: y / 100 };
    }

    return { x, y };
}

/**
 * Calculate the euclidean distance between two lat/lng coordinates
 * @param {object} coords1 Coordinate in the form {lat: val, lng: val}
 * @param {object} coords2 Coordinate in the form {lat: val, lng: val}
 * @return {number} Distance between coordinates in meters
 */
export function euclideanDistance(coords1: Point, coords2: Point): number {
    const lng1 = coords1.lng;
    const lat1 = coords1.lat;
    const lng2 = coords2.lng;
    const lat2 = coords2.lat;

    const dist = Math.sqrt((lng1 - lng2) ** 2 + (lat1 - lat2) ** 2) * 0.01;

    return dist;
}

/**
 * Calculate haversine distance between two lat/lng coordinates
 */
export function haversineDistance(coords1: Point, coords2: Point): number {
    function toRad(x: number) {
        return (x * Math.PI) / 180;
    }

    const R = 6371 * 1000; // Earth radius in meters
    const dLat = toRad(coords2.lat - coords1.lat);
    const dLon = toRad(coords2.lng - coords1.lng);

    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(toRad(coords1.lat)) * Math.cos(toRad(coords2.lat)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);

    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));

    return R * c;
}

/**
 * Request homography calculation from backend
 */
export async function requestHomography(sensorId: string, calibrationType: string, analyticsUIServerEndpoint: string): Promise<void> {
    const endpoint =
        calibrationType === 'cartesian'
            ? `${analyticsUIServerEndpoint}/api/approxHomography/${sensorId}/`
            : `${analyticsUIServerEndpoint}/api/homography/${sensorId}/`;

    const response = await fetch(endpoint, {
        headers: { streamId: sensorId },
    });
    if (!response.ok) {
        throw new Error(`Failed to request homography calculation: ${response.statusText}`);
    }
}

/**
 * Load homography matrix from backend
 */
export async function loadHomography(sensorId: string, analyticsUIServerEndpoint: string): Promise<number[][] | null> {
    const response = await fetch(`${analyticsUIServerEndpoint}/api/sensors/${sensorId}/`, {
        headers: { streamId: sensorId },
    });
    if (!response.ok) {
        throw new Error(`Failed to load homography: ${response.statusText}`);
    }

    const data = await response.json();
    const { homography } = data;

    if (homography) {
        return JSON.parse(homography);
    }

    return null;
}

/**
 * Send calibration polygon to backend (when polygon is complete)
 */
export async function pushPolygonUpdate(
    sensorId: string,
    sensorPolygon: CalibrationFigure[],
    analyticsUIServerEndpoint: string
): Promise<void> {
    const data = {
        sensorPolygon: JSON.stringify(sensorPolygon),
        roiPolygon: JSON.stringify([]),
        tripwireLines: JSON.stringify([]),
        tripDirLines: JSON.stringify([]),
        edgeLengths: JSON.stringify([]),
        isCalibrated: false,
        isValidated: false,
    };

    const response = await fetch(`${analyticsUIServerEndpoint}/api/sensors/${sensorId}/`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
            streamId: sensorId,
        },
        body: JSON.stringify(data),
    });

    if (!response.ok) {
        throw new Error(`Failed to update sensor polygon: ${response.statusText}`);
    }
}

/**
 * Send edge lengths to backend (called whenever real-world coordinates change)
 */
export async function pushEdgeLengthsUpdate(
    sensorId: string,
    sensorPolygon: CalibrationFigure[],
    edgeLengths: RealWorldCoordinate[],
    analyticsUIServerEndpoint: string
): Promise<void> {
    // Convert RealWorldCoordinate[] to the format expected by backend
    const formattedEdgeLengths = edgeLengths.map(coord => ({
        lng: coord.x !== '' ? parseFloat(coord.x) : null,
        lat: coord.y !== '' ? parseFloat(coord.y) : null,
    }));

    const data = {
        sensorPolygon: JSON.stringify(sensorPolygon),
        roiPolygon: JSON.stringify([]),
        tripwireLines: JSON.stringify([]),
        tripDirLines: JSON.stringify([]),
        edgeLengths: JSON.stringify(formattedEdgeLengths),
        isCalibrated: false,
        isValidated: false,
    };

    const response = await fetch(`${analyticsUIServerEndpoint}/api/sensors/${sensorId}/`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
            streamId: sensorId,
        },
        body: JSON.stringify(data),
    });

    if (!response.ok) {
        throw new Error(`Failed to update edge lengths: ${response.statusText}`);
    }
}

/**
 * Wait for homography calculation to complete
 */
export async function waitForHomography(
    sensorId: string,
    calibrationType: string,
    analyticsUIServerEndpoint: string
): Promise<number[][] | null> {
    await requestHomography(sensorId, calibrationType, analyticsUIServerEndpoint);

    // Wait a moment for backend processing
    await new Promise(resolve => setTimeout(resolve, 1000));

    return await loadHomography(sensorId, analyticsUIServerEndpoint);
}

/**
 * Apply homography transformation to a point (removed - not needed for backend approach)
 */

/**
 * Calculate reprojection errors for calibration points using backend homography matrix
 */
export function calculateReprojectionErrors(
    imagePoints: Point[],
    realWorldPoints: RealWorldCoordinate[],
    homography: number[][],
    height: number,
    calibrationType: string
): number[] {
    // Convert the homography array to mathjs matrix
    const homographyMatrix = matrix(homography);

    // Convert string coordinates to numbers and handle unit conversion
    const parsedPoints = realWorldPoints.map(coord => parseCoordinate(coord, calibrationType));

    return imagePoints.map((point, index) => {
        if (calibrationType === 'geo' || calibrationType === 'image') {
            // For geographic calibration
            const matrixPoint = convertLatLngToXYMatrix(point, height);
            const projectedPoint = convertProjectedPoint(multiply(homographyMatrix, matrixPoint) as Matrix);
            const realPoint: Point = { lat: parsedPoints[index].y, lng: parsedPoints[index].x };
            return haversineDistance(projectedPoint, realPoint);
        } else {
            // For cartesian calibration
            const matrixPoint = convertLatLngToXYMatrix(point, height);
            const projectedPoint = convertProjectedPoint(multiply(homographyMatrix, matrixPoint) as Matrix);
            const realPoint: Point = { lat: parsedPoints[index].y, lng: parsedPoints[index].x };
            return euclideanDistance(projectedPoint, realPoint);
        }
    });
}

/**
 * Validate calibration points based on type
 */
export function validateCalibrationPoints(
    imagePoints: Point[],
    realWorldPoints: RealWorldCoordinate[],
    calibrationType: string
): {
    isValid: boolean;
    errors: string[];
} {
    const errors: string[] = [];

    if (calibrationType === 'cartesian') {
        if (imagePoints.length !== 4) {
            errors.push('Cartesian calibration requires exactly 4 points');
        }
        if (realWorldPoints.length !== 4) {
            errors.push('Cartesian calibration requires exactly 4 real-world coordinates');
        }
    } else if (calibrationType === 'image' || calibrationType === 'geo') {
        if (imagePoints.length < 8) {
            errors.push('Geographic calibration requires at least 8 points');
        }
        if (imagePoints.length !== realWorldPoints.length) {
            errors.push('Number of image points must match real-world coordinates');
        }
    }

    // Check if all real-world coordinates are filled and valid numbers
    const incompleteCoords = realWorldPoints.some(
        coord =>
            !coord ||
            coord.x === undefined ||
            coord.y === undefined ||
            coord.x.trim() === '' ||
            coord.y.trim() === '' ||
            isNaN(parseFloat(coord.x)) ||
            isNaN(parseFloat(coord.y))
    );

    if (incompleteCoords) {
        errors.push('All real-world coordinates must be filled with valid numbers');
    }

    return {
        isValid: errors.length === 0,
        errors,
    };
}
