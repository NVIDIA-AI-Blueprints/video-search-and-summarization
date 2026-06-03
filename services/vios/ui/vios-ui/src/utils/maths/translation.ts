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
type Bbox = { leftX: number; rightX: number; topY: number; bottomY: number };
type Coordinate = { x: number; y: number; z?: number };
type GeoLocation = { lat: number; lon: number; alt?: number };

/**
 * Calibration Class - Coordinate Transformation System
 *
 * This class handles the transformation of image coordinates (pixels) to real-world geographic coordinates
 * using homography matrices for perspective correction and coordinate system conversion.
 *
 * HOMOGRAPHY MATRIX:
 * A homography is a 3x3 transformation matrix that maps points from one plane to another.
 * In computer vision, it's commonly used to correct perspective distortion and transform
 * image coordinates to real-world ground plane coordinates.
 *
 * The 3x3 homography matrix H is structured as:
 * [ h00  h01  h02 ]
 * [ h10  h11  h12 ]
 * [ h20  h21  h22 ]
 *
 * For a point (x, y) in image coordinates, the transformation to ground plane coordinates (X, Y) is:
 * X = (h00*x + h01*y + h02) / (h20*x + h21*y + h22)
 * Y = (h10*x + h11*y + h12) / (h20*x + h21*y + h22)
 *
 * COORDINATE CONVERSION PROCESS:
 * 1. Image Coordinates (pixels) → Ground Plane Coordinates (meters)
 *    - Uses homography matrix to correct perspective and convert to real-world scale
 *    - Accounts for camera angle, height, and orientation
 *
 * 2. Ground Plane Coordinates → Geographic Coordinates (lat/lon)
 *    - Converts from local metric coordinates to global geographic coordinates
 *    - Uses a reference origin point (lat/lon) and applies offset calculations
 *    - Accounts for Earth's curvature and longitude compression at different latitudes
 */
class Calibration {
    sensorMap: Record<string, { origin: GeoLocation; homography: number[][] }>;
    private transformCache = new Map<string, [number, number]>();
    private readonly CACHE_SIZE_LIMIT = 1000;

    constructor(sensorMap: Record<string, { origin: GeoLocation; homography: number[][] }>) {
        this.sensorMap = sensorMap;
        this.validateAllSensorConfigs();
    }

    contains(sensorId: string): boolean {
        return Object.prototype.hasOwnProperty.call(this.sensorMap, sensorId);
    }

    /**
     * Validate all sensor configurations on initialization
     */
    private validateAllSensorConfigs(): void {
        for (const [sensorId, config] of Object.entries(this.sensorMap)) {
            try {
                this.validateSensorConfig(config);
            } catch (error) {
                throw new ValidationError(
                    `Invalid configuration for sensor ${sensorId}: ${(error as Error).message}`,
                    'INVALID_SENSOR_CONFIG'
                );
            }
        }
    }

    /**
     * Validate individual sensor configuration
     */
    private validateSensorConfig(config: { origin: GeoLocation; homography: number[][] }): void {
        if (!config.homography || config.homography.length !== 3) {
            throw new ValidationError('Invalid homography matrix', 'INVALID_HOMOGRAPHY');
        }

        for (const row of config.homography) {
            if (row.length !== 3) {
                throw new ValidationError('Homography matrix must be 3x3', 'INVALID_HOMOGRAPHY_SIZE');
            }
        }

        // Validate homography matrix is not singular
        const det = this.calculateDeterminant(config.homography);
        if (Math.abs(det) < 1e-10) {
            throw new ValidationError('Homography matrix is singular', 'SINGULAR_MATRIX');
        }

        if (!config.origin || typeof config.origin.lat !== 'number' || typeof config.origin.lon !== 'number') {
            throw new ValidationError('Invalid origin coordinates', 'INVALID_ORIGIN');
        }
    }

    /**
     * Calculate determinant of 3x3 matrix
     */
    private calculateDeterminant(matrix: number[][]): number {
        const h = matrix;
        return (
            h[0][0] * (h[1][1] * h[2][2] - h[1][2] * h[2][1]) -
            h[0][1] * (h[1][0] * h[2][2] - h[1][2] * h[2][0]) +
            h[0][2] * (h[1][0] * h[2][1] - h[1][1] * h[2][0])
        );
    }

