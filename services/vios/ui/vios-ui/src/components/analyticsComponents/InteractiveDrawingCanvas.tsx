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
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, Typography, Box, Button, ButtonGroup, Alert, Chip, Stack } from '@mui/material';
import { useTheme, alpha } from '@mui/material/styles';
import { Sensor } from '../../interfaces/interfaces';
import config from '../../config';
import nvAxios from '../../services/Axios';

export interface CoordinatePoint {
    x: number;
    y: number;
}

export interface TripwireCoordinates {
    p1: CoordinatePoint;
    p2: CoordinatePoint;
}

type DrawingMode = 'roi' | 'tripwire-line' | 'tripwire-direction' | 'none';

interface InteractiveDrawingCanvasProps {
    sensor: Sensor | null;
    frameWidth: number;
    frameHeight: number;
    onROIChange: (coords: CoordinatePoint[]) => void;
    onTripwireChange: (coords: TripwireCoordinates) => void;
    onTripwireDirectionChange: (direction: TripwireCoordinates) => void;
    initialROI?: CoordinatePoint[];
    initialTripwire?: TripwireCoordinates;
    initialTripwireDirection?: TripwireCoordinates;
}

const InteractiveDrawingCanvas: React.FC<InteractiveDrawingCanvasProps> = ({
    sensor,
    frameWidth,
    frameHeight,
    onROIChange,
    onTripwireChange,
    onTripwireDirectionChange,
    initialROI = [],
    initialTripwire,
    initialTripwireDirection,
}) => {
    const theme = useTheme();
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [backgroundImage, setBackgroundImage] = useState<HTMLImageElement | null>(null);
    const [imageError, setImageError] = useState<string | null>(null);
    const [isLoadingImage, setIsLoadingImage] = useState(false);

    // Drawing state
    const [drawingMode, setDrawingMode] = useState<DrawingMode>('none');
    const [roiPoints, setROIPoints] = useState<CoordinatePoint[]>(initialROI);
    const [tripwirePoints, setTripwirePoints] = useState<TripwireCoordinates | null>(initialTripwire || null);
    const [directionPoints, setDirectionPoints] = useState<TripwireCoordinates | null>(initialTripwireDirection || null);

    // Temporary drawing state
    const [tempTripwireStart, setTempTripwireStart] = useState<CoordinatePoint | null>(null);
    const [tempDirectionStart, setTempDirectionStart] = useState<CoordinatePoint | null>(null);

    // Double-click detection
    const [lastClickTime, setLastClickTime] = useState<number>(0);
    const [lastClickPoint, setLastClickPoint] = useState<CoordinatePoint | null>(null);

    // Calculate responsive canvas size while maintaining aspect ratio
    const maxCanvasWidth = 800;
    const aspectRatio = frameWidth / frameHeight;
    const canvasWidth = Math.min(maxCanvasWidth, frameWidth);
    const canvasHeight = canvasWidth / aspectRatio;

    // Scale factors for drawing coordinates
    const scaleX = frameWidth / canvasWidth;
    const scaleY = frameHeight / canvasHeight;

    // Fetch live picture from sensor
    const fetchLivePicture = useCallback(async () => {
        if (!sensor) return;

        setIsLoadingImage(true);
        setImageError(null);

        try {
            const endpoint = `${config.liveStreamEndpoint}/api/v1/live/stream/${sensor.sensorId}/picture`;
            const response = await nvAxios.get(endpoint, {
                responseType: 'blob',
                headers: { streamId: sensor.sensorId },
            });

            const binaryData = [];
            binaryData.push(response.data);
            const imageUrl = window.URL.createObjectURL(new Blob(binaryData, { type: 'image/jpeg' }));

            const img = new Image();
            img.onload = () => {
                setBackgroundImage(img);
                setIsLoadingImage(false);
            };
            img.onerror = () => {
                setImageError('Failed to load camera image');
                setIsLoadingImage(false);
            };
            img.src = imageUrl;
        } catch (error) {
            console.error('Error fetching live picture:', error);
            setImageError(error instanceof Error ? error.message : 'Unknown error');
            setIsLoadingImage(false);
        }
    }, [sensor]);

    // Reset drawing state when sensor changes
    useEffect(() => {
        if (sensor) {
            // Clear all existing drawing data when switching sensors
            setROIPoints([]);
            setTripwirePoints(null);
            setDirectionPoints(null);
            setTempTripwireStart(null);
            setTempDirectionStart(null);
            setDrawingMode('none');

            // Clear the callbacks to parent component
            onROIChange([]);
            onTripwireChange({ p1: { x: 0, y: 0 }, p2: { x: 0, y: 0 } });
            onTripwireDirectionChange({ p1: { x: 0, y: 0 }, p2: { x: 0, y: 0 } });

            // Fetch fresh image
            fetchLivePicture();
        }
    }, [sensor, fetchLivePicture, onROIChange, onTripwireChange, onTripwireDirectionChange]);

    // Clear canvas when parent clears the coordinates (e.g., after successful POST)
    useEffect(() => {
        // Check if parent has cleared ROI coordinates
        if (initialROI.length === 0 && roiPoints.length > 0) {
            setROIPoints([]);
            setDrawingMode('none');
        }

        // Check if parent has cleared tripwire coordinates
        if (
            initialTripwire &&
            initialTripwire.p1.x === 0 &&
            initialTripwire.p1.y === 0 &&
            initialTripwire.p2.x === 0 &&
            initialTripwire.p2.y === 0 &&
            tripwirePoints
        ) {
            setTripwirePoints(null);
            setTempTripwireStart(null);
            if (drawingMode === 'tripwire-line') {
                setDrawingMode('none');
            }
        }

        // Check if parent has cleared direction coordinates
        if (
            initialTripwireDirection &&
            initialTripwireDirection.p1.x === 0 &&
            initialTripwireDirection.p1.y === 0 &&
            initialTripwireDirection.p2.x === 0 &&
            initialTripwireDirection.p2.y === 0 &&
            directionPoints
        ) {
            setDirectionPoints(null);
            setTempDirectionStart(null);
            if (drawingMode === 'tripwire-direction') {
                setDrawingMode('none');
            }
        }
    }, [initialROI, initialTripwire, initialTripwireDirection, roiPoints.length, tripwirePoints, directionPoints, drawingMode]);

    // Handle canvas drawing
    const drawCanvas = useCallback(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        // Clear canvas
        ctx.clearRect(0, 0, canvasWidth, canvasHeight);

        // Draw background image or solid color with better theme integration
        if (backgroundImage) {
            // Draw the live camera feed as background, scaled to fit canvas
            ctx.drawImage(backgroundImage, 0, 0, canvasWidth, canvasHeight);

            // Add a subtle overlay to improve contrast for overlays
            ctx.fillStyle = alpha(theme.palette.background.paper, theme.palette.mode === 'dark' ? 0.1 : 0.05);
            ctx.fillRect(0, 0, canvasWidth, canvasHeight);
        } else {
            // Draw solid background with better theme integration
            ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.grey[900] : theme.palette.grey[50];
            ctx.fillRect(0, 0, canvasWidth, canvasHeight);
        }

        // Draw frame border with better contrast
        ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.grey[600] : theme.palette.grey[400];
        ctx.lineWidth = 2;
        ctx.strokeRect(0, 0, canvasWidth, canvasHeight);

        // Draw grid for reference with better visibility
        ctx.strokeStyle = theme.palette.mode === 'dark' ? alpha(theme.palette.grey[500], 0.3) : alpha(theme.palette.grey[400], 0.4);
        ctx.lineWidth = 1;
        ctx.setLineDash([2, 2]);

        // Grid lines every 100 pixels on original frame
        for (let x = 0; x <= frameWidth; x += 100) {
            const scaledX = x / scaleX;
            if (scaledX <= canvasWidth) {
                ctx.beginPath();
                ctx.moveTo(scaledX, 0);
                ctx.lineTo(scaledX, canvasHeight);
                ctx.stroke();
            }
        }

        for (let y = 0; y <= frameHeight; y += 100) {
            const scaledY = y / scaleY;
            if (scaledY <= canvasHeight) {
                ctx.beginPath();
                ctx.moveTo(0, scaledY);
                ctx.lineTo(canvasWidth, scaledY);
                ctx.stroke();
            }
        }
        ctx.setLineDash([]);

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

            // Draw ROI points with better visibility
            ctx.fillStyle = theme.palette.primary.main;
            ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
            ctx.lineWidth = 2;

            roiPoints.forEach((point, index) => {
                ctx.beginPath();
                ctx.arc(point.x / scaleX, point.y / scaleY, 6, 0, 2 * Math.PI);
                ctx.fill();
                ctx.stroke();

                // Draw point labels with better contrast
                ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
                ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
                ctx.font = 'bold 12px Arial';
                ctx.textAlign = 'center';
                ctx.lineWidth = 3;

                // Text with stroke for better visibility
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

            // Draw tripwire endpoints with better contrast
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

            // Draw labels with better contrast
            ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
            ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
            ctx.font = 'bold 14px Arial';
            ctx.textAlign = 'center';
            ctx.lineWidth = 3;

            // Text with stroke for better visibility
            ctx.strokeText('W1', tripwirePoints.p1.x / scaleX, tripwirePoints.p1.y / scaleY - 12);
            ctx.fillText('W1', tripwirePoints.p1.x / scaleX, tripwirePoints.p1.y / scaleY - 12);
            ctx.strokeText('W2', tripwirePoints.p2.x / scaleX, tripwirePoints.p2.y / scaleY - 12);
            ctx.fillText('W2', tripwirePoints.p2.x / scaleX, tripwirePoints.p2.y / scaleY - 12);
        }

        // Draw temporary tripwire start point (while drawing)
        if (tempTripwireStart && drawingMode === 'tripwire-line') {
            ctx.fillStyle = theme.palette.secondary.main;
            ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(tempTripwireStart.x / scaleX, tempTripwireStart.y / scaleY, 8, 0, 2 * Math.PI);
            ctx.fill();
            ctx.stroke();

            // Draw label with better contrast
            ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
            ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
            ctx.font = 'bold 12px Arial';
            ctx.textAlign = 'center';
            ctx.lineWidth = 3;

            ctx.strokeText('W1', tempTripwireStart.x / scaleX, tempTripwireStart.y / scaleY - 12);
            ctx.fillText('W1', tempTripwireStart.x / scaleX, tempTripwireStart.y / scaleY - 12);
        }

        // Draw temporary direction start point (while drawing)
        if (tempDirectionStart && drawingMode === 'tripwire-direction') {
            ctx.fillStyle = theme.palette.warning.main;
            ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(tempDirectionStart.x / scaleX, tempDirectionStart.y / scaleY, 8, 0, 2 * Math.PI);
            ctx.fill();
            ctx.stroke();

            // Draw label with better contrast
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
            ctx.setLineDash([8, 4]); // Better dashed line pattern

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
                directionPoints.p2.x / scaleX - (arrowLength * Math.cos(angle - arrowAngle)) / scaleX,
                directionPoints.p2.y / scaleY - (arrowLength * Math.sin(angle - arrowAngle)) / scaleY
            );
            ctx.moveTo(directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY);
            ctx.lineTo(
                directionPoints.p2.x / scaleX - (arrowLength * Math.cos(angle + arrowAngle)) / scaleX,
                directionPoints.p2.y / scaleY - (arrowLength * Math.sin(angle + arrowAngle)) / scaleY
            );
            ctx.stroke();

            // Draw direction point labels with better contrast
            ctx.fillStyle = theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black;
            ctx.strokeStyle = theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white;
            ctx.font = 'bold 12px Arial';
            ctx.textAlign = 'center';
            ctx.lineWidth = 3;

            // Text with stroke for better visibility
            ctx.strokeText('D1', directionPoints.p1.x / scaleX, directionPoints.p1.y / scaleY - 12);
            ctx.fillText('D1', directionPoints.p1.x / scaleX, directionPoints.p1.y / scaleY - 12);
            ctx.strokeText('D2', directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY - 12);
            ctx.fillText('D2', directionPoints.p2.x / scaleX, directionPoints.p2.y / scaleY - 12);
        }

        // Draw coordinate info with better theme integration
        const infoBoxHeight = 75;
        const infoBoxWidth = 200;

        // Draw semi-transparent background for info text (more transparent for better visibility while drawing)
        ctx.fillStyle = alpha(theme.palette.background.paper, theme.palette.mode === 'dark' ? 0.4 : 0.3);
        ctx.fillRect(5, 5, infoBoxWidth, infoBoxHeight);

        // Draw border around info box (more subtle)
        ctx.strokeStyle = alpha(theme.palette.divider, 0.5);
        ctx.lineWidth = 1;
        ctx.strokeRect(5, 5, infoBoxWidth, infoBoxHeight);

        // Draw info text
        ctx.fillStyle = theme.palette.text.primary;
        ctx.font = '12px Arial';
        ctx.textAlign = 'left';
        ctx.fillText(`Frame: ${frameWidth}x${frameHeight}`, 10, 25);
        ctx.fillText(`Canvas: ${canvasWidth.toFixed(0)}x${canvasHeight.toFixed(0)}`, 10, 40);
        ctx.fillText(`Scale: ${scaleX.toFixed(3)}x, ${scaleY.toFixed(3)}y`, 10, 55);

        // Draw mode indicator with better styling
        if (drawingMode !== 'none') {
            const modeText = `Mode: ${drawingMode.toUpperCase()}`;
            ctx.font = 'bold 14px Arial';
            ctx.textAlign = 'right';

            // Measure text to create background box
            const textMetrics = ctx.measureText(modeText);
            const textWidth = textMetrics.width;
            const textHeight = 20;
            const padding = 8;

            // Draw background for mode indicator
            ctx.fillStyle = alpha(theme.palette.info.main, 0.2);
            ctx.fillRect(canvasWidth - textWidth - padding * 2, 5, textWidth + padding * 2, textHeight + padding);

            // Draw border
            ctx.strokeStyle = theme.palette.info.main;
            ctx.lineWidth = 1;
            ctx.strokeRect(canvasWidth - textWidth - padding * 2, 5, textWidth + padding * 2, textHeight + padding);

            // Draw mode text
            ctx.fillStyle = theme.palette.info.main;
            ctx.fillText(modeText, canvasWidth - padding, 25);
        }
    }, [
        canvasWidth,
        canvasHeight,
        frameWidth,
        frameHeight,
        scaleX,
        scaleY,
        roiPoints,
        tripwirePoints,
        directionPoints,
        tempTripwireStart,
        tempDirectionStart,
        backgroundImage,
        theme,
        drawingMode,
    ]);

    // Redraw canvas when state changes
    useEffect(() => {
        drawCanvas();
    }, [drawCanvas]);

    // Handle canvas click
    const handleCanvasClick = useCallback(
        (event: React.MouseEvent<HTMLCanvasElement>) => {
            const canvas = canvasRef.current;
            if (!canvas) return;

            const rect = canvas.getBoundingClientRect();
            const canvasX = event.clientX - rect.left;
            const canvasY = event.clientY - rect.top;

            // Convert to image coordinates
            const imageX = Math.round(canvasX * scaleX);
            const imageY = Math.round(canvasY * scaleY);

            const clickPoint = { x: imageX, y: imageY };
            const currentTime = Date.now();

            // Check for double-click (within 300ms and close proximity)
            const isDoubleClick =
                currentTime - lastClickTime < 300 &&
                lastClickPoint &&
                Math.abs(clickPoint.x - lastClickPoint.x) < 10 &&
                Math.abs(clickPoint.y - lastClickPoint.y) < 10;

            if (drawingMode === 'roi') {
                if (isDoubleClick && roiPoints.length >= 3) {
                    // Close the ROI polygon on double-click
                    onROIChange([...roiPoints]);
                    setDrawingMode('none');
                } else {
                    // Add new ROI point
                    const newPoints = [...roiPoints, clickPoint];
                    setROIPoints(newPoints);
                    onROIChange(newPoints);
                }
            } else if (drawingMode === 'tripwire-line') {
                if (!tempTripwireStart) {
                    // First point of tripwire
                    setTempTripwireStart(clickPoint);
                } else {
                    // Second point of tripwire - complete the line
                    const newTripwire = { p1: tempTripwireStart, p2: clickPoint };
                    setTripwirePoints(newTripwire);
                    onTripwireChange(newTripwire);
                    setTempTripwireStart(null);
                    setDrawingMode('none');
                }
            } else if (drawingMode === 'tripwire-direction') {
                if (!tempDirectionStart) {
                    // First point of direction
                    setTempDirectionStart(clickPoint);
                } else {
                    // Second point of direction - complete the direction
                    const newDirection = { p1: tempDirectionStart, p2: clickPoint };
                    setDirectionPoints(newDirection);
                    onTripwireDirectionChange(newDirection);
                    setTempDirectionStart(null);
                    setDrawingMode('none');
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
            scaleX,
            scaleY,
            lastClickTime,
            lastClickPoint,
            onROIChange,
            onTripwireChange,
            onTripwireDirectionChange,
        ]
    );

    // Clear functions
    const clearROI = () => {
        setROIPoints([]);
        onROIChange([]);
        setDrawingMode('none');
    };

    const clearTripwire = () => {
        setTripwirePoints(null);
        onTripwireChange({ p1: { x: 0, y: 0 }, p2: { x: 0, y: 0 } });
        setTempTripwireStart(null);
        setDrawingMode('none');
    };

    const clearDirection = () => {
        setDirectionPoints(null);
        onTripwireDirectionChange({ p1: { x: 0, y: 0 }, p2: { x: 0, y: 0 } });
        setTempDirectionStart(null);
        setDrawingMode('none');
    };

    return (
        <Card
            sx={{
                bgcolor: theme.palette.background.paper,
                borderColor: theme.palette.divider,
            }}
        >
            <CardHeader
                title='Interactive Drawing Canvas'
                subheader={
                    sensor ? `Draw ROI and Tripwire on ${sensor.name || sensor.sensorId} camera feed` : 'Select a sensor to start drawing'
                }
                sx={{
                    bgcolor: alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.1 : 0.05),
                    borderBottom: `1px solid ${theme.palette.divider}`,
                }}
                action={
                    <Box display='flex' gap={1} alignItems='center' justifyContent='space-between' width='100%'>
                        <Box display='flex' gap={1} alignItems='center'>
                            {sensor && (
                                <>
                                    <Chip
                                        label={`Camera: ${sensor.name || sensor.sensorId}`}
                                        color='info'
                                        size='small'
                                        sx={{
                                            bgcolor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.3 : 0.1),
                                            color: theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black,
                                            fontWeight: 'medium',
                                        }}
                                    />
                                    {isLoadingImage && (
                                        <Chip
                                            label='Loading...'
                                            color='warning'
                                            size='small'
                                            sx={{
                                                bgcolor: alpha(theme.palette.warning.main, theme.palette.mode === 'dark' ? 0.3 : 0.1),
                                                color:
                                                    theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black,
                                                fontWeight: 'medium',
                                            }}
                                        />
                                    )}
                                </>
                            )}
                        </Box>
                        {sensor && (
                            <Button
                                size='small'
                                onClick={fetchLivePicture}
                                disabled={isLoadingImage}
                                sx={{
                                    color: theme.palette.primary.main,
                                    borderColor: theme.palette.primary.main,
                                    '&:hover': {
                                        bgcolor: alpha(theme.palette.primary.main, 0.1),
                                    },
                                }}
                                variant='outlined'
                            >
                                Refresh Image
                            </Button>
                        )}
                    </Box>
                }
            />
            <CardContent>
                {imageError && (
                    <Alert
                        severity='error'
                        sx={{
                            mb: 2,
                            bgcolor: alpha(theme.palette.error.main, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                            color: theme.palette.error.main,
                            '& .MuiAlert-icon': {
                                color: theme.palette.error.main,
                            },
                        }}
                    >
                        Failed to load camera feed: {imageError}
                    </Alert>
                )}

                {!sensor && (
                    <Alert
                        severity='info'
                        sx={{
                            mb: 2,
                            bgcolor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                            color: theme.palette.info.main,
                            '& .MuiAlert-icon': {
                                color: theme.palette.info.main,
                            },
                        }}
                    >
                        Please select a sensor and load calibration data to start drawing coordinates.
                    </Alert>
                )}

                {sensor && (
                    <>
                        {/* Drawing Controls */}
                        <Stack direction='row' spacing={2} sx={{ mb: 2 }} flexWrap='wrap'>
                            <ButtonGroup variant='outlined' size='small'>
                                <Button
                                    onClick={() => setDrawingMode('roi')}
                                    color={drawingMode === 'roi' ? 'primary' : 'inherit'}
                                    variant={drawingMode === 'roi' ? 'contained' : 'outlined'}
                                    sx={{
                                        '&:hover': {
                                            bgcolor: drawingMode === 'roi' ? undefined : alpha(theme.palette.primary.main, 0.1),
                                        },
                                    }}
                                >
                                    Draw ROI
                                </Button>
                                <Button
                                    onClick={() => setDrawingMode('tripwire-line')}
                                    color={drawingMode === 'tripwire-line' ? 'secondary' : 'inherit'}
                                    variant={drawingMode === 'tripwire-line' ? 'contained' : 'outlined'}
                                    sx={{
                                        '&:hover': {
                                            bgcolor: drawingMode === 'tripwire-line' ? undefined : alpha(theme.palette.secondary.main, 0.1),
                                        },
                                    }}
                                >
                                    Draw Tripwire
                                </Button>
                                <Button
                                    onClick={() => setDrawingMode('tripwire-direction')}
                                    color={drawingMode === 'tripwire-direction' ? 'warning' : 'inherit'}
                                    variant={drawingMode === 'tripwire-direction' ? 'contained' : 'outlined'}
                                    sx={{
                                        '&:hover': {
                                            bgcolor:
                                                drawingMode === 'tripwire-direction' ? undefined : alpha(theme.palette.warning.main, 0.1),
                                        },
                                    }}
                                >
                                    Draw Direction
                                </Button>
                            </ButtonGroup>

                            <ButtonGroup variant='outlined' size='small' color='error'>
                                <Button
                                    onClick={clearROI}
                                    disabled={roiPoints.length === 0}
                                    sx={{
                                        '&:hover:not(:disabled)': {
                                            bgcolor: alpha(theme.palette.error.main, 0.1),
                                        },
                                    }}
                                >
                                    Clear ROI
                                </Button>
                                <Button
                                    onClick={clearTripwire}
                                    disabled={!tripwirePoints}
                                    sx={{
                                        '&:hover:not(:disabled)': {
                                            bgcolor: alpha(theme.palette.error.main, 0.1),
                                        },
                                    }}
                                >
                                    Clear Tripwire
                                </Button>
                                <Button
                                    onClick={clearDirection}
                                    disabled={!directionPoints}
                                    sx={{
                                        '&:hover:not(:disabled)': {
                                            bgcolor: alpha(theme.palette.error.main, 0.1),
                                        },
                                    }}
                                >
                                    Clear Direction
                                </Button>
                            </ButtonGroup>

                            <Button
                                variant='outlined'
                                onClick={() => setDrawingMode('none')}
                                disabled={drawingMode === 'none'}
                                sx={{
                                    '&:hover:not(:disabled)': {
                                        bgcolor: alpha(theme.palette.text.primary, 0.1),
                                    },
                                }}
                            >
                                Stop Drawing
                            </Button>
                        </Stack>

                        {/* Drawing Instructions */}
                        {drawingMode !== 'none' && (
                            <Alert
                                severity='info'
                                sx={{
                                    mb: 2,
                                    bgcolor: alpha(theme.palette.info.main, theme.palette.mode === 'dark' ? 0.15 : 0.1),
                                    color: theme.palette.info.main,
                                    '& .MuiAlert-icon': {
                                        color: theme.palette.info.main,
                                    },
                                }}
                            >
                                {drawingMode === 'roi' &&
                                    'Click to add ROI points. Double-click to close the polygon (minimum 3 points required).'}
                                {drawingMode === 'tripwire-line' && 'Click two points to define the tripwire line.'}
                                {drawingMode === 'tripwire-direction' && 'Click two points to define the detection direction arrow.'}
                            </Alert>
                        )}

                        {/* Canvas */}
                        <Box
                            display='flex'
                            justifyContent='center'
                            sx={{
                                border: `2px solid ${theme.palette.divider}`,
                                borderRadius: 2,
                                p: 2,
                                bgcolor:
                                    theme.palette.mode === 'dark'
                                        ? alpha(theme.palette.grey[900], 0.5)
                                        : alpha(theme.palette.grey[100], 0.5),
                                backgroundImage:
                                    theme.palette.mode === 'dark'
                                        ? 'linear-gradient(45deg, transparent 25%, rgba(255,255,255,0.02) 25%, rgba(255,255,255,0.02) 50%, transparent 50%, transparent 75%, rgba(255,255,255,0.02) 75%)'
                                        : 'linear-gradient(45deg, transparent 25%, rgba(0,0,0,0.02) 25%, rgba(0,0,0,0.02) 50%, transparent 50%, transparent 75%, rgba(0,0,0,0.02) 75%)',
                                backgroundSize: '20px 20px',
                            }}
                        >
                            <canvas
                                ref={canvasRef}
                                width={canvasWidth}
                                height={canvasHeight}
                                onClick={handleCanvasClick}
                                style={{
                                    maxWidth: '100%',
                                    height: 'auto',
                                    border: `2px solid ${theme.palette.divider}`,
                                    borderRadius: '8px',
                                    cursor: drawingMode !== 'none' ? 'crosshair' : 'default',
                                    boxShadow: theme.shadows[4],
                                    backgroundColor: theme.palette.background.paper,
                                }}
                            />
                        </Box>

                        {/* Status Display with better theme integration */}
                        <Box
                            mt={2}
                            display='flex'
                            justifyContent='center'
                            gap={3}
                            flexWrap='wrap'
                            sx={{
                                p: 2,
                                bgcolor: alpha(theme.palette.background.default, 0.5),
                                borderRadius: 2,
                                border: `1px solid ${theme.palette.divider}`,
                            }}
                        >
                            <Box display='flex' alignItems='center' gap={1}>
                                <Box
                                    width={16}
                                    height={16}
                                    bgcolor={theme.palette.primary.main}
                                    borderRadius='50%'
                                    sx={{
                                        boxShadow: `0 0 0 2px ${alpha(theme.palette.primary.main, 0.3)}`,
                                    }}
                                />
                                <Typography
                                    variant='caption'
                                    sx={{
                                        color: theme.palette.text.primary,
                                        fontWeight: 'medium',
                                    }}
                                >
                                    ROI ({roiPoints.length} points)
                                </Typography>
                            </Box>
                            <Box display='flex' alignItems='center' gap={1}>
                                <Box
                                    width={16}
                                    height={4}
                                    bgcolor={theme.palette.secondary.main}
                                    borderRadius={1}
                                    sx={{
                                        boxShadow: `0 0 0 1px ${alpha(theme.palette.secondary.main, 0.3)}`,
                                    }}
                                />
                                <Typography
                                    variant='caption'
                                    sx={{
                                        color: theme.palette.text.primary,
                                        fontWeight: 'medium',
                                    }}
                                >
                                    Tripwire {tripwirePoints ? '✓' : '✗'}
                                </Typography>
                            </Box>
                            <Box display='flex' alignItems='center' gap={1}>
                                <Box
                                    width={16}
                                    height={4}
                                    bgcolor={theme.palette.warning.main}
                                    borderRadius={1}
                                    sx={{
                                        backgroundImage: `repeating-linear-gradient(90deg, ${theme.palette.warning.main}, ${theme.palette.warning.main} 4px, transparent 4px, transparent 8px)`,
                                        border: `1px solid ${alpha(theme.palette.warning.main, 0.5)}`,
                                    }}
                                />
                                <Typography
                                    variant='caption'
                                    sx={{
                                        color: theme.palette.text.primary,
                                        fontWeight: 'medium',
                                    }}
                                >
                                    Direction {directionPoints ? '✓' : '✗'}
                                </Typography>
                            </Box>
                        </Box>
                    </>
                )}
            </CardContent>
        </Card>
    );
};

export default InteractiveDrawingCanvas;
