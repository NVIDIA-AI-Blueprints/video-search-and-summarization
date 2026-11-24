/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

/**
 * Annotation Canvas Component
 * Based on CalibrationCanvas.tsx - handles ROI and tripwire drawing
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { Stage, Layer, Image, Line, Circle, Text } from 'react-konva';
import { KonvaEventObject } from 'konva/lib/Node';
import Konva from 'konva';
import { Box, useTheme, Button } from '@mui/material';
import type { ROI, Tripwire, Point2D } from '../types';

const maxZoom = 7;
const minZoom = 0.1;

interface AnnotationCanvasProps {
  imageUrl: string;
  rois: ROI[];
  tripwires: Tripwire[];
  drawingMode: 'none' | 'roi' | 'tripwire' | 'tripwire-direction' | 'measure';
  showAnnotations: boolean;
  /** When true, ignore draw/finish clicks (view-only). Pan/zoom still work. */
  readOnly?: boolean;
  /** Points for floor-plan scale measurement (image pixel coords). */
  measurePoints?: Point2D[];
  onAddROIPoint: (x: number, y: number) => void;
  onFinishROI: () => void;
  onAddTripwirePoint: (x: number, y: number) => void;
  onAddMeasurePoint?: (x: number, y: number) => void;
  onROIClick?: (roi: ROI) => void;
  onTripwireClick?: (tripwire: Tripwire) => void;
}

interface FitImageOptions {
  stage: Konva.Stage;
  imageWidth: number;
  imageHeight: number;
  containerWidth: number;
  containerHeight: number;
}