    /**
     * Transform bounding box from image coordinates to real-world coordinates
     */
    transformBbox(bbox: Bbox, sensorId: string): [Coordinate, GeoLocation] {
        // Use center-x and bottom-y of bbox as the reference point
        const px = (bbox.rightX + bbox.leftX) / 2.0;
        const py = Math.max(bbox.topY, bbox.bottomY);

        let latlonOrigin: GeoLocation;
        let coordinate: Coordinate;

        if (this.contains(sensorId)) {
            const { origin, homography } = this.sensorMap[sensorId];
            latlonOrigin = origin;
            const globalCoords = this.perspectiveTransform(px, py, homography);
            if (globalCoords) {
                coordinate = { x: globalCoords[0], y: globalCoords[1] };
            } else {
                // Fallback to pixel coordinates if transformation fails
                coordinate = { x: px, y: py, z: 0 };
            }
        } else {
            // Default origin and pixel coordinates for unknown sensors
            latlonOrigin = { lat: 0.0, lon: 0.0, alt: 0.0 };
            coordinate = { x: px, y: py, z: 0 };
        }

        const latlon = getLatLon(latlonOrigin, coordinate);
        return [coordinate, latlon];
    }

    /**
     * Apply homography transformation to convert pixel to ground plane coordinates
     * Enhanced with caching for performance optimization
     */
    perspectiveTransform(px: number, py: number, homography?: number[][]): [number, number] | null {
        if (!homography) return null;

        // Create cache key for transformation
        const cacheKey = `${px},${py},${JSON.stringify(homography)}`;
        if (this.transformCache.has(cacheKey)) {
            return this.transformCache.get(cacheKey)!;
        }

        // Apply 3x3 homography matrix transformation
        const w = homography[2][0] * px + homography[2][1] * py + homography[2][2];
        if (Math.abs(w) < 1e-10) return null; // Avoid division by zero

        const transformedX = (homography[0][0] * px + homography[0][1] * py + homography[0][2]) / w;
        const transformedY = (homography[1][0] * px + homography[1][1] * py + homography[1][2]) / w;

        const result: [number, number] = [transformedX, transformedY];

        // Cache management
        if (this.transformCache.size >= this.CACHE_SIZE_LIMIT) {
            const firstKey = this.transformCache.keys().next().value;
            if (firstKey) {
                this.transformCache.delete(firstKey);
            }
        }
        this.transformCache.set(cacheKey, result);

        return result;
    }

    /**
     * Validate transformation inputs
     */
    validateTransformation(sensorId: string, coordinates: Coordinate[]): ValidationResult {
        const result: ValidationResult = { isValid: true, errors: [], warnings: [] };

        if (!this.contains(sensorId)) {
            result.errors.push(`Sensor ${sensorId} not found`);
            result.isValid = false;
        }

        if (coordinates.length === 0) {
            result.errors.push('No coordinates provided');
            result.isValid = false;
        }

        // Check for coordinates outside reasonable bounds
        coordinates.forEach((coord, index) => {
            if (Math.abs(coord.x) > 50000 || Math.abs(coord.y) > 50000) {
                result.warnings.push(`Coordinate ${index} has unusually large values: (${coord.x}, ${coord.y})`);
            }

            if (coord.x < 0 || coord.y < 0) {
                result.warnings.push(`Coordinate ${index} has negative values: (${coord.x}, ${coord.y})`);
            }
        });

        return result;
    }

    /**
     * Transform multiple points efficiently
     */
    transformMultiplePoints(points: Coordinate[], sensorId: string): Coordinate[] {
        const validation = this.validateTransformation(sensorId, points);
        if (!validation.isValid) {
            throw new TransformationError(`Batch transformation validation failed: ${validation.errors.join(', ')}`, sensorId);
        }

        const sensor = this.sensorMap[sensorId];
        if (!sensor) {
            throw new TransformationError(`Sensor ${sensorId} not found`, sensorId);
        }

        return points.map(point => {
            const transformed = this.perspectiveTransform(point.x, point.y, sensor.homography);
            return transformed ? { x: transformed[0], y: transformed[1], z: point.z } : point;
        });
    }

    /**
     * Clear transformation cache
     */
    clearCache(): void {
        this.transformCache.clear();
    }

    /**
     * Get cache statistics
     */
    getCacheStats(): { size: number; limit: number } {
        return {
            size: this.transformCache.size,
            limit: this.CACHE_SIZE_LIMIT,
        };
    }
}

/**
 * Convert local coordinates to geographic lat/lon based on origin point
 */
function getLatLon(origin: GeoLocation, coor: Coordinate): GeoLocation {
    // Convert meters to kilometers
    const x = coor.x / 1000.0;
    const y = coor.y / 1000.0;

    // Earth's circumference approximation: ~40,000 km
    const lat = origin.lat - (y * 360.0) / 40000.0;
    // Account for longitude compression at different latitudes
    const avgLat = ((origin.lat + lat) * Math.PI) / 360.0;
    const lon = origin.lon - (x * 360.0) / (40000.0 * Math.cos(avgLat));

    return { lat: lat, lon: lon, alt: origin.alt };
}

