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
import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import { useTheme, alpha } from '@mui/material/styles';
import { DrawingMode, CoordinatePoint, TripwireCoordinates } from './AnalyticsTypes';

interface AnalyticsDrawingCanvasProps {
    canvasRef: React.RefObject<HTMLCanvasElement>;
    videoRef: React.RefObject<HTMLVideoElement>;
    videoWidth: number;
    videoHeight: number;
    drawingMode: DrawingMode;
    roiPoints: CoordinatePoint[];
    tripwirePoints: TripwireCoordinates | null;
    directionPoints: TripwireCoordinates | null;
    tempTripwireStart: CoordinatePoint | null;
    tempDirectionStart: CoordinatePoint | null;
    onCanvasClick: (event: React.MouseEvent<HTMLCanvasElement>, videoRef: React.RefObject<HTMLVideoElement>) => void;
    existingROIsForDisplay?: Array<{ id: string; imageCoords: CoordinatePoint[] }>;
    existingTripwiresForDisplay?: Array<{
        id: string;
        wire: TripwireCoordinates;
        direction?: TripwireCoordinates;
    }>;
}

// Utility function to calculate actual video content area accounting for letterboxing
const getVideoContentArea = (video: HTMLVideoElement) => {
    const videoRect = video.getBoundingClientRect();
    const videoAspectRatio = video.videoWidth / video.videoHeight;
    const containerAspectRatio = videoRect.width / videoRect.height;

    let contentWidth, contentHeight, offsetX, offsetY;

    if (videoAspectRatio > containerAspectRatio) {
        // Video is wider than container - letterboxing on top and bottom
        contentWidth = videoRect.width;
        contentHeight = videoRect.width / videoAspectRatio;
        offsetX = 0;
        offsetY = (videoRect.height - contentHeight) / 2;
    } else {
        // Video is taller than container - letterboxing on left and right
        contentWidth = videoRect.height * videoAspectRatio;
        contentHeight = videoRect.height;
        offsetX = (videoRect.width - contentWidth) / 2;
        offsetY = 0;
    }

    return {
        contentWidth,
        contentHeight,
        offsetX,
        offsetY,
        scaleX: video.videoWidth / contentWidth,
        scaleY: video.videoHeight / contentHeight,
    };
};