const AnnotationCanvas: React.FC<AnnotationCanvasProps> = ({
  imageUrl,
  rois,
  tripwires,
  drawingMode,
  showAnnotations,
  readOnly = false,
  measurePoints = [],
  onAddROIPoint,
  onFinishROI,
  onAddTripwirePoint,
  onAddMeasurePoint,
  onROIClick,
  onTripwireClick,
}) => {
  const effectiveDrawingMode = readOnly ? 'none' : drawingMode;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [cursorPos, setCursorPos] = useState<Point2D | null>(null);
  const stageRef = useRef<Konva.Stage>(null);
  const [isImageLoaded, setIsImageLoaded] = useState(false);
  const [lastClickTime, setLastClickTime] = useState<number>(0);
  const [isDoubleClick, setIsDoubleClick] = useState<boolean>(false);
  const stageDragMovedRef = useRef(false);
  
  const [actualImageHeight, setActualImageHeight] = useState<number | null>(null);
  const [actualImageWidth, setActualImageWidth] = useState<number | null>(null);
  const [imageElement, setImageElement] = useState<HTMLImageElement | null>(null);
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

  const theme = useTheme();

  // Function to fit image to container
  const fitImageToContainer = useCallback((options: FitImageOptions) => {
    const { stage, imageWidth, imageHeight, containerWidth, containerHeight } = options;

    if (containerWidth <= 0 || containerHeight <= 0) return;

    // Calculate scale to fit image within container
    const scaleX = (containerWidth - 40) / imageWidth;
    const scaleY = (containerHeight - 40) / imageHeight;
    const scale = Math.min(scaleX, scaleY);

    if (scale <= 0) return;

    // Center the image
    const scaledWidth = imageWidth * scale;
    const scaledHeight = imageHeight * scale;
    const x = (containerWidth - scaledWidth) / 2;
    const y = (containerHeight - scaledHeight) / 2;

    stage.scale({ x: scale, y: scale });
    stage.position({ x, y });
    stage.batchDraw();
  }, []);

  // Load image
  useEffect(() => {
    if (!imageUrl) return;

    const img = new window.Image();
    img.onload = () => {
      setActualImageHeight(img.height);
      setActualImageWidth(img.width);
      setImageElement(img);
      setIsImageLoaded(true);
    };
    img.crossOrigin = 'anonymous';
    img.src = imageUrl;

    return () => {
      img.onload = null;
    };
  }, [imageUrl]);

  // Get relative pointer position
  const getRelativePointerPosition = (stage: Konva.Stage) => {
    const pointerPosition = stage.getPointerPosition();
    if (!pointerPosition) return null;

    const transform = stage.getAbsoluteTransform().copy();
    transform.invert();

    return transform.point(pointerPosition);
  };

  // Handle stage click
  const handleStageClick = (e: KonvaEventObject<MouseEvent>) => {
    if (isDoubleClick) return;

    // Ignore click that ends a pan drag in measure mode
    if (effectiveDrawingMode === 'measure' && stageDragMovedRef.current) {
      stageDragMovedRef.current = false;
      return;
    }

    const now = Date.now();
    const timeDiff = now - lastClickTime;

    if (timeDiff < 800) {
      setIsDoubleClick(true);
      setTimeout(() => setIsDoubleClick(false), 1000);
      return;
    }

    setLastClickTime(now);
    if (readOnly) return;

    const stage = e.target.getStage();
    if (!stage || !actualImageHeight || !actualImageWidth) return;

    const relativePos = getRelativePointerPosition(stage);
    if (!relativePos) return;

    if (effectiveDrawingMode === 'roi') {
      onAddROIPoint(relativePos.x, relativePos.y);
    } else if (effectiveDrawingMode === 'tripwire' || effectiveDrawingMode === 'tripwire-direction') {
      onAddTripwirePoint(relativePos.x, relativePos.y);
    } else if (effectiveDrawingMode === 'measure') {
      onAddMeasurePoint?.(relativePos.x, relativePos.y);
    } else {
      setSelectedId(null);
    }
  };

  // Handle double-click to finish ROI
  const handleStageDoubleClick = (e: KonvaEventObject<MouseEvent>) => {
    setIsDoubleClick(false);
    if (readOnly) return;

    if (effectiveDrawingMode === 'roi') {
      const stage = e.target.getStage();
      if (!stage) return;
      
      const relativePos = getRelativePointerPosition(stage);
      if (!relativePos) return;

      // Add final point and finish
      onAddROIPoint(relativePos.x, relativePos.y);
      setTimeout(() => onFinishROI(), 100);
    }

    e.evt.preventDefault();
    e.evt.stopPropagation();
  };

  // Handle right-click to finish ROI
  const handleStageRightClick = (e: KonvaEventObject<MouseEvent>) => {
    e.evt.preventDefault();
    if (readOnly) return;
    if (effectiveDrawingMode === 'roi') {
      onFinishROI();
    }
  };

  // Handle mouse move for cursor tracking
  const handleStageMouseMove = (e: KonvaEventObject<MouseEvent>) => {
    const stage = e.target.getStage();
    if (!stage) return;

    const relativePos = getRelativePointerPosition(stage);
    if (relativePos) {
      setCursorPos(relativePos);
    }
  };

  // Keyboard shortcuts
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const tagName = document.activeElement?.tagName.toLowerCase();
      if (tagName === 'input' || tagName === 'textarea') {
        return;
      }

      if (effectiveDrawingMode === 'roi' && (e.key === 'f' || e.key === 'F')) {
        onFinishROI();
      } else if ((e.key === 'Backspace' || e.key === 'Delete') && selectedId) {
        // Handle deletion if needed
      }
    },
    [effectiveDrawingMode, selectedId, onFinishROI]
  );

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [handleKeyDown]);

  // Update container size
  const updateContainerSize = useCallback(() => {
    const container = containerRef.current;
    if (container) {
      const rect = container.getBoundingClientRect();
      const newSize = {
        width: rect.width || container.offsetWidth,
        height: rect.height || container.offsetHeight,
      };
      if (newSize.width > 0 && newSize.height > 0) {
        setContainerSize(prevSize => {
          const widthChanged = Math.abs(prevSize.width - newSize.width) > 1;
          const heightChanged = Math.abs(prevSize.height - newSize.height) > 1;
          return widthChanged || heightChanged ? newSize : prevSize;
        });
      }
    }
  }, []);

  useEffect(() => {
    const attemptUpdate = () => updateContainerSize();
    
    // Multiple attempts to ensure we get container size
    attemptUpdate();
    const timeout1 = setTimeout(attemptUpdate, 10);
    const timeout2 = setTimeout(attemptUpdate, 50);
    const timeout3 = setTimeout(attemptUpdate, 100);
    const timeout4 = setTimeout(attemptUpdate, 300);

    window.addEventListener('resize', updateContainerSize);

    let resizeObserver: ResizeObserver | null = null;
    if (containerRef.current && window.ResizeObserver) {
      resizeObserver = new ResizeObserver(() => updateContainerSize());
      resizeObserver.observe(containerRef.current);
    }

    return () => {
      clearTimeout(timeout1);
      clearTimeout(timeout2);
      clearTimeout(timeout3);
      clearTimeout(timeout4);
      window.removeEventListener('resize', updateContainerSize);
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
    };
  }, [updateContainerSize]);

  // Additional update when image loads
  useEffect(() => {
    if (isImageLoaded) {
      const timeout1 = setTimeout(() => updateContainerSize(), 50);
      const timeout2 = setTimeout(() => updateContainerSize(), 200);
      return () => {
        clearTimeout(timeout1);
        clearTimeout(timeout2);
      };
    }
  }, [isImageLoaded, updateContainerSize]);

  // Fit stage to image - runs when image loads or container resizes
  useEffect(() => {
    if (isImageLoaded && stageRef.current && actualImageWidth && actualImageHeight && 
        containerSize.width > 0 && containerSize.height > 0) {
      
      // Small delay to ensure DOM is ready
      const timeout = setTimeout(() => {
        if (!stageRef.current) return;
        
        fitImageToContainer({
          stage: stageRef.current,
          imageWidth: actualImageWidth,
          imageHeight: actualImageHeight,
          containerWidth: containerSize.width,
          containerHeight: containerSize.height,
        });
      }, 100);

      return () => clearTimeout(timeout);
    }
  }, [isImageLoaded, actualImageWidth, actualImageHeight, containerSize, fitImageToContainer]);

  // Handle zoom
  const handleWheel = (e: KonvaEventObject<WheelEvent>) => {
    e.evt.preventDefault();

    const stage = stageRef.current;
    if (!stage) return;

    const oldScale = stage.scaleX();
    const pointer = stage.getPointerPosition();
    if (!pointer) return;

    const mousePointTo = {
      x: (pointer.x - stage.x()) / oldScale,
      y: (pointer.y - stage.y()) / oldScale,
    };

    const direction = e.evt.deltaY > 0 ? -1 : 1;
    const factor = 1.1;
    const newScale = Math.max(minZoom, Math.min(maxZoom, direction > 0 ? oldScale * factor : oldScale / factor));

    stage.scale({ x: newScale, y: newScale });

    const newPos = {
      x: pointer.x - mousePointTo.x * newScale,
      y: pointer.y - mousePointTo.y * newScale,
    };

    stage.position(newPos);
  };

  // Get drawing ROI
  const drawingROI = rois.find(r => r.id === 'drawing');
  const drawingTripwire = tripwires.find(t => t.id === 'drawing');

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
        Loading image...
      </Box>
    );
  }

  const getCursor = () => {
    if (effectiveDrawingMode === 'measure') return 'crosshair';
    if (effectiveDrawingMode !== 'none') return 'crosshair';
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
      {/* Helper text */}
      {effectiveDrawingMode === 'roi' && drawingROI && drawingROI.points.length >= 2 && (
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
          Press <strong>F</strong> key or right-click to finish ROI
        </Box>
      )}

      {/* Finish ROI Button */}
      {effectiveDrawingMode === 'roi' && drawingROI && drawingROI.points.length >= 3 && (
        <Button
          variant="contained"
          size="small"
          sx={{
            position: 'absolute',
            bottom: 16,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 1000,
          }}
          onClick={(e) => {
            e.stopPropagation();
            onFinishROI();
          }}
        >
          Finish ROI (F) - {drawingROI.points.length} points
        </Button>
      )}

      {containerSize.width > 0 && containerSize.height > 0 ? (
        <Stage
          ref={stageRef}
          width={containerSize.width}
          height={containerSize.height}
          draggable={effectiveDrawingMode === 'none' || effectiveDrawingMode === 'measure'}
          onDragStart={() => {
            stageDragMovedRef.current = false;
          }}
          onDragMove={() => {
            stageDragMovedRef.current = true;
          }}
          onClick={handleStageClick}
          onDblClick={handleStageDoubleClick}
          onContextMenu={handleStageRightClick}
          onMouseMove={handleStageMouseMove}
          onWheel={handleWheel}
        >
          <Layer>
            {/* Background Image */}
            {imageElement && (
              <Image 
                image={imageElement} 
                width={actualImageWidth} 
                height={actualImageHeight} 
              />
            )}

            {/* Floor-plan scale measure points */}
            {effectiveDrawingMode === 'measure' && measurePoints.length > 0 && (
              <React.Fragment>
                {measurePoints.length === 2 && (
                  <Line
                    points={[
                      measurePoints[0].x,
                      measurePoints[0].y,
                      measurePoints[1].x,
                      measurePoints[1].y,
                    ]}
                    stroke="#76b900"
                    strokeWidth={3}
                  />
                )}
                {measurePoints.map((point, idx) => (
                  <React.Fragment key={`measure-${idx}`}>
                    <Circle
                      x={point.x}
                      y={point.y}
                      radius={6}
                      fill={idx === 0 ? '#00bcd4' : '#ff9800'}
                      stroke="white"
                      strokeWidth={2}
                    />
                    <Text
                      x={point.x + 8}
                      y={point.y - 18}
                      text={`${idx + 1}`}
                      fontSize={16}
                      fill="white"
                      fontStyle="bold"
                      stroke="black"
                      strokeWidth={0.5}
                    />
                  </React.Fragment>
                ))}
              </React.Fragment>
            )}

            {/* Render finished ROIs */}
            {showAnnotations && rois.filter(r => r.id !== 'drawing').map(roi => (
              <React.Fragment key={roi.id}>
                {/* Polygon */}
                {roi.points.length > 2 && (
                  <Line
                    points={roi.points.map(p => [p.x, p.y]).flat()}
                    stroke={roi.color || '#00ff00'}
                    strokeWidth={2}
                    fill={roi.color || '#00ff00'}
                    fillOpacity={0.2}
                    closed={true}
                    onClick={() => onROIClick?.(roi)}
                  />
                )}
                {/* Vertices */}
                {roi.points.map((point, idx) => (
                  <React.Fragment key={`${roi.id}-point-${idx}`}>
                    <Circle
                      x={point.x}
                      y={point.y}
                      radius={5}
                      fill={roi.color || '#00ff00'}
                      stroke="white"
                      strokeWidth={2}
                    />
                    <Text
                      x={point.x - 10}
                      y={point.y - 25}
                      text={idx.toString()}
                      fontSize={16}
                      fill="white"
                      fontStyle="bold"
                      stroke="black"
                      strokeWidth={0.5}
                    />
                  </React.Fragment>
                ))}
              </React.Fragment>
            ))}

            {/* Render drawing ROI */}
            {showAnnotations && drawingROI && (
              <React.Fragment>
                {/* Current polygon line */}
                {drawingROI.points.length > 0 && (
                  <Line
                    points={[
                      ...drawingROI.points.map(p => [p.x, p.y]).flat(),
                      ...(cursorPos && effectiveDrawingMode === 'roi' ? [cursorPos.x, cursorPos.y] : [])
                    ]}
                    stroke={drawingROI.color || '#00ff00'}
                    strokeWidth={2}
                    dash={[5, 5]}
                    opacity={0.7}
                  />
                )}
                {/* Vertices */}
                {drawingROI.points.map((point, idx) => (
                  <React.Fragment key={`drawing-point-${idx}`}>
                    <Circle
                      x={point.x}
                      y={point.y}
                      radius={5}
                      fill={drawingROI.color || '#00ff00'}
                      stroke="white"
                      strokeWidth={2}
                    />
                    <Text
                      x={point.x - 10}
                      y={point.y - 25}
                      text={idx.toString()}
                      fontSize={16}
                      fill="white"
                      fontStyle="bold"
                      stroke="black"
                      strokeWidth={0.5}
                    />
                  </React.Fragment>
                ))}
              </React.Fragment>
            )}

            {/* Render finished tripwires */}
            {showAnnotations && tripwires.filter(t => t.id !== 'drawing').map(tripwire => {
              const hasDirection = tripwire.direction !== undefined;
              const color = tripwire.color || (hasDirection ? '#ffff00' : '#ff0000');

              return (
                <React.Fragment key={tripwire.id}>
                  {/* Line */}
                  <Line
                    points={[tripwire.start.x, tripwire.start.y, tripwire.end.x, tripwire.end.y]}
                    stroke={color}
                    strokeWidth={3}
                    onClick={() => onTripwireClick?.(tripwire)}
                  />
                  {/* Start point */}
                  <Circle
                    x={tripwire.start.x}
                    y={tripwire.start.y}
                    radius={5}
                    fill={color}
                    stroke="white"
                    strokeWidth={2}
                  />
                  <Text
                    x={tripwire.start.x - 10}
                    y={tripwire.start.y - 25}
                    text="0"
                    fontSize={16}
                    fill="white"
                    fontStyle="bold"
                    stroke="black"
                    strokeWidth={0.5}
                  />
                  {/* End point */}
                  <Circle
                    x={tripwire.end.x}
                    y={tripwire.end.y}
                    radius={5}
                    fill={color}
                    stroke="white"
                    strokeWidth={2}
                  />
                  <Text
                    x={tripwire.end.x - 10}
                    y={tripwire.end.y - 25}
                    text="1"
                    fontSize={16}
                    fill="white"
                    fontStyle="bold"
                    stroke="black"
                    strokeWidth={0.5}
                  />
                  {/* Arrow for direction */}
                  {hasDirection && (() => {
                    const dx = tripwire.end.x - tripwire.start.x;
                    const dy = tripwire.end.y - tripwire.start.y;
                    const angle = Math.atan2(dy, dx);
                    const arrowLength = 20;
                    const arrowAngle = Math.PI / 6;

                    return (
                      <>
                        <Line
                          points={[
                            tripwire.end.x,
                            tripwire.end.y,
                            tripwire.end.x - arrowLength * Math.cos(angle - arrowAngle),
                            tripwire.end.y - arrowLength * Math.sin(angle - arrowAngle),
                          ]}
                          stroke={color}
                          strokeWidth={3}
                        />
                        <Line
                          points={[
                            tripwire.end.x,
                            tripwire.end.y,
                            tripwire.end.x - arrowLength * Math.cos(angle + arrowAngle),
                            tripwire.end.y - arrowLength * Math.sin(angle + arrowAngle),
                          ]}
                          stroke={color}
                          strokeWidth={3}
                        />
                      </>
                    );
                  })()}
                </React.Fragment>
              );
            })}

            {/* Render drawing tripwire */}
            {showAnnotations && drawingTripwire && (
              <React.Fragment>
                {/* Line */}
                {drawingTripwire.start && (
                  <>
                    <Line
                      points={[
                        drawingTripwire.start.x,
                        drawingTripwire.start.y,
                        cursorPos && (effectiveDrawingMode === 'tripwire' || effectiveDrawingMode === 'tripwire-direction') ? cursorPos.x : drawingTripwire.end.x,
                        cursorPos && (effectiveDrawingMode === 'tripwire' || effectiveDrawingMode === 'tripwire-direction') ? cursorPos.y : drawingTripwire.end.y,
                      ]}
                      stroke={drawingTripwire.color || '#ff0000'}
                      strokeWidth={3}
                      dash={[5, 5]}
                      opacity={0.7}
                    />
                    {/* Start point */}
                    <Circle
                      x={drawingTripwire.start.x}
                      y={drawingTripwire.start.y}
                      radius={5}
                      fill={drawingTripwire.color || '#ff0000'}
                      stroke="white"
                      strokeWidth={2}
                    />
                    <Text
                      x={drawingTripwire.start.x - 10}
                      y={drawingTripwire.start.y - 25}
                      text="0"
                      fontSize={16}
                      fill="white"
                      fontStyle="bold"
                      stroke="black"
                      strokeWidth={0.5}
                    />
                  </>
                )}
              </React.Fragment>
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

export default AnnotationCanvas;