/**
 * ReverseCalibration Class - Reverse Coordinate Transformation System
 *
 * This class handles the reverse transformation of world coordinates back to image coordinates.
 * It's designed to work with ROIs and tripwires that need to be displayed on video frames.
 *
 * REVERSE TRANSFORMATION PROCESS:
 * 1. World Coordinates (meters/global coords) → Image Coordinates (pixels)
 *    - Uses inverse homography matrix to transform from real-world coordinates back to pixel space
 *    - Essential for displaying ROIs and tripwires on video frames
 *
 * 2. Geographic Coordinates (lat/lon) → World Coordinates (meters)
 *    - Converts from geographic coordinates back to local metric coordinates
 *    - Uses the same origin point as the forward transformation
 *
 * INVERSE HOMOGRAPHY:
 * If H is the homography matrix that transforms image coords to world coords,
 * then H^-1 (inverse of H) transforms world coords back to image coords.
 *
 * TYPICAL USE CASE:
 * - Displaying ROIs and tripwires on video frames
 * - Converting API response coordinates to drawable pixel coordinates
 * - Reverse mapping from stored world coordinates to UI elements
 */
class ReverseCalibration {
    sensorMap: Record<string, { origin: GeoLocation; homography: number[][]; inverseHomography?: number[][] }>;

    constructor(sensorMap: Record<string, { origin: GeoLocation; homography: number[][]; inverseHomography?: number[][] }>) {
        this.sensorMap = sensorMap;
        // Pre-calculate inverse homography matrices for performance
        this.preCalculateInverseHomographies();
    }

    contains(sensorId: string): boolean {
        return Object.prototype.hasOwnProperty.call(this.sensorMap, sensorId);
    }

    /**
     * Pre-calculate inverse homography matrices for all sensors
     */
    private preCalculateInverseHomographies(): void {
        for (const sensorId in this.sensorMap) {
            const sensor = this.sensorMap[sensorId];
            if (sensor.homography) {
                sensor.inverseHomography = this.calculateInverseHomography(sensor.homography);
            }
        }
    }

    /**
     * Calculate the inverse of a 3x3 homography matrix
     */
    private calculateInverseHomography(homography: number[][]): number[][] | undefined {
        const h = homography;

        // Calculate determinant
        const det =
            h[0][0] * (h[1][1] * h[2][2] - h[1][2] * h[2][1]) -
            h[0][1] * (h[1][0] * h[2][2] - h[1][2] * h[2][0]) +
            h[0][2] * (h[1][0] * h[2][1] - h[1][1] * h[2][0]);

        if (Math.abs(det) < 1e-10) {
            console.warn('Homography matrix is singular, cannot calculate inverse');
            return undefined;
        }

        // Calculate inverse matrix using adjugate matrix / determinant
        const inv: number[][] = [
            [
                (h[1][1] * h[2][2] - h[1][2] * h[2][1]) / det,
                (h[0][2] * h[2][1] - h[0][1] * h[2][2]) / det,
                (h[0][1] * h[1][2] - h[0][2] * h[1][1]) / det,
            ],
            [
                (h[1][2] * h[2][0] - h[1][0] * h[2][2]) / det,
                (h[0][0] * h[2][2] - h[0][2] * h[2][0]) / det,
                (h[0][2] * h[1][0] - h[0][0] * h[1][2]) / det,
            ],
            [
                (h[1][0] * h[2][1] - h[1][1] * h[2][0]) / det,
                (h[0][1] * h[2][0] - h[0][0] * h[2][1]) / det,
                (h[0][0] * h[1][1] - h[0][1] * h[1][0]) / det,
            ],
        ];

        return inv;
    }

    /**
     * Transform world coordinates back to image pixel coordinates
     */
    transformWorldToImage(worldCoord: Coordinate, sensorId: string): Coordinate | null {
        if (!this.contains(sensorId)) {
            console.warn(`Sensor ${sensorId} not found in sensor map`);
            return null;
        }

        const sensor = this.sensorMap[sensorId];
        if (!sensor.inverseHomography) {
            console.warn(`Inverse homography not available for sensor ${sensorId}`);
            return null;
        }

        const imageCoords = this.applyInverseHomography(worldCoord.x, worldCoord.y, sensor.inverseHomography);

        if (!imageCoords) {
            return null;
        }

        return { x: imageCoords[0], y: imageCoords[1] };
    }

    /**
     * Apply inverse homography transformation to convert world coordinates to image coordinates
     */
    private applyInverseHomography(worldX: number, worldY: number, inverseHomography: number[][]): [number, number] | null {
        // Apply 3x3 inverse homography matrix transformation
        const w = inverseHomography[2][0] * worldX + inverseHomography[2][1] * worldY + inverseHomography[2][2];

        if (Math.abs(w) < 1e-10) {
            console.warn('Division by zero in inverse homography transformation');
            return null;
        }

        const imageX = (inverseHomography[0][0] * worldX + inverseHomography[0][1] * worldY + inverseHomography[0][2]) / w;

        const imageY = (inverseHomography[1][0] * worldX + inverseHomography[1][1] * worldY + inverseHomography[1][2]) / w;

        return [imageX, imageY];
    }

