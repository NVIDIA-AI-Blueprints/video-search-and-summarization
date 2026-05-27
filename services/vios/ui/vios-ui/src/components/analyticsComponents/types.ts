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
export interface CalibrationData {
    sensors: Array<{
        id: string;
        origin: {
            lat: number;
            lng: number;
        };
        homography?: number[][]; // Optional for 2D use cases
        geoLocation?: {
            lat: number;
            lng: number;
        };
        // 2D calibration specific fields
        imageCoordinates?: { x: number; y: number }[];
        globalCoordinates?: { x: number; y: number }[];
        scaleFactor?: number;
        coordinates?: { x: number; y: number };
        // Sensor-specific ROIs and tripwires
        rois?: Array<{
            id: string;
            roiCoordinates: { x: number; y: number; z?: number }[];
            sensors?: string[];
            groups?: string[];
            type?: string;
            restrictedObjectTypes?: string[];
            confinedObjectTypes?: string[];
        }>;
        tripwires?: Array<{
            id: string;
            wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
            direction?: { p1: { x: number; y: number }; p2: { x: number; y: number } };
        }>;
        attributes?: Array<{
            name: string;
            value: string;
        }>;
        place?: Array<{
            name: string;
            value: string;
        }>;
        type?: string;
        [key: string]: unknown;
    }>;
    // Root-level ROIs and tripwires (for 3D use cases)
    rois?: Array<{
        id: string;
        roiCoordinates: { x: number; y: number; z?: number }[];
        sensors?: string[];
        groups?: string[];
        type?: string;
        restrictedObjectTypes?: string[];
        confinedObjectTypes?: string[];
    }>;
    tripwires?: Array<{
        id: string;
        wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
        direction?: { p1: { x: number; y: number }; p2: { x: number; y: number } };
    }>;
    calibrationType: string;
    version: string;
    osmURL?: string;
    [key: string]: unknown;
}

export interface ROITransformResults {
    imageCoords: { x: number; y: number }[];
    worldCoords: { x: number; y: number; z: number }[];
}

export interface TripwireTransformResults {
    imageCoords: { p1: { x: number; y: number }; p2: { x: number; y: number } };
    worldCoords: { p1: { x: number; y: number; z: number }; p2: { x: number; y: number; z: number } };
}

export interface ReverseTransformResults {
    worldCoords: { x: number; y: number; z?: number }[];
    imageCoords: { x: number; y: number }[];
}

export interface ReverseTripwireResults {
    worldCoords: {
        wire: { p1: { x: number; y: number; z?: number }; p2: { x: number; y: number; z?: number } };
        direction?: { p1: { x: number; y: number; z?: number }; p2: { x: number; y: number; z?: number } };
    };
    imageCoords: {
        wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
        direction?: { p1: { x: number; y: number }; p2: { x: number; y: number } };
    };
}

export interface CoordinatePoint {
    x: number;
    y: number;
}

export interface TripwireCoordinates {
    p1: { x: number; y: number };
    p2: { x: number; y: number };
}
