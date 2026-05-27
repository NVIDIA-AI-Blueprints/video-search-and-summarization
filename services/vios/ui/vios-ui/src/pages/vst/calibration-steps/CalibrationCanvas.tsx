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
import { Stage, Layer, Image, Line, Circle, Text } from 'react-konva';
import { KonvaEventObject } from 'konva/lib/Node';
import Konva from 'konva';
import { Box, useTheme } from '@mui/material';
import { Sensor } from './types';
import { CalibrationFigure, CalibrationLabel } from './Calibration';
import config from '../../../config';

// Exact same maxZoom as React UI CalcBoundsHOC
const maxZoom = 7;
const minZoom = 0.1;

interface CalibrationCanvasProps {
    sensor: Sensor;
    figures: CalibrationFigure[];
    unfinishedFigure: CalibrationFigure | null;
    labels: CalibrationLabel[];
    onChange: (eventType: string, figureData: CalibrationFigure) => void;
    readOnly?: boolean;
    showVertexNumbers?: boolean;
    lineWidth?: number;
    dotWidth?: number;
    opacity?: number;
}

interface Point {
    lat: number;
    lng: number;
}

const DEFAULT_COLORS = {
    blue: '#1976d2', // theme.palette.primary.main
    red: '#d32f2f', // theme.palette.error.main
    green: '#2e7d32', // theme.palette.success.main
    yellow: '#ed6c02', // theme.palette.warning.main
    purple: '#9c27b0', // theme.palette.secondary.main
    orange: '#ff9800', // theme.palette.info.main
    // High contrast colors for better visibility
    cyan: '#00ffff',
    magenta: '#ff00ff',
    lime: '#00ff00',
    bright_yellow: '#ffff00',
};

// Coordinate transformation functions - CORRECTED after user feedback about Y-axis flip
// Original Leaflet system has Y-axis flipped: top of image = higher lat values
const konvaToCalibration = (x: number, y: number, imageHeight: number): Point => ({
    lng: x, // lng = X coordinate (0 to width, left to right)
    lat: imageHeight - y, // lat = flipped Y coordinate (top of image = higher values)
});

const calibrationToKonva = (lat: number, lng: number, imageHeight: number) => ({
    x: lng, // X = lng coordinate
    y: imageHeight - lat, // Y = flipped lat coordinate back to standard image coords
});

const convertPoint = (x: number, y: number, imageHeight: number): Point => konvaToCalibration(x, y, imageHeight);

const checkIfDrawing = (unfinishedFigure: CalibrationFigure | null, figures: CalibrationFigure[], labels: CalibrationLabel[]): boolean => {
    if (!unfinishedFigure) {
        return false;
    }

    const drawId = labels.find(label => label.id === unfinishedFigure.id);
    if (!drawId) {
        return false;
    }

    if (!drawId.draw) {
        return false;
    }

    const limited = labels.filter(label => label.limit === true);
    const calib = figures.filter(item => item.class === unfinishedFigure.id);

    if (limited.some(label => label.id === drawId.id) && calib.length > 0) {
        return false;
    }

    return true;
};

interface FigureRendererProps {
    figure: CalibrationFigure;
    isSelected: boolean;
    isUnfinished?: boolean;
    cursorPos?: Point | null;
    onSelect?: () => void;
    color: string;
    showVertexNumbers?: boolean;
    imageHeight: number;
    lineWidth?: number;
    dotWidth?: number;
    opacity?: number;
}