    /**
     * Transform geographic coordinates back to world coordinates
     */
    transformGeoToWorld(geoLocation: GeoLocation, origin: GeoLocation): Coordinate {
        // Reverse of the getLatLon function
        const latDiff = origin.lat - geoLocation.lat;
        const y = (latDiff * 40000.0) / 360.0; // Convert back to kilometers

        const avgLat = ((origin.lat + geoLocation.lat) * Math.PI) / 360.0;
        const lonDiff = origin.lon - geoLocation.lon;
        const x = (lonDiff * 40000.0 * Math.cos(avgLat)) / 360.0; // Convert back to kilometers

        // Convert kilometers back to meters
        return { x: x * 1000.0, y: y * 1000.0, z: geoLocation.alt || 0 };
    }

    /**
     * Transform ROI coordinates from world coordinates to image coordinates
     */
    transformROIToImage(roiCoordinates: Coordinate[], sensorId: string): Coordinate[] {
        const transformedCoords: Coordinate[] = [];

        for (const coord of roiCoordinates) {
            const imageCoord = this.transformWorldToImage(coord, sensorId);
            if (imageCoord) {
                transformedCoords.push(imageCoord);
            }
        }

        return transformedCoords;
    }

    /**
     * Transform tripwire coordinates from world coordinates to image coordinates
     */
    transformTripwireToImage(tripwire: { p1: Coordinate; p2: Coordinate }, sensorId: string): { p1: Coordinate; p2: Coordinate } | null {
        const p1Image = this.transformWorldToImage(tripwire.p1, sensorId);
        const p2Image = this.transformWorldToImage(tripwire.p2, sensorId);

        if (!p1Image || !p2Image) {
            return null;
        }

        return { p1: p1Image, p2: p2Image };
    }
}

/**
 * Helper functions for forward transformations (image to world coordinates)
 * These are used when drawing ROIs/tripwires on UI and submitting to API
 */

/**
 * Transform image ROI coordinates to world coordinates
 */
function transformImageROIToWorld(roiImageCoords: Coordinate[], sensorId: string, calibrationInstance: Calibration): Coordinate[] {
    const worldCoords: Coordinate[] = [];

    for (const imageCoord of roiImageCoords) {
        // Use the center-bottom approach similar to transformBbox
        const px = imageCoord.x;
        const py = imageCoord.y;

        if (calibrationInstance.contains(sensorId)) {
            const { homography } = calibrationInstance.sensorMap[sensorId];
            const globalCoords = calibrationInstance.perspectiveTransform(px, py, homography);
            if (globalCoords) {
                worldCoords.push({ x: globalCoords[0], y: globalCoords[1], z: imageCoord.z || 0 });
            } else {
                // Fallback to pixel coordinates if transformation fails
                worldCoords.push({ x: px, y: py, z: 0 });
            }
        } else {
            // Default to pixel coordinates for unknown sensors
            worldCoords.push({ x: px, y: py, z: 0 });
        }
    }

    return worldCoords;
}

/**
 * Transform image tripwire coordinates to world coordinates
 */
function transformImageTripwireToWorld(
    tripwireImageCoords: { p1: { x: number; y: number }; p2: { x: number; y: number } },
    sensorId: string,
    calibrationInstance: Calibration
): { p1: Coordinate; p2: Coordinate } {
    const transformPoint = (point: { x: number; y: number }): Coordinate => {
        if (calibrationInstance.contains(sensorId)) {
            const { homography } = calibrationInstance.sensorMap[sensorId];
            const globalCoords = calibrationInstance.perspectiveTransform(point.x, point.y, homography);
            if (globalCoords) {
                return { x: globalCoords[0], y: globalCoords[1], z: 0 };
            }
        }
        // Fallback to pixel coordinates
        return { x: point.x, y: point.y, z: 0 };
    };

    return {
        p1: transformPoint(tripwireImageCoords.p1),
        p2: transformPoint(tripwireImageCoords.p2),
    };
}

// 2D transformation utilities using 4-point perspective transformation (homography)
// This properly handles perspective distortion using all 4 reference point correspondences

/**
 * Calculate homography matrix from n point correspondences (n >= 4)
 * Using least squares approach for overdetermined systems when n > 4
 * @param srcPoints - Source points (e.g., world coordinates)
 * @param dstPoints - Destination points (e.g., image coordinates)
 * @returns 3x3 homography matrix as flat array [h11, h12, h13, h21, h22, h23, h31, h32, h33]
 */