const AnalyticsDrawingCanvas: React.FC<AnalyticsDrawingCanvasProps> = ({
    canvasRef,
    videoRef,
    videoWidth,
    videoHeight,
    drawingMode,
    roiPoints,
    tripwirePoints,
    directionPoints,
    tempTripwireStart,
    tempDirectionStart,
    onCanvasClick,
    existingROIsForDisplay,
    existingTripwiresForDisplay,
}) => {
    const theme = useTheme();

    // Memoize existing items to prevent unnecessary redraws
    const stableExistingROIs = useMemo(() => existingROIsForDisplay, [existingROIsForDisplay]);
    const stableExistingTripwires = useMemo(() => existingTripwiresForDisplay, [existingTripwiresForDisplay]);

    // Use requestAnimationFrame to batch canvas updates and reduce flashing
    const animationFrameRef = useRef<number>();

    // Separate function to draw existing items (only redraws when existing items change)
    const drawExistingItems = useCallback(
        (ctx: CanvasRenderingContext2D, scaleX: number, scaleY: number) => {
            // Draw existing ROIs (from selected items)
            if (stableExistingROIs && stableExistingROIs.length > 0) {
                stableExistingROIs.forEach(roiDisplay => {
                    if (roiDisplay.imageCoords.length >= 3) {
                        // Use a different color/style for existing ROIs
                        ctx.strokeStyle = alpha(theme.palette.success.main, 0.8);
                        ctx.fillStyle = alpha(theme.palette.success.main, 0.15);
                        ctx.lineWidth = 2;
                        ctx.setLineDash([5, 5]); // Dashed line for existing items

                        ctx.beginPath();
                        const firstPoint = roiDisplay.imageCoords[0];
                        ctx.moveTo(firstPoint.x / scaleX, firstPoint.y / scaleY);

                        for (let i = 1; i < roiDisplay.imageCoords.length; i++) {
                            const point = roiDisplay.imageCoords[i];
                            ctx.lineTo(point.x / scaleX, point.y / scaleY);
                        }

                        ctx.closePath();
                        ctx.fill();
                        ctx.stroke();
                        ctx.setLineDash([]); // Reset line dash

                        // Draw label for existing ROI
                        const centerX = roiDisplay.imageCoords.reduce((sum, p) => sum + p.x, 0) / roiDisplay.imageCoords.length;
                        const centerY = roiDisplay.imageCoords.reduce((sum, p) => sum + p.y, 0) / roiDisplay.imageCoords.length;

                        ctx.font = 'bold 12px Arial';
                        ctx.fillStyle = theme.palette.success.main;
                        ctx.strokeStyle = theme.palette.background.paper;
                        ctx.lineWidth = 3;
                        ctx.textAlign = 'center';

                        const label = `${roiDisplay.id}`;
                        ctx.strokeText(label, centerX / scaleX, centerY / scaleY);
                        ctx.fillText(label, centerX / scaleX, centerY / scaleY);
                    }
                });
            }

            // Draw existing Tripwires (from selected items)
            if (stableExistingTripwires && stableExistingTripwires.length > 0) {
                stableExistingTripwires.forEach(tripwireDisplay => {
                    // Draw tripwire line with different style for existing items
                    ctx.strokeStyle = alpha(theme.palette.info.main, 0.8);
                    ctx.lineWidth = 3;
                    ctx.setLineDash([8, 4]); // Dashed line for existing items

                    ctx.beginPath();
                    ctx.moveTo(tripwireDisplay.wire.p1.x / scaleX, tripwireDisplay.wire.p1.y / scaleY);
                    ctx.lineTo(tripwireDisplay.wire.p2.x / scaleX, tripwireDisplay.wire.p2.y / scaleY);
                    ctx.stroke();
                    ctx.setLineDash([]); // Reset line dash

                    // Draw tripwire endpoints
                    ctx.fillStyle = theme.palette.info.main;
                    ctx.strokeStyle = theme.palette.background.paper;
                    ctx.lineWidth = 2;

                    // P1
                    ctx.beginPath();
                    ctx.arc(tripwireDisplay.wire.p1.x / scaleX, tripwireDisplay.wire.p1.y / scaleY, 6, 0, 2 * Math.PI);
                    ctx.fill();
                    ctx.stroke();

                    // P2
                    ctx.beginPath();
                    ctx.arc(tripwireDisplay.wire.p2.x / scaleX, tripwireDisplay.wire.p2.y / scaleY, 6, 0, 2 * Math.PI);
                    ctx.fill();
                    ctx.stroke();

                    // Draw label for existing tripwire
                    const centerX = (tripwireDisplay.wire.p1.x + tripwireDisplay.wire.p2.x) / 2;
                    const centerY = (tripwireDisplay.wire.p1.y + tripwireDisplay.wire.p2.y) / 2;

                    ctx.font = 'bold 11px Arial';
                    ctx.fillStyle = theme.palette.info.main;
                    ctx.strokeStyle = theme.palette.background.paper;
                    ctx.lineWidth = 3;
                    ctx.textAlign = 'center';

                    const label = `${tripwireDisplay.id}`;
                    ctx.strokeText(label, centerX / scaleX, centerY / scaleY - 15);
                    ctx.fillText(label, centerX / scaleX, centerY / scaleY - 15);

                    // Draw direction arrow if available
                    if (tripwireDisplay.direction) {
                        ctx.strokeStyle = alpha(theme.palette.warning.main, 0.8);
                        ctx.fillStyle = theme.palette.warning.main;
                        ctx.lineWidth = 2;
                        ctx.setLineDash([4, 4]); // Smaller dash for direction

                        // Draw direction line
                        ctx.beginPath();
                        ctx.moveTo(tripwireDisplay.direction.p1.x / scaleX, tripwireDisplay.direction.p1.y / scaleY);
                        ctx.lineTo(tripwireDisplay.direction.p2.x / scaleX, tripwireDisplay.direction.p2.y / scaleY);
                        ctx.stroke();
                        ctx.setLineDash([]); // Reset line dash

                        // Draw arrowhead
                        const arrowLength = 10;
                        const angle = Math.atan2(
                            tripwireDisplay.direction.p2.y - tripwireDisplay.direction.p1.y,
                            tripwireDisplay.direction.p2.x - tripwireDisplay.direction.p1.x
                        );
                        const arrowAngle = Math.PI / 6;

                        ctx.beginPath();
                        ctx.moveTo(tripwireDisplay.direction.p2.x / scaleX, tripwireDisplay.direction.p2.y / scaleY);
                        ctx.lineTo(
                            tripwireDisplay.direction.p2.x / scaleX - arrowLength * Math.cos(angle - arrowAngle),
                            tripwireDisplay.direction.p2.y / scaleY - arrowLength * Math.sin(angle - arrowAngle)
                        );
                        ctx.moveTo(tripwireDisplay.direction.p2.x / scaleX, tripwireDisplay.direction.p2.y / scaleY);
                        ctx.lineTo(
                            tripwireDisplay.direction.p2.x / scaleX - arrowLength * Math.cos(angle + arrowAngle),
                            tripwireDisplay.direction.p2.y / scaleY - arrowLength * Math.sin(angle + arrowAngle)
                        );
                        ctx.stroke();
                    }
                });
            }
        },
        [theme, stableExistingROIs, stableExistingTripwires]
    );

    // Function to draw current drawing elements
    const drawCurrentElements = useCallback(
        (ctx: CanvasRenderingContext2D, scaleX: number, scaleY: number) => {
            // Draw ROI points and lines
            if (roiPoints.length > 0) {
                ctx.strokeStyle = theme.palette.primary.main;
                ctx.fillStyle = alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.25 : 0.15);
                ctx.lineWidth = 3;

                // Draw ROI polygon if more than 2 points
                if (roiPoints.length > 2) {
                    ctx.beginPath();
                    const firstPoint = roiPoints[0];
                    ctx.moveTo(firstPoint.x / scaleX, firstPoint.y / scaleY);

                    for (let i = 1; i < roiPoints.length; i++) {
                        const point = roiPoints[i];
                        ctx.lineTo(point.x / scaleX, point.y / scaleY);
                    }

                    ctx.closePath();
                    ctx.fill();
                    ctx.stroke();
                } else if (roiPoints.length === 2) {
                    // Draw line for first two points
                    ctx.beginPath();
                    ctx.moveTo(roiPoints[0].x / scaleX, roiPoints[0].y / scaleY);
                    ctx.lineTo(roiPoints[1].x / scaleX, roiPoints[1].y / scaleY);
                    ctx.stroke();
                }

                // Draw ROI points
                ctx.fillStyle = theme.palette.primary.main;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.lineWidth = 2;

                roiPoints.forEach((point, index) => {
                    ctx.beginPath();
                    ctx.arc(point.x / scaleX, point.y / scaleY, 6, 0, 2 * Math.PI);
                    ctx.fill();
                    ctx.stroke();

                    // Draw point labels
                    ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                    ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
                    ctx.font = 'bold 12px Arial';
                    ctx.textAlign = 'center';
                    ctx.lineWidth = 3;

                    ctx.strokeText(`${index + 1}`, point.x / scaleX, point.y / scaleY - 10);
                    ctx.fillText(`${index + 1}`, point.x / scaleX, point.y / scaleY - 10);
                });
            }

            // Draw tripwire line
            if (tripwirePoints) {
                ctx.strokeStyle = theme.palette.secondary.main;
                ctx.lineWidth = 4;
                ctx.beginPath();
                ctx.moveTo(tripwirePoints.p1.x / scaleX, tripwirePoints.p1.y / scaleY);
                ctx.lineTo(tripwirePoints.p2.x / scaleX, tripwirePoints.p2.y / scaleY);
                ctx.stroke();

                // Draw tripwire endpoints
                ctx.fillStyle = theme.palette.secondary.main;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.lineWidth = 2;

                ctx.beginPath();
                ctx.arc(tripwirePoints.p1.x / scaleX, tripwirePoints.p1.y / scaleY, 8, 0, 2 * Math.PI);
                ctx.fill();
                ctx.stroke();

                ctx.beginPath();
                ctx.arc(tripwirePoints.p2.x / scaleX, tripwirePoints.p2.y / scaleY, 8, 0, 2 * Math.PI);
                ctx.fill();
                ctx.stroke();

                // Draw labels
                ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
                ctx.font = 'bold 14px Arial';
                ctx.textAlign = 'center';
                ctx.lineWidth = 3;

                ctx.strokeText('W1', tripwirePoints.p1.x / scaleX, tripwirePoints.p1.y / scaleY - 12);
                ctx.fillText('W1', tripwirePoints.p1.x / scaleX, tripwirePoints.p1.y / scaleY - 12);
                ctx.strokeText('W2', tripwirePoints.p2.x / scaleX, tripwirePoints.p2.y / scaleY - 12);
                ctx.fillText('W2', tripwirePoints.p2.x / scaleX, tripwirePoints.p2.y / scaleY - 12);
            }

            // Draw temporary tripwire start point
            if (tempTripwireStart && drawingMode === 'tripwire-line') {
                ctx.fillStyle = theme.palette.secondary.main;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(tempTripwireStart.x / scaleX, tempTripwireStart.y / scaleY, 8, 0, 2 * Math.PI);
                ctx.fill();
                ctx.stroke();

                ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
                ctx.font = 'bold 12px Arial';
                ctx.textAlign = 'center';
                ctx.lineWidth = 3;

                ctx.strokeText('W1', tempTripwireStart.x / scaleX, tempTripwireStart.y / scaleY - 12);
                ctx.fillText('W1', tempTripwireStart.x / scaleX, tempTripwireStart.y / scaleY - 12);
            }

            // Draw temporary direction start point
            if (tempDirectionStart && drawingMode === 'tripwire-direction') {
                ctx.fillStyle = theme.palette.warning.main;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.arc(tempDirectionStart.x / scaleX, tempDirectionStart.y / scaleY, 8, 0, 2 * Math.PI);
                ctx.fill();
                ctx.stroke();

                ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
                ctx.font = 'bold 12px Arial';
                ctx.textAlign = 'center';
                ctx.lineWidth = 3;

                ctx.strokeText('D1', tempDirectionStart.x / scaleX, tempDirectionStart.y / scaleY - 12);
                ctx.fillText('D1', tempDirectionStart.x / scaleX, tempDirectionStart.y / scaleY - 12);
            }

            // Draw direction line
            if (directionPoints) {
                ctx.strokeStyle = theme.palette.warning.main;
                ctx.fillStyle = theme.palette.warning.main;
                ctx.lineWidth = 3;
                ctx.setLineDash([8, 4]);

                ctx.beginPath();
                ctx.moveTo(directionPoints.p1.x / scaleX, directionPoints.p1.y / scaleY);
                ctx.lineTo(directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY);
                ctx.stroke();
                ctx.setLineDash([]);

                // Draw arrowhead
                const arrowLength = 15;
                const angle = Math.atan2(directionPoints.p2.y - directionPoints.p1.y, directionPoints.p2.x - directionPoints.p1.x);
                const arrowAngle = Math.PI / 6;

                ctx.beginPath();
                ctx.moveTo(directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY);
                ctx.lineTo(
                    directionPoints.p2.x / scaleX - arrowLength * Math.cos(angle - arrowAngle),
                    directionPoints.p2.y / scaleY - arrowLength * Math.sin(angle - arrowAngle)
                );
                ctx.moveTo(directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY);
                ctx.lineTo(
                    directionPoints.p2.x / scaleX - arrowLength * Math.cos(angle + arrowAngle),
                    directionPoints.p2.y / scaleY - arrowLength * Math.sin(angle + arrowAngle)
                );
                ctx.stroke();

                // Draw direction point labels
                ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
                ctx.font = 'bold 12px Arial';
                ctx.textAlign = 'center';
                ctx.lineWidth = 3;

                ctx.strokeText('D1', directionPoints.p1.x / scaleX, directionPoints.p1.y / scaleY - 12);
                ctx.fillText('D1', directionPoints.p1.x / scaleX, directionPoints.p1.y / scaleY - 12);
                ctx.strokeText('D2', directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY - 12);
                ctx.fillText('D2', directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY - 12);
            }
        },
        [drawingMode, roiPoints, tripwirePoints, directionPoints, tempTripwireStart, tempDirectionStart, theme]
    );

    // Batched canvas drawing function
    const scheduleCanvasRedraw = useCallback(() => {
        if (animationFrameRef.current) {
            cancelAnimationFrame(animationFrameRef.current);
        }
        animationFrameRef.current = requestAnimationFrame(() => {
            const canvas = canvasRef.current;
            const video = videoRef.current;
            if (!canvas || !video) return;

            // Check if there's anything to draw (including existing items)
            const hasDrawnElements = roiPoints.length > 0 || tripwirePoints || directionPoints;
            const hasExistingItems =
                (stableExistingROIs && stableExistingROIs.length > 0) || (stableExistingTripwires && stableExistingTripwires.length > 0);

            // Only skip drawing if there's absolutely nothing to show
            if (drawingMode === 'none' && !hasDrawnElements && !hasExistingItems) {
                return;
            }

            const ctx = canvas.getContext('2d');
            if (!ctx) return;

            // Clear canvas
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            // Calculate scaling factors since canvas matches video content area
            const scaleX = video.videoWidth / canvas.width;
            const scaleY = video.videoHeight / canvas.height;

            // Draw existing items first (so they appear behind new drawings)
            drawExistingItems(ctx, scaleX, scaleY);

            // Draw current drawing elements
            drawCurrentElements(ctx, scaleX, scaleY);
        });
    }, [
        drawingMode,
        roiPoints,
        tripwirePoints,
        directionPoints,
        tempTripwireStart,
        tempDirectionStart,
        stableExistingROIs,
        stableExistingTripwires,
        canvasRef,
        videoRef,
        drawExistingItems,
        drawCurrentElements,
    ]);

    // Redraw canvas when state changes
    useEffect(() => {
        scheduleCanvasRedraw();
    }, [scheduleCanvasRedraw]);

    // Initialize drawing overlay canvas with video content dimensions
    useEffect(() => {
        const canvas = canvasRef.current;
        const video = videoRef.current;
        const hasDrawnElements = roiPoints.length > 0 || tripwirePoints || directionPoints;
        const hasExistingItems =
            (stableExistingROIs && stableExistingROIs.length > 0) || (stableExistingTripwires && stableExistingTripwires.length > 0);

        if (canvas && video && (drawingMode !== 'none' || hasDrawnElements || hasExistingItems)) {
            // Calculate video content area
            const { contentWidth, contentHeight } = getVideoContentArea(video);

            // Set canvas size to match the video content area
            canvas.width = contentWidth;
            canvas.height = contentHeight;

            // Redraw canvas after resizing
            setTimeout(() => scheduleCanvasRedraw(), 0);
        }
    }, [
        drawingMode,
        videoWidth,
        videoHeight,
        roiPoints.length,
        tripwirePoints,
        directionPoints,
        scheduleCanvasRedraw,
        stableExistingROIs,
        stableExistingTripwires,
    ]);

    // Cleanup animation frames on unmount
    useEffect(() => {
        return () => {
            if (animationFrameRef.current) {
                cancelAnimationFrame(animationFrameRef.current);
            }
        };
    }, []);

    // Check if there's anything to show before returning null
    const hasDrawnElements = roiPoints.length > 0 || tripwirePoints || directionPoints;
    const hasExistingItems =
        (stableExistingROIs && stableExistingROIs.length > 0) || (stableExistingTripwires && stableExistingTripwires.length > 0);

    if (drawingMode === 'none' && !hasDrawnElements && !hasExistingItems) {
        return null;
    }

    // Calculate video content area for canvas positioning
    const video = videoRef.current;
    let canvasStyle: React.CSSProperties = {
        position: 'absolute',
        cursor: drawingMode !== 'none' ? 'crosshair' : 'default',
        pointerEvents: drawingMode !== 'none' ? 'auto' : 'none',
        zIndex: 5,
    };

    if (video) {
        // Position canvas relative to video container
        const { contentWidth, contentHeight, offsetX, offsetY } = getVideoContentArea(video);
        canvasStyle = {
            ...canvasStyle,
            width: `${contentWidth}px`,
            height: `${contentHeight}px`,
            left: `${offsetX}px`,
            top: `${offsetY}px`,
        };
    } else {
        // Fallback if video not ready
        canvasStyle = {
            ...canvasStyle,
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
        };
    }

    return <canvas ref={canvasRef} onClick={event => onCanvasClick(event, videoRef)} style={canvasStyle} />;
};

export default AnalyticsDrawingCanvas;
