/*
 * SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import LOG from '../../utils/misc/Logger';
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, Typography, Box, Chip, Alert, Button } from '@mui/material';
import { useTheme, alpha, Theme } from '@mui/material/styles';
import { Sensor } from '../../interfaces/interfaces';
import config from '../../config';
import nvAxios from '../../services/Axios';

interface Point {
    x: number;
    y: number;
}

interface TripwireCoords {
    wire: { p1: Point; p2: Point };
    direction?: { p1: Point; p2: Point };
}

interface CanvasGeometry {
    canvasWidth: number;
    canvasHeight: number;
    frameWidth: number;
    frameHeight: number;
    scaleX: number;
    scaleY: number;
}

interface VisualizationCanvasProps {
    frameWidth: number;
    frameHeight: number;
    roiImageCoords?: Point[];
    tripwireImageCoords?: TripwireCoords;
    roiId?: string;
    tripwireId?: string;
    sensor?: Sensor;
    showLiveBackground?: boolean;
}

// Pick the value matching the active theme mode
const modeValue = <T,>(theme: Theme, dark: T, light: T): T => (theme.palette.mode === 'dark' ? dark : light);

// Foreground colour readable on the active background, and its inverse (used for text outlines)
const contrastColor = (theme: Theme) => modeValue(theme, theme.palette.common.white, theme.palette.common.black);
const invertedContrastColor = (theme: Theme) => modeValue(theme, theme.palette.common.black, theme.palette.common.white);

const chipSx = (theme: Theme, color: string) => ({
    bgcolor: alpha(color, modeValue(theme, 0.3, 0.1)),
    color: contrastColor(theme),
    fontWeight: 'medium',
});

const toErrorMessage = (error: unknown): string => (error instanceof Error ? error.message : 'Unknown error');

const buildSubheader = (frameWidth: number, frameHeight: number, hasLiveSensor: boolean): string =>
    hasLiveSensor
        ? `Live camera picture with overlays - ${frameWidth}x${frameHeight}`
        : `Converted coordinates displayed on ${frameWidth}x${frameHeight} frame`;

// Utility function to detect if coordinates need transformation (negative Y values indicate image calibration)
const needsCoordinateTransformation = (coords?: Point[]): boolean => coords?.some(coord => coord.y < 0) ?? false;

const tripwireNeedsCoordinateTransformation = (coords?: TripwireCoords): boolean =>
    needsCoordinateTransformation(coords && [coords.wire.p1, coords.wire.p2]);

// Transform image calibration coordinates using the same logic as the client
const transformImageCalibratonCoords = (coords: Point[], frameHeight: number): Point[] => {
    // Based on client code analysis:
    // 1. In client: lng=x, lat=y
    // 2. Client uses addPointY(point, (height-fpImHeight)) then flipPointY
    // 3. addPointY does: lat = lat - offset
    // 4. flipPointY does: lat = height - lat
    // 5. For image calibration, negative Y values need to be made positive

    // Find the minimum Y to create an offset
    const minY = Math.min(...coords.map(c => c.y));
    const offset = minY < 0 ? Math.abs(minY) : 0;

    const transformed = coords.map(coord => ({
        x: coord.x,
        y: coord.y + offset, // Shift negative coordinates to positive
    }));

    LOG.info('[VST_CANVAS_DEBUG] Coordinate transformation:', {
        original: coords,
        frameHeight,
        minY,
        offset,
        transformed,
        'In frame bounds': transformed.every(c => c.x >= 0 && c.x <= 1920 && c.y >= 0 && c.y <= frameHeight),
    });

    return transformed;
};

// Transform tripwire coordinates for image calibration
const transformTripwireImageCalibrationCoords = (tripwireCoords: TripwireCoords, frameHeight: number): TripwireCoords => {
    // Extract all points for transformation
    const allPoints = [
        tripwireCoords.wire.p1,
        tripwireCoords.wire.p2,
        ...(tripwireCoords.direction ? [tripwireCoords.direction.p1, tripwireCoords.direction.p2] : []),
    ];

    // Transform all points using the same logic as ROI
    const transformedPoints = transformImageCalibratonCoords(allPoints, frameHeight);

    // Reconstruct the tripwire structure
    const result = {
        wire: {
            p1: transformedPoints[0],
            p2: transformedPoints[1],
        },
        direction: tripwireCoords.direction
            ? {
                  p1: transformedPoints[2],
                  p2: transformedPoints[3],
              }
            : undefined,
    };

    return result;
};

<<<<<<< ours
// Transform coordinates if they have negative Y values
const resolveROICoords = (coords: Point[] | undefined, frameHeight: number): Point[] | undefined =>
    coords && needsCoordinateTransformation(coords) ? transformImageCalibratonCoords(coords, frameHeight) : coords;

const resolveTripwireCoords = (coords: TripwireCoords | undefined, frameHeight: number): TripwireCoords | undefined =>
    coords && tripwireNeedsCoordinateTransformation(coords) ? transformTripwireImageCalibrationCoords(coords, frameHeight) : coords;
=======
type CanvasPoint = { x: number; y: number };

type TripwireCoords = {
    wire: { p1: CanvasPoint; p2: CanvasPoint };
    direction?: { p1: CanvasPoint; p2: CanvasPoint };
};

type CanvasGeometry = {
    canvasWidth: number;
    canvasHeight: number;
    frameWidth: number;
    frameHeight: number;
    scaleX: number;
    scaleY: number;
};

// Label colors that stay readable in both themes
const getLabelColors = (theme: Theme) => ({
    fill: theme.palette.mode === 'dark' ? theme.palette.common.white : theme.palette.common.black,
    stroke: theme.palette.mode === 'dark' ? theme.palette.common.black : theme.palette.common.white,
});
>>>>>>> theirs

const drawBackground = (
    ctx: CanvasRenderingContext2D,
    theme: Theme,
<<<<<<< ours
    { canvasWidth, canvasHeight }: CanvasGeometry,
    backgroundImage: HTMLImageElement | null,
    showLiveBackground: boolean
) => {
=======
    geo: CanvasGeometry,
    backgroundImage: HTMLImageElement | null,
    showLiveBackground: boolean
) => {
    const { canvasWidth, canvasHeight } = geo;

>>>>>>> theirs
    // Clear canvas
    ctx.clearRect(0, 0, canvasWidth, canvasHeight);

    // Draw background (live image or solid color)
    if (backgroundImage && showLiveBackground) {
        // Draw the live camera feed as background, scaled to fit canvas
        ctx.drawImage(backgroundImage, 0, 0, canvasWidth, canvasHeight);

        // Add a subtle overlay to improve contrast for overlays
<<<<<<< ours
        ctx.fillStyle = alpha(theme.palette.background.paper, modeValue(theme, 0.1, 0.05));
    } else {
        // Draw solid background with better theme integration
        ctx.fillStyle = modeValue(theme, theme.palette.grey[900], theme.palette.grey[50]);
    }
    ctx.fillRect(0, 0, canvasWidth, canvasHeight);
};

const drawFrameAndGrid = (ctx: CanvasRenderingContext2D, theme: Theme, geometry: CanvasGeometry) => {
    const { canvasWidth, canvasHeight, frameWidth, frameHeight, scaleX, scaleY } = geometry;

    // Draw frame border with better contrast
    ctx.strokeStyle = modeValue(theme, theme.palette.grey[600], theme.palette.grey[400]);
    ctx.lineWidth = 2;
    ctx.strokeRect(0, 0, canvasWidth, canvasHeight);

    // Draw grid for reference with better visibility
    ctx.strokeStyle = modeValue(theme, alpha(theme.palette.grey[500], 0.3), alpha(theme.palette.grey[400], 0.4));
=======
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
};

const drawGrid = (ctx: CanvasRenderingContext2D, theme: Theme, geo: CanvasGeometry) => {
    const { canvasWidth, canvasHeight, frameWidth, frameHeight, scaleX, scaleY } = geo;

    // Draw grid for reference with better visibility
    ctx.strokeStyle = theme.palette.mode === 'dark' ? alpha(theme.palette.grey[500], 0.3) : alpha(theme.palette.grey[400], 0.4);
>>>>>>> theirs
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);

    // Vertical grid lines (every 100 pixels on original frame)
    for (let x = 0; x <= frameWidth; x += 100) {
        const scaledX = x * scaleX;
        ctx.beginPath();
        ctx.moveTo(scaledX, 0);
        ctx.lineTo(scaledX, canvasHeight);
        ctx.stroke();
    }

    // Horizontal grid lines (every 100 pixels on original frame)
    for (let y = 0; y <= frameHeight; y += 100) {
        const scaledY = y * scaleY;
        ctx.beginPath();
        ctx.moveTo(0, scaledY);
        ctx.lineTo(canvasWidth, scaledY);
        ctx.stroke();
    }

    ctx.setLineDash([]); // Reset line dash
};

<<<<<<< ours
const drawROI = (ctx: CanvasRenderingContext2D, theme: Theme, { scaleX, scaleY }: CanvasGeometry, coords?: Point[]) => {
    if (!coords?.length) return;

    ctx.strokeStyle = theme.palette.primary.main;
    ctx.fillStyle = alpha(theme.palette.primary.main, modeValue(theme, 0.25, 0.15));
=======
const drawRoi = (ctx: CanvasRenderingContext2D, theme: Theme, geo: CanvasGeometry, coords: CanvasPoint[]) => {
    const { scaleX, scaleY } = geo;
    const labels = getLabelColors(theme);

    ctx.strokeStyle = theme.palette.primary.main;
    ctx.fillStyle = alpha(theme.palette.primary.main, theme.palette.mode === 'dark' ? 0.25 : 0.15);
>>>>>>> theirs
    ctx.lineWidth = 3;

    ctx.beginPath();
    const firstPoint = coords[0];
    ctx.moveTo(firstPoint.x * scaleX, firstPoint.y * scaleY);

    for (let i = 1; i < coords.length; i++) {
        const point = coords[i];
        ctx.lineTo(point.x * scaleX, point.y * scaleY);
    }

    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Draw ROI points with better visibility
    ctx.fillStyle = theme.palette.primary.main;
<<<<<<< ours
    ctx.strokeStyle = contrastColor(theme);
=======
    ctx.strokeStyle = labels.fill;
>>>>>>> theirs
    ctx.lineWidth = 2;

    coords.forEach((point, index) => {
        ctx.beginPath();
        ctx.arc(point.x * scaleX, point.y * scaleY, 6, 0, 2 * Math.PI);
        ctx.fill();
        ctx.stroke();

        // Draw point labels with better contrast
<<<<<<< ours
        ctx.fillStyle = contrastColor(theme);
        ctx.strokeStyle = invertedContrastColor(theme);
=======
        ctx.fillStyle = labels.fill;
        ctx.strokeStyle = labels.stroke;
>>>>>>> theirs
        ctx.font = 'bold 12px Arial';
        ctx.textAlign = 'center';
        ctx.lineWidth = 3;

        // Text with stroke for better visibility
        ctx.strokeText(`P${index + 1}`, point.x * scaleX, point.y * scaleY - 10);
        ctx.fillText(`P${index + 1}`, point.x * scaleX, point.y * scaleY - 10);
    });
};

<<<<<<< ours
const drawDirectionArrow = (
    ctx: CanvasRenderingContext2D,
    theme: Theme,
    { scaleX, scaleY }: CanvasGeometry,
    direction: { p1: Point; p2: Point }
) => {
=======
const drawTripwireDirection = (
    ctx: CanvasRenderingContext2D,
    theme: Theme,
    geo: CanvasGeometry,
    direction: { p1: CanvasPoint; p2: CanvasPoint }
) => {
    const { scaleX, scaleY } = geo;
    const labels = getLabelColors(theme);

>>>>>>> theirs
    ctx.strokeStyle = theme.palette.warning.main;
    ctx.fillStyle = theme.palette.warning.main;
    ctx.lineWidth = 3;
    ctx.setLineDash([8, 4]); // Better dashed line pattern

    // Draw direction line
    ctx.beginPath();
    ctx.moveTo(direction.p1.x * scaleX, direction.p1.y * scaleY);
    ctx.lineTo(direction.p2.x * scaleX, direction.p2.y * scaleY);
    ctx.stroke();
    ctx.setLineDash([]); // Reset line dash

    // Draw arrowhead at p2
    const arrowLength = 15;
    const angle = Math.atan2(direction.p2.y - direction.p1.y, direction.p2.x - direction.p1.x);
    const arrowAngle = Math.PI / 6; // 30 degrees

    ctx.beginPath();
    ctx.moveTo(direction.p2.x * scaleX, direction.p2.y * scaleY);
    ctx.lineTo(
        direction.p2.x * scaleX - arrowLength * Math.cos(angle - arrowAngle),
        direction.p2.y * scaleY - arrowLength * Math.sin(angle - arrowAngle)
    );
    ctx.moveTo(direction.p2.x * scaleX, direction.p2.y * scaleY);
    ctx.lineTo(
        direction.p2.x * scaleX - arrowLength * Math.cos(angle + arrowAngle),
        direction.p2.y * scaleY - arrowLength * Math.sin(angle + arrowAngle)
    );
    ctx.stroke();

    // Draw direction point labels with better contrast
<<<<<<< ours
    ctx.fillStyle = contrastColor(theme);
    ctx.strokeStyle = invertedContrastColor(theme);
=======
    ctx.fillStyle = labels.fill;
    ctx.strokeStyle = labels.stroke;
>>>>>>> theirs
    ctx.font = 'bold 12px Arial';
    ctx.textAlign = 'center';
    ctx.lineWidth = 3;

    // Text with stroke for better visibility
    ctx.strokeText('D1', direction.p1.x * scaleX, direction.p1.y * scaleY - 12);
    ctx.fillText('D1', direction.p1.x * scaleX, direction.p1.y * scaleY - 12);
    ctx.strokeText('D2', direction.p2.x * scaleX, direction.p2.y * scaleY - 12);
    ctx.fillText('D2', direction.p2.x * scaleX, direction.p2.y * scaleY - 12);
};

<<<<<<< ours
const drawTripwire = (ctx: CanvasRenderingContext2D, theme: Theme, geometry: CanvasGeometry, coords?: TripwireCoords) => {
    if (!coords) return;

    const { scaleX, scaleY } = geometry;
    const { wire, direction } = coords;
=======
const drawTripwire = (ctx: CanvasRenderingContext2D, theme: Theme, geo: CanvasGeometry, tripwire: TripwireCoords) => {
    const { wire, direction } = tripwire;
    const { scaleX, scaleY } = geo;
    const labels = getLabelColors(theme);
>>>>>>> theirs

    // Draw tripwire line with better visibility
    ctx.strokeStyle = theme.palette.secondary.main;
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(wire.p1.x * scaleX, wire.p1.y * scaleY);
    ctx.lineTo(wire.p2.x * scaleX, wire.p2.y * scaleY);
    ctx.stroke();

    // Draw tripwire endpoints with better contrast
    ctx.fillStyle = theme.palette.secondary.main;
<<<<<<< ours
    ctx.strokeStyle = contrastColor(theme);
=======
    ctx.strokeStyle = labels.fill;
>>>>>>> theirs
    ctx.lineWidth = 2;

    // P1
    ctx.beginPath();
    ctx.arc(wire.p1.x * scaleX, wire.p1.y * scaleY, 8, 0, 2 * Math.PI);
    ctx.fill();
    ctx.stroke();

    // P2
    ctx.beginPath();
    ctx.arc(wire.p2.x * scaleX, wire.p2.y * scaleY, 8, 0, 2 * Math.PI);
    ctx.fill();
    ctx.stroke();

    // Draw wire point labels with better contrast
<<<<<<< ours
    ctx.fillStyle = contrastColor(theme);
    ctx.strokeStyle = invertedContrastColor(theme);
=======
    ctx.fillStyle = labels.fill;
    ctx.strokeStyle = labels.stroke;
>>>>>>> theirs
    ctx.font = 'bold 14px Arial';
    ctx.textAlign = 'center';
    ctx.lineWidth = 3;

    // Text with stroke for better visibility
    ctx.strokeText('W1', wire.p1.x * scaleX, wire.p1.y * scaleY - 12);
    ctx.fillText('W1', wire.p1.x * scaleX, wire.p1.y * scaleY - 12);
    ctx.strokeText('W2', wire.p2.x * scaleX, wire.p2.y * scaleY - 12);
    ctx.fillText('W2', wire.p2.x * scaleX, wire.p2.y * scaleY - 12);

    // Draw direction arrow if provided
    if (direction) {
<<<<<<< ours
        drawDirectionArrow(ctx, theme, geometry, direction);
    }
};

const drawInfoBox = (ctx: CanvasRenderingContext2D, theme: Theme, geometry: CanvasGeometry) => {
    const { canvasWidth, canvasHeight, frameWidth, frameHeight, scaleX, scaleY } = geometry;
=======
        drawTripwireDirection(ctx, theme, geo, direction);
    }
};

const drawInfoBox = (ctx: CanvasRenderingContext2D, theme: Theme, geo: CanvasGeometry) => {
    const { canvasWidth, canvasHeight, frameWidth, frameHeight, scaleX, scaleY } = geo;
>>>>>>> theirs

    // Draw coordinate info with better theme integration
    const infoBoxHeight = 55;
    const infoBoxWidth = 200;

    // Draw semi-transparent background for info text (more transparent for better visibility)
<<<<<<< ours
    ctx.fillStyle = alpha(theme.palette.background.paper, modeValue(theme, 0.4, 0.3));
=======
    ctx.fillStyle = alpha(theme.palette.background.paper, theme.palette.mode === 'dark' ? 0.4 : 0.3);
>>>>>>> theirs
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
};

<<<<<<< ours
interface CanvasHeaderActionProps {
    sensor?: Sensor;
    showLiveBackground: boolean;
    isLoadingImage: boolean;
    roiId?: string;
    tripwireId?: string;
    onRefresh: () => void;
}

const CanvasHeaderAction: React.FC<CanvasHeaderActionProps> = ({
    sensor,
    showLiveBackground,
    isLoadingImage,
    roiId,
    tripwireId,
    onRefresh,
}) => {
    const theme = useTheme();

    return (
        <Box display='flex' gap={1} alignItems='center' justifyContent='space-between' width='100%'>
            <Box display='flex' gap={1} alignItems='center'>
                {showLiveBackground && sensor && (
                    <>
                        <Chip
                            label={`Camera: ${sensor.name || sensor.sensorId}`}
                            color='info'
                            size='small'
                            sx={chipSx(theme, theme.palette.info.main)}
                        />
                        {isLoadingImage && (
                            <Chip label='Loading...' color='warning' size='small' sx={chipSx(theme, theme.palette.warning.main)} />
                        )}
                    </>
                )}
                {roiId && <Chip label={`ROI: ${roiId}`} color='primary' size='small' sx={chipSx(theme, theme.palette.primary.main)} />}
                {tripwireId && (
                    <Chip
                        label={`Tripwire: ${tripwireId}`}
                        color='secondary'
                        size='small'
                        sx={chipSx(theme, theme.palette.secondary.main)}
                    />
                )}
            </Box>
            {showLiveBackground && sensor && (
                <Button
                    size='small'
                    onClick={onRefresh}
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
                    Refresh
                </Button>
            )}
        </Box>
    );
};

const CanvasLegend: React.FC<{ roiCoords?: Point[]; tripwireCoords?: TripwireCoords }> = ({ roiCoords, tripwireCoords }) => {
    const theme = useTheme();
    const labelSx = {
        color: theme.palette.text.primary,
        fontWeight: 'medium',
    };

    return (
        <Box
            mt={2}
            display='flex'
            justifyContent='center'
            gap={3}
            sx={{
                p: 2,
                bgcolor: alpha(theme.palette.background.default, 0.5),
                borderRadius: 2,
                border: `1px solid ${theme.palette.divider}`,
            }}
        >
            {roiCoords && (
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
                    <Typography variant='caption' sx={labelSx}>
                        ROI Polygon ({roiCoords.length} points)
                    </Typography>
                </Box>
            )}
            {tripwireCoords && (
                <>
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
                        <Typography variant='caption' sx={labelSx}>
                            Tripwire Line (W1-W2)
                        </Typography>
                    </Box>
                    {tripwireCoords.direction && (
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
                            <Typography variant='caption' sx={labelSx}>
                                Detection Direction (D1→D2)
                            </Typography>
                        </Box>
                    )}
                </>
            )}
        </Box>
    );
};

=======
>>>>>>> theirs
const VisualizationCanvas: React.FC<VisualizationCanvasProps> = ({
    frameWidth,
    frameHeight,
    roiImageCoords,
    tripwireImageCoords,
    roiId,
    tripwireId,
    sensor,
    showLiveBackground = false,
}) => {
    LOG.info('[VST_CANVAS_DEBUG] Component called with props:', {
        frameWidth,
        frameHeight,
        roiImageCoords,
        tripwireImageCoords,
        roiId,
        tripwireId,
        showLiveBackground,
    });

    const theme = useTheme();
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const [backgroundImage, setBackgroundImage] = useState<HTMLImageElement | null>(null);
    const [imageError, setImageError] = useState<string | null>(null);
    const [isLoadingImage, setIsLoadingImage] = useState(false);

    // Debug: Log incoming coordinates
    LOG.info('[VST_CANVAS_DEBUG] Initial coordinates:', {
        frameWidth,
        frameHeight,
        roiImageCoords,
        tripwireImageCoords,
        roiNeedsTransform: needsCoordinateTransformation(roiImageCoords),
        tripwireNeedsTransform: tripwireNeedsCoordinateTransformation(tripwireImageCoords),
    });

    const transformedROICoords = resolveROICoords(roiImageCoords, frameHeight);
    const transformedTripwireCoords = resolveTripwireCoords(tripwireImageCoords, frameHeight);
    const hasCoordsToRender = Boolean(transformedROICoords || transformedTripwireCoords);

    LOG.info('[VST_CANVAS_DEBUG] Final coordinates for rendering:', {
        transformedROICoords,
        transformedTripwireCoords,
        willRender: hasCoordsToRender,
    });

    // Calculate responsive canvas size while maintaining aspect ratio
    const maxCanvasWidth = 800;
    const aspectRatio = frameWidth / frameHeight;
    const canvasWidth = Math.min(maxCanvasWidth, frameWidth);
    const canvasHeight = canvasWidth / aspectRatio;

    // Scale factors for drawing coordinates
    const scaleX = canvasWidth / frameWidth;
    const scaleY = canvasHeight / frameHeight;

    // Fetch live picture from sensor
    const fetchLivePicture = useCallback(async () => {
        if (!sensor || !showLiveBackground) return;

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
            LOG.error('Error fetching live picture:', error);
            setImageError(toErrorMessage(error));
            setIsLoadingImage(false);
        }
    }, [sensor, showLiveBackground]);

    // Fetch image once when component mounts or sensor changes
    useEffect(() => {
        if (showLiveBackground && sensor) {
            fetchLivePicture();
        }
    }, [fetchLivePicture, showLiveBackground, sensor]);

    useEffect(() => {
        const ctx = canvasRef.current?.getContext('2d');
        if (!ctx) return;

<<<<<<< ours
        const geometry: CanvasGeometry = { canvasWidth, canvasHeight, frameWidth, frameHeight, scaleX, scaleY };

        drawBackground(ctx, theme, geometry, backgroundImage, showLiveBackground);
        drawFrameAndGrid(ctx, theme, geometry);
        drawROI(ctx, theme, geometry, transformedROICoords);
        drawTripwire(ctx, theme, geometry, transformedTripwireCoords);
        drawInfoBox(ctx, theme, geometry);
=======
        const geo: CanvasGeometry = { canvasWidth, canvasHeight, frameWidth, frameHeight, scaleX, scaleY };

        drawBackground(ctx, theme, geo, backgroundImage, showLiveBackground);
        drawGrid(ctx, theme, geo);

        // Draw ROI if provided
        if (transformedROICoords && transformedROICoords.length > 0) {
            drawRoi(ctx, theme, geo, transformedROICoords);
        }

        // Draw Tripwire if provided
        if (transformedTripwireCoords) {
            drawTripwire(ctx, theme, geo, transformedTripwireCoords);
        }

        drawInfoBox(ctx, theme, geo);
>>>>>>> theirs
    }, [
        canvasWidth,
        canvasHeight,
        frameWidth,
        frameHeight,
        scaleX,
        scaleY,
        transformedROICoords,
        transformedTripwireCoords,
        backgroundImage,
        showLiveBackground,
        theme,
    ]);

    LOG.info('[VST_CANVAS_DEBUG] Checking render condition:', {
        hasTransformedROI: !!transformedROICoords,
        hasTransformedTripwire: !!transformedTripwireCoords,
        willReturn: !hasCoordsToRender,
    });

    if (!hasCoordsToRender) {
        LOG.info('[VST_CANVAS_DEBUG] Returning null - no coordinates to render');
        return null;
    }

    LOG.info('[VST_CANVAS_DEBUG] About to render component');

    const checkerTint = modeValue(theme, 'rgba(255,255,255,0.02)', 'rgba(0,0,0,0.02)');

    return (
        <Card
            sx={{
                bgcolor: theme.palette.background.paper,
                borderColor: theme.palette.divider,
            }}
        >
            <CardHeader
                title='Coordinate Visualization'
                subheader={buildSubheader(frameWidth, frameHeight, showLiveBackground && Boolean(sensor))}
                sx={{
                    bgcolor: alpha(theme.palette.primary.main, modeValue(theme, 0.1, 0.05)),
                    borderBottom: `1px solid ${theme.palette.divider}`,
                }}
                action={
                    <CanvasHeaderAction
                        sensor={sensor}
                        showLiveBackground={showLiveBackground}
                        isLoadingImage={isLoadingImage}
                        roiId={roiId}
                        tripwireId={tripwireId}
                        onRefresh={fetchLivePicture}
                    />
                }
            />
            <CardContent>
                {imageError && (
                    <Alert
                        severity='error'
                        sx={{
                            mb: 2,
                            bgcolor: alpha(theme.palette.error.main, modeValue(theme, 0.15, 0.1)),
                            color: theme.palette.error.main,
                            '& .MuiAlert-icon': {
                                color: theme.palette.error.main,
                            },
                        }}
                    >
                        Failed to load camera feed: {imageError}
                    </Alert>
                )}
                <Box
                    display='flex'
                    justifyContent='center'
                    sx={{
                        border: `2px solid ${theme.palette.divider}`,
                        borderRadius: 2,
                        p: 2,
                        bgcolor: alpha(modeValue(theme, theme.palette.grey[900], theme.palette.grey[100]), 0.5),
                        backgroundImage: `linear-gradient(45deg, transparent 25%, ${checkerTint} 25%, ${checkerTint} 50%, transparent 50%, transparent 75%, ${checkerTint} 75%)`,
                        backgroundSize: '20px 20px',
                    }}
                >
                    <canvas
                        ref={canvasRef}
                        width={canvasWidth}
                        height={canvasHeight}
                        style={{
                            maxWidth: '100%',
                            height: 'auto',
                            border: `2px solid ${theme.palette.divider}`,
                            borderRadius: '8px',
                            boxShadow: theme.shadows[4],
                            backgroundColor: theme.palette.background.paper,
                        }}
                    />
                </Box>

                {/* Legend with better theme integration */}
                <CanvasLegend roiCoords={transformedROICoords} tripwireCoords={transformedTripwireCoords} />
            </CardContent>
        </Card>
    );
};

export default VisualizationCanvas;