const FigureRenderer: React.FC<FigureRendererProps> = ({
    figure,
    isSelected,
    isUnfinished = false,
    cursorPos,
    onSelect,
    color,
    showVertexNumbers = false,
    imageHeight,
    lineWidth = 8,
    dotWidth = 8,
    opacity = 0.4,
}) => {
    // Convert calibration points to konva coordinates
    const konvaPoints = figure.points
        .map(p => {
            const konvaCoords = calibrationToKonva(p.lat, p.lng, imageHeight);
            return [konvaCoords.x, konvaCoords.y];
        })
        .flat();

    // Add cursor position if unfinished
    const displayPoints =
        isUnfinished && cursorPos
            ? [...konvaPoints, ...Object.values(calibrationToKonva(cursorPos.lat, cursorPos.lng, imageHeight))]
            : konvaPoints;

    const handleShapeClick = () => {
        if (onSelect && !isUnfinished) {
            onSelect();
        }
    };

    return (
        <>
            {/* Render the line/polygon */}
            {displayPoints.length > 3 && (
                <Line
                    points={displayPoints}
                    stroke={color}
                    strokeWidth={isSelected ? lineWidth + 1 : lineWidth}
                    opacity={isUnfinished ? opacity * 0.7 : opacity}
                    fill={figure.type === 'polygon' ? color : undefined}
                    fillOpacity={figure.type === 'polygon' ? opacity * 0.3 : 0}
                    closed={figure.type === 'polygon'}
                    dash={isUnfinished ? [5, 10] : undefined}
                    onClick={handleShapeClick}
                    onTap={handleShapeClick}
                />
            )}

            {/* Render vertex markers */}
            {figure.points.map((point, index) => {
                const konvaCoords = calibrationToKonva(point.lat, point.lng, imageHeight);
                return (
                    <React.Fragment key={`${figure.id}-marker-${index}`}>
                        <Circle
                            x={konvaCoords.x}
                            y={konvaCoords.y}
                            radius={isSelected ? dotWidth + 1 : dotWidth}
                            stroke={color}
                            strokeWidth={isSelected ? lineWidth + 1 : lineWidth}
                            fill={isSelected ? '#ffffff' : color}
                            opacity={opacity}
                        />
                        {showVertexNumbers && (
                            <Text
                                x={konvaCoords.x - 18}
                                y={konvaCoords.y - 45}
                                text={index.toString()}
                                fontSize={36}
                                fill='white'
                                fontStyle='bold'
                                padding={6}
                                align='center'
                                stroke='black'
                                strokeWidth={2}
                            />
                        )}
                    </React.Fragment>
                );
            })}
        </>
    );
};

