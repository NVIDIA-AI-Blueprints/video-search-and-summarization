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
import React, { useRef } from 'react';
import { StreamType } from 'vst-streaming-lib';
import { useAnalytics } from '../useAnalytics';
// import AnalyticsDrawingControls from './AnalyticsDrawingControls';
// import AnalyticsDrawingStatus from './AnalyticsDrawingStatus';
import AnalyticsDrawingCanvas from './AnalyticsDrawingCanvas';
import AnalyticsNameDialog from './AnalyticsNameDialog';
import AnalyticsExistingItemsSelector from './AnalyticsExistingItemsSelector';

interface AnalyticsRenderProps {
    canvasOverlay: React.ReactNode;
}

interface AnalyticsProps {
    sensor?: {
        sensorId: string;
        name?: string;
    };
    streamType: StreamType;
    videoRef: React.RefObject<HTMLVideoElement>;
    videoWidth: number;
    videoHeight: number;
    enqueueSnackbar: (message: string, options: { variant: 'info' | 'success' | 'error' | 'warning' }) => void;
    children: (props: AnalyticsRenderProps) => React.ReactNode;
}

const Analytics: React.FC<AnalyticsProps> = ({ sensor, streamType, videoRef, videoWidth, videoHeight, enqueueSnackbar, children }) => {
    const drawingCanvasRef = useRef<HTMLCanvasElement>(null);

    const analytics = useAnalytics({
        sensor,
        streamType,
        enqueueSnackbar,
    });

    // Only show drawing controls when we have valid calibration data
    const shouldShowDrawingControls = !analytics.hasCalibrationError && analytics.calibrationData !== null;

    const handleCanvasClick = (event: React.MouseEvent<HTMLCanvasElement>) => {
        analytics.handleCanvasClick(event, videoRef);
    };

    // const handleSave = () => {
    //     analytics.setShowNameDialog(true);
    // };

    // const handleClear = () => {
    //     analytics.clearAllDrawing();
    // };

    const handleNameDialogCancel = () => {
        analytics.setShowNameDialog(false);
    };

    // Canvas overlay that should be positioned over the video
    const canvasOverlay = (
        <AnalyticsDrawingCanvas
            canvasRef={drawingCanvasRef}
            videoRef={videoRef}
            videoWidth={videoWidth}
            videoHeight={videoHeight}
            drawingMode={analytics.drawingMode}
            roiPoints={analytics.roiPoints}
            tripwirePoints={analytics.tripwirePoints}
            directionPoints={analytics.directionPoints}
            tempTripwireStart={analytics.tempTripwireStart}
            tempDirectionStart={analytics.tempDirectionStart}
            onCanvasClick={handleCanvasClick}
            existingROIsForDisplay={analytics.existingROIsForDisplay}
            existingTripwiresForDisplay={analytics.existingTripwiresForDisplay}
        />
    );

    return (
        <>
            {/* Only show drawing components if calibration data is available or loading */}
            {shouldShowDrawingControls && (
                <>
                    {/* Existing Items Selector - appears above video */}
                    <AnalyticsExistingItemsSelector
                        streamType={streamType}
                        calibrationData={analytics.calibrationData}
                        selectedSensor={sensor}
                        selectedROIIds={analytics.selectedROIIds}
                        selectedTripwireIds={analytics.selectedTripwireIds}
                        onROISelectionChange={analytics.handleROISelectionChange}
                        onTripwireSelectionChange={analytics.handleTripwireSelectionChange}
                        isLoadingCalibration={analytics.isLoadingCalibration}
                    />

                    {/* Drawing Controls - appears above video */}
                    {/* <AnalyticsDrawingControls
                        streamType={streamType}
                        drawingMode={analytics.drawingMode}
                        isLoadingCalibration={analytics.isLoadingCalibration}
                        hasCalibrationData={!!analytics.calibrationData}
                        roiPoints={analytics.roiPoints}
                        tripwirePoints={analytics.tripwirePoints}
                        directionPoints={analytics.directionPoints}
                        onDrawingModeChange={analytics.setDrawingMode}
                        onSave={handleSave}
                        onClear={handleClear}
                    /> */}

                    {/* Drawing Status - appears above video */}
                    {/* <AnalyticsDrawingStatus
                        drawingMode={analytics.drawingMode}
                        roiPoints={analytics.roiPoints}
                        tripwirePoints={analytics.tripwirePoints}
                        directionPoints={analytics.directionPoints}
                        calibrationData={analytics.calibrationData}
                    /> */}
                </>
            )}

            {/* Render props for canvas overlay - this appears over the video */}
            {children({
                canvasOverlay,
            })}

            {/* Name Dialog - only show if drawing controls are available */}
            {shouldShowDrawingControls && (
                <AnalyticsNameDialog
                    open={analytics.showNameDialog}
                    roiPoints={analytics.roiPoints}
                    tripwirePoints={analytics.tripwirePoints}
                    directionPoints={analytics.directionPoints}
                    roiName={analytics.roiName}
                    tripwireName={analytics.tripwireName}
                    onRoiNameChange={analytics.setRoiName}
                    onTripwireNameChange={analytics.setTripwireName}
                    onSubmit={analytics.handleNameDialogSubmit}
                    onCancel={handleNameDialogCancel}
                />
            )}
        </>
    );
};

export default Analytics;