export const calculateHomographyMatrix = (srcPoints: { x: number; y: number }[], dstPoints: { x: number; y: number }[]): number[] => {
    if (srcPoints.length !== dstPoints.length) {
        throw new Error('Source and destination points must have the same length');
    }

    if (srcPoints.length < 4) {
        throw new Error('At least 4 point correspondences required for homography calculation');
    }

    const numPoints = srcPoints.length;

    // Set up the system of equations with h33 = 1
    // For each point correspondence:
    // dx = (h11*sx + h12*sy + h13) / (h31*sx + h32*sy + 1)
    // dy = (h21*sx + h22*sy + h23) / (h31*sx + h32*sy + 1)
    //
    // Rearranging:
    // h11*sx + h12*sy + h13 - dx*h31*sx - dx*h32*sy = dx
    // h21*sx + h22*sy + h23 - dy*h31*sx - dy*h32*sy = dy

    const A: number[][] = [];
    const b: number[] = [];

    for (let i = 0; i < numPoints; i++) {
        const { x: sx, y: sy } = srcPoints[i];
        const { x: dx, y: dy } = dstPoints[i];

        // First equation for this point
        A.push([sx, sy, 1, 0, 0, 0, -dx * sx, -dx * sy]);
        b.push(dx);

        // Second equation for this point
        A.push([0, 0, 0, sx, sy, 1, -dy * sx, -dy * sy]);
        b.push(dy);
    }

    // Solve the system for [h11, h12, h13, h21, h22, h23, h31, h32]
    // For overdetermined systems (n > 4), this uses least squares
    const h8 = solveLinearSystemLeastSquares(A, b);

    // Return the full 9-element homography matrix with h33 = 1
    const homography = [...h8, 1];
    return homography;
};

export const solveLinearSystemLeastSquares = (A: number[][], b: number[]): number[] => {
    // Least squares solution using normal equations: (A^T * A) * x = A^T * b
    // For production, use a proper linear algebra library like ml-matrix

    const m = A.length; // number of equations
    const n = A[0].length; // number of unknowns

    // Calculate A^T (transpose of A)
    const AT: number[][] = [];
    for (let j = 0; j < n; j++) {
        const row: number[] = [];
        for (let i = 0; i < m; i++) {
            row.push(A[i][j]);
        }
        AT.push(row);
    }

    // Calculate A^T * A
    const ATA: number[][] = [];
    for (let i = 0; i < n; i++) {
        const row: number[] = [];
        for (let j = 0; j < n; j++) {
            let sum = 0;
            for (let k = 0; k < m; k++) {
                sum += AT[i][k] * A[k][j];
            }
            row.push(sum);
        }
        ATA.push(row);
    }

    // Calculate A^T * b
    const ATb: number[] = [];
    for (let i = 0; i < n; i++) {
        let sum = 0;
        for (let j = 0; j < m; j++) {
            sum += AT[i][j] * b[j];
        }
        ATb.push(sum);
    }

    // Solve the system (A^T * A) * x = A^T * b using Gaussian elimination
    const augmented = ATA.map((row, i) => [...row, ATb[i]]);

    // Forward elimination
    for (let i = 0; i < n; i++) {
        // Find pivot
        let maxRow = i;
        for (let k = i + 1; k < n; k++) {
            if (Math.abs(augmented[k][i]) > Math.abs(augmented[maxRow][i])) {
                maxRow = k;
            }
        }
        [augmented[i], augmented[maxRow]] = [augmented[maxRow], augmented[i]];

        // Check for zero pivot
        if (Math.abs(augmented[i][i]) < 1e-10) {
            throw new Error(`Matrix is singular or near-singular at row ${i}`);
        }

        // Eliminate column
        for (let k = i + 1; k < n; k++) {
            const factor = augmented[k][i] / augmented[i][i];
            for (let j = i; j <= n; j++) {
                augmented[k][j] -= factor * augmented[i][j];
            }
        }
    }

    // Back substitution
    const x = new Array(n);
    for (let i = n - 1; i >= 0; i--) {
        x[i] = augmented[i][n];
        for (let j = i + 1; j < n; j++) {
            x[i] -= augmented[i][j] * x[j];
        }
        x[i] /= augmented[i][i];
    }

    return x;
};

/**
 * Apply homography transformation to a point
 * @param point - Input point
 * @param homography - 3x3 homography matrix as flat array
 * @returns Transformed point
 */
export const applyHomography = (point: { x: number; y: number }, homography: number[]): { x: number; y: number } => {
    const [h11, h12, h13, h21, h22, h23, h31, h32, h33] = homography;

    const w = h31 * point.x + h32 * point.y + h33;
    const x = (h11 * point.x + h12 * point.y + h13) / w;
    const y = (h21 * point.x + h22 * point.y + h23) / w;

    return { x, y };
};