const CalibrationCanvas: React.FC<CalibrationCanvasProps> = ({
    sensor,
    figures,
    unfinishedFigure,
    labels,
    onChange,
    readOnly = false,
    showVertexNumbers = false,
    lineWidth = 2,
    dotWidth = 4,
    opacity = 0.8,
}) => {
    const [selectedFigureId, setSelectedFigureId] = useState<string | null>(null);
    const [cursorPos, setCursorPos] = useState<Point | null>(null);
    const stageRef = useRef<Konva.Stage>(null);
    const [isImageLoaded, setIsImageLoaded] = useState(false);
    const [lastClickTime, setLastClickTime] = useState<number>(0);
    const [isDoubleClick, setIsDoubleClick] = useState<boolean>(false);

    // State for actual image dimensions (matching React UI's withLoadImageData pattern)
    const [actualImageHeight, setActualImageHeight] = useState<number | null>(null);
    const [actualImageWidth, setActualImageWidth] = useState<number | null>(null);
    const [imageElement, setImageElement] = useState<HTMLImageElement | null>(null);
    const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
    const containerRef = useRef<HTMLDivElement>(null);

    const theme = useTheme();

    const getImageUrl = (): string => {
        if (!sensor.imageUrl) {
            return '';
        }
        const cleanImageUrl = sensor.imageUrl.startsWith('/') ? sensor.imageUrl.substring(1) : sensor.imageUrl;
        return `${config.analyticsUIServerEndpoint}/${cleanImageUrl}`;
    };

    // Load actual image dimensions (matching React UI's withLoadImageData)
    useEffect(() => {
        const imageUrl = getImageUrl();
        if (!imageUrl) return;

        const img = new window.Image();
        img.onload = () => {
            // Use actual image dimensions like React UI
            setActualImageHeight(img.height);
            setActualImageWidth(img.width);
            setImageElement(img);
            setIsImageLoaded(true);
        };
        img.src = imageUrl;

        return () => {
            img.onload = null;
        };
    }, [sensor.imageUrl]);

    // Get pointer position relative to the stage content (accounting for zoom/pan)
    const getRelativePointerPosition = (stage: Konva.Stage) => {
        const pointerPosition = stage.getPointerPosition();
        if (!pointerPosition) return null;

        const transform = stage.getAbsoluteTransform().copy();
        transform.invert();

        return transform.point(pointerPosition);
    };

    // Event handlers
    const handleStageClick = (e: KonvaEventObject<MouseEvent>) => {
        if (readOnly) return;

        // Skip processing if we're in a double-click sequence
        if (isDoubleClick) return;

        const now = Date.now();
        const timeDiff = now - lastClickTime;

        // If this is a quick second click, mark as potential double-click and ignore
        if (timeDiff < 800) {
            // More lenient timing
            setIsDoubleClick(true);
            // Reset flag after a timeout in case double-click doesn't fire
            setTimeout(() => setIsDoubleClick(false), 1000);
            return;
        }

        setLastClickTime(now);

        const stage = e.target.getStage();
        if (!stage || !actualImageHeight) return;

        const relativePos = getRelativePointerPosition(stage);
        if (!relativePos) return;

        const drawing = checkIfDrawing(unfinishedFigure, figures, labels);

        if (drawing && unfinishedFigure) {
            const newPoint = convertPoint(relativePos.x, relativePos.y, actualImageHeight!);
            const newPoints = [...unfinishedFigure.points, newPoint];

            // Auto-complete tripwire lines and direction lines after 2 points
            if ((unfinishedFigure.class === 'tripwire' || unfinishedFigure.class === 'tripDirection') && newPoints.length === 2) {
                onChange('new', {
                    ...unfinishedFigure,
                    points: newPoints,
                });
                return;
            }

            onChange('unfinished', {
                ...unfinishedFigure,
                points: newPoints,
            });
            return;
        }

        if (!drawing) {
            setSelectedFigureId(null);
        }
    };

    const handleStageDoubleClick = (e: KonvaEventObject<MouseEvent>) => {
        if (readOnly) return;

        // Reset double-click flag
        setIsDoubleClick(false);

        const drawing = checkIfDrawing(unfinishedFigure, figures, labels);

        if (drawing && unfinishedFigure && actualImageHeight) {
            const stage = e.target.getStage();
            if (!stage) return;
            const relativePos = getRelativePointerPosition(stage);
            if (!relativePos) return;
            const newPoint = convertPoint(relativePos.x, relativePos.y, actualImageHeight);
            const newPoints = [...unfinishedFigure.points, newPoint];

            // For polygon, need at least 3 points
            if (unfinishedFigure.type === 'polygon' && newPoints.length >= 3) {
                onChange('new', { ...unfinishedFigure, points: newPoints });
            } else if (unfinishedFigure.type === 'polyline' && newPoints.length >= 2) {
                onChange('new', { ...unfinishedFigure, points: newPoints });
            }
        }

        e.evt.preventDefault();
        e.evt.stopPropagation();
    };

    const handleStageRightClick = (e: KonvaEventObject<MouseEvent>) => {
        if (readOnly) return;

        const drawing = checkIfDrawing(unfinishedFigure, figures, labels);

        if (drawing && unfinishedFigure) {
            // Reset the unfinished figure
            onChange('unfinished', {
                ...unfinishedFigure,
                points: [],
            });
        }
        e.evt.preventDefault();
    };

    const handleStageMouseMove = (e: KonvaEventObject<MouseEvent>) => {
        if (!readOnly) {
            const stage = e.target.getStage();
            if (!stage) return;

            const relativePos = getRelativePointerPosition(stage);
            if (!relativePos) return;

            const calibrationPoint = convertPoint(relativePos.x, relativePos.y, actualImageHeight!);
            setCursorPos(calibrationPoint);
        }
    };

    const handleKeyDown = useCallback(
        (e: KeyboardEvent) => {
            const tagName = document.activeElement?.tagName.toLowerCase();
            if (tagName === 'input' || tagName === 'textarea') {
                return;
            }

            const drawing = checkIfDrawing(unfinishedFigure, figures, labels);

            if (drawing && unfinishedFigure) {
                if (e.key === 'f' || e.key === 'F') {
                    const { type, points } = unfinishedFigure;
                    if (type === 'polygon' && points.length >= 3) {
                        onChange('new', unfinishedFigure);
                    } else if (type === 'polyline' && points.length >= 2) {
                        onChange('new', unfinishedFigure);
                    }
                }
            } else {
                if ((e.key === 'Backspace' || e.key === 'Delete') && selectedFigureId) {
                    const selectedFigure = figures.find(f => f.id === selectedFigureId);
                    if (selectedFigure) {
                        onChange('delete', selectedFigure);
                        setSelectedFigureId(null);
                    }
                }
            }
        },
        [unfinishedFigure, figures, labels, selectedFigureId, onChange]
    );

    useEffect(() => {
        document.addEventListener('keydown', handleKeyDown);
        return () => {
            document.removeEventListener('keydown', handleKeyDown);
        };
    }, [handleKeyDown]);

    // Function to update container size
    const updateContainerSize = useCallback(() => {
        const container = containerRef.current;
        if (container) {
            // Use getBoundingClientRect for more accurate sizing
            const rect = container.getBoundingClientRect();
            const newSize = {
                width: rect.width || container.offsetWidth,
                height: rect.height || container.offsetHeight,
            };
            // Only update if dimensions actually changed and are valid
            if (newSize.width > 0 && newSize.height > 0) {
                setContainerSize(prevSize => {
                    // Update if size changed by more than 1px to avoid micro-adjustments
                    const widthChanged = Math.abs(prevSize.width - newSize.width) > 1;
                    const heightChanged = Math.abs(prevSize.height - newSize.height) > 1;
                    return widthChanged || heightChanged ? newSize : prevSize;
                });
            }
        }
    }, []);

    // Update container size on mount and when window resizes
    useEffect(() => {
        // Multiple attempts to get container size as DOM renders
        const attemptUpdate = () => updateContainerSize();

        // Immediate attempt
        attemptUpdate();

        // Short delay attempt
        const shortTimeout = setTimeout(attemptUpdate, 10);

        // Longer delay attempt for complex layouts
        const longTimeout = setTimeout(attemptUpdate, 100);

        // Even longer delay for slow-loading images
        const veryLongTimeout = setTimeout(attemptUpdate, 500);

        window.addEventListener('resize', updateContainerSize);

        // Use ResizeObserver for better container size detection
        let resizeObserver: ResizeObserver | null = null;
        if (containerRef.current && window.ResizeObserver) {
            resizeObserver = new ResizeObserver(entries => {
                for (const entry of entries) {
                    const { width, height } = entry.contentRect;
                    if (width > 0 && height > 0) {
                        setContainerSize(prevSize => {
                            const widthChanged = Math.abs(prevSize.width - width) > 1;
                            const heightChanged = Math.abs(prevSize.height - height) > 1;
                            return widthChanged || heightChanged ? { width, height } : prevSize;
                        });
                    }
                }
            });
            resizeObserver.observe(containerRef.current);
        }

        return () => {
            clearTimeout(shortTimeout);
            clearTimeout(longTimeout);
            clearTimeout(veryLongTimeout);
            window.removeEventListener('resize', updateContainerSize);
            if (resizeObserver) {
                resizeObserver.disconnect();
            }
        };
    }, [updateContainerSize]);

    // Additional container size detection when image loads
    useEffect(() => {
        if (isImageLoaded) {
            // Re-detect container size when image loads to handle uncached images
            // Multiple attempts to ensure we get the correct size
            const timeoutId1 = setTimeout(() => {
                updateContainerSize();
            }, 50);

            const timeoutId2 = setTimeout(() => {
                updateContainerSize();
            }, 150);

            return () => {
                clearTimeout(timeoutId1);
                clearTimeout(timeoutId2);
            };
        }
    }, [isImageLoaded, updateContainerSize]);

    // Fit stage to image when loaded - better container utilization
    useEffect(() => {
        if (isImageLoaded && stageRef.current && actualImageWidth && actualImageHeight) {
            const stage = stageRef.current;
            const { width: containerWidth, height: containerHeight } = containerSize;

            // Ensure container has valid dimensions before fitting
            if (containerWidth <= 0 || containerHeight <= 0) {
                console.warn('Container dimensions not ready for image fitting, skipping...');
                return;
            }

            // Calculate scale to fit image within container with minimal padding
            const scaleX = (containerWidth - 20) / actualImageWidth; // 10px padding on each side
            const scaleY = (containerHeight - 20) / actualImageHeight; // 10px padding on each side
            const scale = Math.min(scaleX, scaleY); // Allow scaling up for better utilization

            // Ensure scale is valid
            if (scale <= 0) {
                console.warn('Invalid scale calculated, skipping image fitting...');
                return;
            }

            // Center the image
            const x = (containerWidth - actualImageWidth * scale) / 2;
            const y = (containerHeight - actualImageHeight * scale) / 2;

            stage.scale({ x: scale, y: scale });
            stage.position({ x, y });

            // Force stage update
            stage.batchDraw();
        }
    }, [isImageLoaded, actualImageWidth, actualImageHeight, containerSize]);

    // Handle zoom with mouse wheel - improved centering
    const handleWheel = (e: KonvaEventObject<WheelEvent>) => {
        e.evt.preventDefault();

        const stage = stageRef.current;
        if (!stage) return;

        const oldScale = stage.scaleX();
        const pointer = stage.getPointerPosition();
        if (!pointer) return;

        // Calculate the point relative to the stage
        const mousePointTo = {
            x: (pointer.x - stage.x()) / oldScale,
            y: (pointer.y - stage.y()) / oldScale,
        };

        const direction = e.evt.deltaY > 0 ? -1 : 1;
        const factor = 1.1;
        const newScale = Math.max(minZoom, Math.min(maxZoom, direction > 0 ? oldScale * factor : oldScale / factor));

        stage.scale({ x: newScale, y: newScale });

        // Calculate new position to keep the mouse point fixed
        const newPos = {
            x: pointer.x - mousePointTo.x * newScale,
            y: pointer.y - mousePointTo.y * newScale,
        };

        // Apply lenient drag boundaries after zoom
        const constrainedPos = dragBoundFunc(newPos);
        stage.position(constrainedPos);
    };

    // More lenient drag boundaries - allow full container utilization
    const dragBoundFunc = useCallback(
        (pos: { x: number; y: number }) => {
            if (!actualImageWidth || !actualImageHeight || !stageRef.current) return pos;

            const stage = stageRef.current;
            const scale = stage.scaleX();
            const scaledImageWidth = actualImageWidth * scale;
            const scaledImageHeight = actualImageHeight * scale;

            // Allow dragging within reasonable bounds - much more lenient
            // Allow the image to be positioned anywhere within the container plus some overflow
            const padding = 100; // Allow some overflow for better UX

            const minX = -scaledImageWidth + padding;
            const maxX = containerSize.width - padding;
            const minY = -scaledImageHeight + padding;
            const maxY = containerSize.height - padding;

            return {
                x: Math.max(minX, Math.min(maxX, pos.x)),
                y: Math.max(minY, Math.min(maxY, pos.y)),
            };
        },
        [actualImageWidth, actualImageHeight, containerSize.width, containerSize.height]
    );

    const drawing = checkIfDrawing(unfinishedFigure, figures, labels);
    const imageUrl = getImageUrl();

    // Wait for image dimensions to load (matching React UI's withLoadImageData pattern)
    if (!actualImageHeight || !actualImageWidth || !isImageLoaded) {
        return (
            <Box
                sx={{
                    height: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: '#f5f5f5',
                    border: '2px dashed #ccc',
                }}
            >
                Loading image dimensions...
            </Box>
        );
    }

    if (!imageUrl) {
        return (
            <Box
                sx={{
                    height: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: '#f5f5f5',
                    border: '2px dashed #ccc',
                }}
            >
                No image available for this sensor
            </Box>
        );
    }

    const getCursor = () => {
        if (drawing) {
            return 'crosshair';
        }
        return 'grab';
    };

    return (
        <Box
            ref={containerRef}
            sx={{
                height: '100%',
                position: 'relative',
                cursor: getCursor(),
                backgroundColor: theme.palette.background.default,
            }}
        >
            {/* Helper text for drawing */}
            {drawing && unfinishedFigure && unfinishedFigure.points.length >= 2 && (
                <Box
                    sx={{
                        position: 'absolute',
                        top: 16,
                        left: 16,
                        backgroundColor: 'rgba(0, 0, 0, 0.7)',
                        color: 'white',
                        padding: '8px 12px',
                        borderRadius: '4px',
                        fontSize: '14px',
                        zIndex: 1000,
                    }}
                >
                    Press <strong>F</strong> key or double-click to finish polygon
                </Box>
            )}
            {containerSize.width > 0 && containerSize.height > 0 ? (
                <Stage
                    ref={stageRef}
                    width={containerSize.width}
                    height={containerSize.height}
                    draggable={!drawing}
                    dragBoundFunc={dragBoundFunc}
                    onClick={handleStageClick}
                    onDblClick={handleStageDoubleClick}
                    onContextMenu={handleStageRightClick}
                    onMouseMove={handleStageMouseMove}
                    onWheel={handleWheel}
                >
                    <Layer>
                        {/* Background Image */}
                        {imageElement && <Image image={imageElement} width={actualImageWidth} height={actualImageHeight} />}

                        {/* Render finished figures */}
                        {figures.map(figure => (
                            <FigureRenderer
                                key={figure.id}
                                figure={figure}
                                isSelected={selectedFigureId === figure.id}
                                onSelect={() => setSelectedFigureId(figure.id)}
                                color={DEFAULT_COLORS[figure.color as keyof typeof DEFAULT_COLORS] || figure.color}
                                showVertexNumbers={showVertexNumbers}
                                imageHeight={actualImageHeight!}
                                lineWidth={lineWidth}
                                dotWidth={dotWidth}
                                opacity={opacity}
                            />
                        ))}

                        {/* Render unfinished figure */}
                        {drawing && unfinishedFigure && (
                            <FigureRenderer
                                figure={unfinishedFigure}
                                isSelected={false}
                                isUnfinished={true}
                                cursorPos={cursorPos}
                                color={DEFAULT_COLORS[unfinishedFigure.color as keyof typeof DEFAULT_COLORS] || unfinishedFigure.color}
                                showVertexNumbers={showVertexNumbers}
                                imageHeight={actualImageHeight!}
                                lineWidth={lineWidth}
                                dotWidth={dotWidth}
                                opacity={opacity}
                            />
                        )}
                    </Layer>
                </Stage>
            ) : (
                <Box
                    sx={{
                        height: '100%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        backgroundColor: '#f5f5f5',
                        border: '2px dashed #ccc',
                    }}
                >
                    Preparing canvas...
                </Box>
            )}
        </Box>
    );
};

export default CalibrationCanvas;
