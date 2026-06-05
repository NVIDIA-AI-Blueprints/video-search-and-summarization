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
import React from 'react';
import { Theme } from '@mui/material/styles';
import { StreamType } from 'vst-streaming-lib';

export interface CoordinatePoint {
    x: number;
    y: number;
}

export interface TripwireCoordinates {
    p1: CoordinatePoint;
    p2: CoordinatePoint;
}

export interface CalibrationData {
    version: string;
    osmURL?: string;
    calibrationType: string;
    sensors: {
        id: string;
        origin: { lat: number; lng: number };
        homography?: number[][];
        imageCoordinates?: { x: number; y: number }[];
        globalCoordinates?: { x: number; y: number }[];
        rois?: Array<{
            id: string;
            roiCoordinates: { x: number; y: number; z: number }[];
        }>;
        tripwires?: Array<{
            id: string;
            wire: { p1: { x: number; y: number }; p2: { x: number; y: number } };
            direction?: { p1: { x: number; y: number }; p2: { x: number; y: number } };
        }>;
    }[];
}

export type DrawingMode = 'roi' | 'tripwire-line' | 'tripwire-direction' | 'none';

export interface AnalyticsState {
    drawingMode: DrawingMode;
    roiPoints: CoordinatePoint[];
    tripwirePoints: TripwireCoordinates | null;
    directionPoints: TripwireCoordinates | null;
    tempTripwireStart: CoordinatePoint | null;
    tempDirectionStart: CoordinatePoint | null;
    lastClickTime: number;
    lastClickPoint: CoordinatePoint | null;
    calibrationData: CalibrationData | null;
    isLoadingCalibration: boolean;
    showNameDialog: boolean;
    roiName: string;
    tripwireName: string;
}

export interface AnalyticsProps {
    sensor?: { sensorId: string; name?: string };
    streamType: StreamType;
    videoRef: React.RefObject<HTMLVideoElement>;
    drawingCanvasRef: React.RefObject<HTMLCanvasElement>;
    theme: Theme;
}