export const transform2DImageToWorld = (
    imageCoords: { x: number; y: number }[],
    referenceImageCoords: { x: number; y: number }[],
    referenceGlobalCoords: { x: number; y: number }[]
): { x: number; y: number; z: number }[] => {
    if (
        referenceImageCoords.length < 4 ||
        referenceGlobalCoords.length < 4 ||
        referenceImageCoords.length !== referenceGlobalCoords.length
    ) {
        throw new Error('At least 4 matching reference point correspondences required for 2D transformation');
    }

    try {
        // Calculate bounds for reference coordinates to validate results
        const globalXValues = referenceGlobalCoords.map(coord => coord.x);
        const globalYValues = referenceGlobalCoords.map(coord => coord.y);
        const minGlobalX = Math.min(...globalXValues);
        const maxGlobalX = Math.max(...globalXValues);
        const minGlobalY = Math.min(...globalYValues);
        const maxGlobalY = Math.max(...globalYValues);
        const globalRangeX = maxGlobalX - minGlobalX;
        const globalRangeY = maxGlobalY - minGlobalY;

        // Calculate homography from image coordinates to world coordinates
        const homography = calculateHomographyMatrix(referenceImageCoords, referenceGlobalCoords);

        // Test homography stability by applying it to reference points
        const testTransformations = referenceImageCoords.map((coord, index) => {
            const transformed = applyHomography(coord, homography);
            const expected = referenceGlobalCoords[index];
            const errorX = Math.abs(transformed.x - expected.x);
            const errorY = Math.abs(transformed.y - expected.y);
            return {
                index,
                transformed,
                expected,
                errorX,
                errorY,
                relativeErrorX: globalRangeX > 0 ? errorX / globalRangeX : 0,
                relativeErrorY: globalRangeY > 0 ? errorY / globalRangeY : 0,
            };
        });

        // Check if homography is stable (low transformation error on reference points)
        const maxRelativeError = Math.max(...testTransformations.map(t => Math.max(t.relativeErrorX, t.relativeErrorY)));

        if (maxRelativeError > 0.1) {
            // 10% relative error threshold
            console.warn('Homography appears unstable. Reference point transformation errors:', testTransformations);
            console.warn(`Maximum relative error: ${(maxRelativeError * 100).toFixed(2)}%`);
        }

        return imageCoords.map((coord, index) => {
            const worldCoord = applyHomography(coord, homography);

            // Validate the resulting world coordinates are reasonable
            if (!isFinite(worldCoord.x) || !isFinite(worldCoord.y)) {
                console.warn(`Homography produced invalid world coordinates at index ${index}:`, worldCoord, 'from image coord:', coord);
                throw new Error(`Invalid transformation result at point ${index}: non-finite coordinates`);
            }

            // Check if result is extremely far from reference bounds (likely indicates unstable homography)
            const toleranceFactor = 5.0; // Allow 5x the reference range as tolerance
            const expandedMinX = minGlobalX - globalRangeX * toleranceFactor;
            const expandedMaxX = maxGlobalX + globalRangeX * toleranceFactor;
            const expandedMinY = minGlobalY - globalRangeY * toleranceFactor;
            const expandedMaxY = maxGlobalY + globalRangeY * toleranceFactor;

            if (worldCoord.x < expandedMinX || worldCoord.x > expandedMaxX || worldCoord.y < expandedMinY || worldCoord.y > expandedMaxY) {
                console.warn(`Transformed coordinate at index ${index} is far outside reference bounds:`, {
                    transformed: worldCoord,
                    imageCoord: coord,
                    referenceBounds: { minGlobalX, maxGlobalX, minGlobalY, maxGlobalY },
                    expandedBounds: { expandedMinX, expandedMaxX, expandedMinY, expandedMaxY },
                });
                console.warn('This may indicate an unstable homography transformation. Consider reviewing calibration points.');

                // For now, we'll allow it but warn the user
                // In production, you might want to throw an error or use a fallback method
            }

            return { x: worldCoord.x, y: worldCoord.y, z: 0 };
        });
    } catch (error) {
        console.error('Homography calculation failed:', error);
        console.error('Reference image coordinates:', referenceImageCoords);
        console.error('Reference global coordinates:', referenceGlobalCoords);
        console.error('Input image coordinates:', imageCoords);
        throw new Error(`Failed to calculate 2D transformation: ${error}`);
    }
};

export const transform2DWorldToImage = (
    worldCoords: { x: number; y: number; z?: number }[],
    referenceImageCoords: { x: number; y: number }[],
    referenceGlobalCoords: { x: number; y: number }[]
): { x: number; y: number }[] => {
    if (
        referenceImageCoords.length < 4 ||
        referenceGlobalCoords.length < 4 ||
        referenceImageCoords.length !== referenceGlobalCoords.length
    ) {
        throw new Error('At least 4 matching reference point correspondences required for 2D transformation');
    }

    try {
        // Calculate homography from world coordinates to image coordinates
        const homography = calculateHomographyMatrix(referenceGlobalCoords, referenceImageCoords);

        return worldCoords.map(coord => {
            return applyHomography({ x: coord.x, y: coord.y }, homography);
        });
    } catch (error) {
        console.error('Homography calculation failed:', error);
        throw new Error(`Failed to calculate 2D reverse transformation: ${error}`);
    }
};

/**
 * Diagnose calibration data quality and identify potential issues
 */
export const diagnoseCalibrationData = (
    referenceImageCoords: { x: number; y: number }[],
    referenceGlobalCoords: { x: number; y: number }[]
): {
    isHealthy: boolean;
    issues: string[];
    warnings: string[];
    recommendations: string[];
    details: {
        pointCount: number;
        imageSpread: { x: number; y: number };
        globalSpread: { x: number; y: number };
        aspectRatioMismatch: number;
        collinearityScore: number;
    };
} => {
    const issues: string[] = [];
    const warnings: string[] = [];
    const recommendations: string[] = [];

    // Basic validation
    if (referenceImageCoords.length !== referenceGlobalCoords.length) {
        issues.push('Mismatch between image and global coordinate counts');
    }

    if (referenceImageCoords.length < 4) {
        issues.push('Less than 4 calibration points (minimum required)');
    }

    // Calculate spreads and ranges
    const imageXValues = referenceImageCoords.map(c => c.x);
    const imageYValues = referenceImageCoords.map(c => c.y);
    const globalXValues = referenceGlobalCoords.map(c => c.x);
    const globalYValues = referenceGlobalCoords.map(c => c.y);

    const imageSpread = {
        x: Math.max(...imageXValues) - Math.min(...imageXValues),
        y: Math.max(...imageYValues) - Math.min(...imageYValues),
    };

    const globalSpread = {
        x: Math.max(...globalXValues) - Math.min(...globalXValues),
        y: Math.max(...globalYValues) - Math.min(...globalYValues),
    };

    // Check for insufficient spread
    if (imageSpread.x < 100 || imageSpread.y < 100) {
        warnings.push('Image calibration points are clustered too close together');
        recommendations.push('Spread calibration points across a larger area of the image');
    }

    if (globalSpread.x < 1.0 || globalSpread.y < 1.0) {
        warnings.push('Global calibration points cover a very small real-world area');
        recommendations.push('Use calibration points that span a larger physical area');
    }

    // Check aspect ratio consistency
    const imageAspectRatio = imageSpread.x / imageSpread.y;
    const globalAspectRatio = globalSpread.x / globalSpread.y;
    const aspectRatioMismatch = Math.abs(imageAspectRatio - globalAspectRatio) / Math.max(imageAspectRatio, globalAspectRatio);

    if (aspectRatioMismatch > 0.5) {
        warnings.push('Significant aspect ratio mismatch between image and global coordinates');
        recommendations.push('Check if calibration points maintain consistent geometric relationships');
    }

    // Check for collinearity (simplified check)
    let collinearityScore = 0;
    if (referenceImageCoords.length >= 4) {
        // Check if points are roughly aligned (simplified collinearity test)
        const imagePoints = referenceImageCoords.slice(0, 4);

        // Calculate if 3+ points are nearly collinear in image space
        for (let i = 0; i < imagePoints.length - 2; i++) {
            for (let j = i + 1; j < imagePoints.length - 1; j++) {
                for (let k = j + 1; k < imagePoints.length; k++) {
                    const p1 = imagePoints[i];
                    const p2 = imagePoints[j];
                    const p3 = imagePoints[k];

                    // Calculate area of triangle formed by 3 points
                    const area = Math.abs((p1.x * (p2.y - p3.y) + p2.x * (p3.y - p1.y) + p3.x * (p1.y - p2.y)) / 2);
                    const maxDist = Math.max(
                        Math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2),
                        Math.sqrt((p2.x - p3.x) ** 2 + (p2.y - p3.y) ** 2),
                        Math.sqrt((p1.x - p3.x) ** 2 + (p1.y - p3.y) ** 2)
                    );

                    if (maxDist > 0) {
                        const normalizedArea = area / maxDist ** 2;
                        if (normalizedArea < 0.01) {
                            // Very small triangle relative to distances
                            collinearityScore += 1;
                        }
                    }
                }
            }
        }

        if (collinearityScore > 0) {
            warnings.push('Some calibration points appear to be nearly collinear');
            recommendations.push('Ensure calibration points form a well-distributed pattern, not aligned in straight lines');
        }
    }

    // Check for extreme coordinate values
    const hasExtremeImageCoords = referenceImageCoords.some(
        c => Math.abs(c.x) > 10000 || Math.abs(c.y) > 10000 || !isFinite(c.x) || !isFinite(c.y)
    );

    const hasExtremeGlobalCoords = referenceGlobalCoords.some(
        c => Math.abs(c.x) > 1000 || Math.abs(c.y) > 1000 || !isFinite(c.x) || !isFinite(c.y)
    );

    if (hasExtremeImageCoords) {
        issues.push('Image coordinates contain extreme or invalid values');
    }

    if (hasExtremeGlobalCoords) {
        warnings.push('Global coordinates contain very large values');
        recommendations.push('Consider using a local coordinate system with smaller values');
    }

    // Overall health assessment
    const isHealthy = issues.length === 0 && warnings.length <= 1;

    return {
        isHealthy,
        issues,
        warnings,
        recommendations,
        details: {
            pointCount: referenceImageCoords.length,
            imageSpread,
            globalSpread,
            aspectRatioMismatch,
            collinearityScore,
        },
    };
};

// Custom Error Classes
class ValidationError extends Error {
    constructor(
        message: string,
        public code: string
    ) {
        super(message);
        this.name = 'ValidationError';
    }
}

class TransformationError extends Error {
    constructor(
        message: string,
        public sensorId: string
    ) {
        super(message);
        this.name = 'TransformationError';
    }
}

interface ValidationResult {
    isValid: boolean;
    errors: string[];
    warnings: string[];
}

/**
 * Calculate euclidean distance between two coordinates
 */
export function euclideanDistance(coord1: Coordinate, coord2: Coordinate): number {
    const dx = coord1.x - coord2.x;
    const dy = coord1.y - coord2.y;
    const dz = (coord1.z || 0) - (coord2.z || 0);
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

/**
 * Calculate haversine distance between two geographic coordinates
 */
export function haversineDistance(coord1: GeoLocation, coord2: GeoLocation): number {
    const toRad = (degrees: number) => degrees * (Math.PI / 180);

    const R = 6371 * 1000; // Earth's radius in meters
    const dLat = toRad(coord2.lat - coord1.lat);
    const dLon = toRad(coord2.lon - coord1.lon);

    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(toRad(coord1.lat)) * Math.cos(toRad(coord2.lat)) * Math.sin(dLon / 2) * Math.sin(dLon / 2);

    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
}

/**
 * Calculate distance between coordinates (with method selection)
 */
export function calculateDistance(
    coord1: Coordinate | GeoLocation,
    coord2: Coordinate | GeoLocation,
    method: 'euclidean' | 'haversine' = 'euclidean'
): number {
    if (method === 'haversine' && 'lat' in coord1 && 'lat' in coord2) {
        return haversineDistance(coord1, coord2);
    }
    return euclideanDistance(coord1 as Coordinate, coord2 as Coordinate);
}

interface CalibrationStats {
    reprojectionError: number;
    meanError: number;
    maxError: number;
    isReliable: boolean;
}

/**
 * Calculate calibration quality metrics
 */
export function calculateCalibrationQuality(
    imagePoints: Coordinate[],
    worldPoints: Coordinate[],
    calibrationInstance: Calibration,
    sensorId: string
): CalibrationStats {
    if (imagePoints.length !== worldPoints.length) {
        throw new ValidationError('Point arrays must have same length', 'LENGTH_MISMATCH');
    }

    if (!calibrationInstance.contains(sensorId)) {
        throw new TransformationError(`Sensor ${sensorId} not found`, sensorId);
    }

    const sensor = calibrationInstance.sensorMap[sensorId];
    const errors: number[] = [];

    for (let i = 0; i < imagePoints.length; i++) {
        const transformed = calibrationInstance.perspectiveTransform(imagePoints[i].x, imagePoints[i].y, sensor.homography);

        if (transformed) {
            const transformedCoord = { x: transformed[0], y: transformed[1] };
            const error = euclideanDistance(transformedCoord, worldPoints[i]);
            errors.push(error);
        }
    }

    if (errors.length === 0) {
        throw new TransformationError('No valid transformations found', sensorId);
    }

    const meanError = errors.reduce((a, b) => a + b, 0) / errors.length;
    const maxError = Math.max(...errors);
    const reprojectionError = Math.sqrt(errors.reduce((sum, err) => sum + err * err, 0) / errors.length);

    return {
        reprojectionError,
        meanError,
        maxError,
        isReliable: meanError < 0.5 && maxError < 2.0, // meters
    };
}

export {
    Calibration,
    ReverseCalibration,
    transformImageROIToWorld,
    transformImageTripwireToWorld,
    ValidationError,
    TransformationError,
    type ValidationResult,
    type CalibrationStats,
};
