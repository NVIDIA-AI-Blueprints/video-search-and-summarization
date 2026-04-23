// SPDX-License-Identifier: MIT
import React, { useRef, useEffect, useLayoutEffect, useState, useCallback } from 'react';
import { Stage, Layer, Image as KonvaImage, Rect } from 'react-konva';
import { SearchByImageFrameData } from '../types';

interface SearchByImageOverlayProps {
  frameData: SearchByImageFrameData;
  selectedObjectId?: string | null;
  onSelectObject?: (objectId: string | null) => void;
}

export const SearchByImageOverlay: React.FC<SearchByImageOverlayProps> = ({
  frameData,
  selectedObjectId: controlledSelectedObjectId,
  onSelectObject,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageContainerRef = useRef<HTMLDivElement>(null);
  const [scaleFactor, setScaleFactor] = useState(1);
  const [hoveredObjectId, setHoveredObjectId] = useState<string | null>(null);
  const [internalSelectedObjectId, setInternalSelectedObjectId] = useState<string | null>(null);
  const selectedObjectId = controlledSelectedObjectId ?? internalSelectedObjectId;

  // Reset selection whenever the underlying frame changes (new frame = new set of objects).
  useEffect(() => {
    if (controlledSelectedObjectId === undefined) {
      setInternalSelectedObjectId(null);
    }
  }, [frameData, controlledSelectedObjectId]);

  const updateScale = useCallback(() => {
    const el = containerRef.current;
    if (!el || !frameData.frameImage) return;
    const cw = el.clientWidth;
    const ch = el.clientHeight;
    if (cw <= 0 || ch <= 0) return;
    const scaleX = cw / frameData.frameImage.width;
    const scaleY = ch / frameData.frameImage.height;
    const next = Math.min(scaleX, scaleY);
    setScaleFactor((prev) => (Math.abs(prev - next) > 1e-4 ? next : prev));
  }, [frameData.frameImage]);

  // Measure after layout (not after commit) to avoid a first-paint glitch where
  // the dialog/flex container hasn't been sized yet.
  useLayoutEffect(() => {
    updateScale();
    // Second measure on next frame in case flex layout settles after first commit.
    const raf = requestAnimationFrame(updateScale);
    return () => cancelAnimationFrame(raf);
  }, [updateScale]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(updateScale);
    observer.observe(el);

    // Browser zoom (Ctrl+/-) may not change clientWidth in CSS pixels so
    // ResizeObserver can stay silent. Listen to window/visualViewport resize
    // explicitly so we recompute the scale on zoom and DPR changes.
    window.addEventListener('resize', updateScale);
    const vv = window.visualViewport;
    vv?.addEventListener('resize', updateScale);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', updateScale);
      vv?.removeEventListener('resize', updateScale);
    };
  }, [updateScale]);

  if (!frameData.frameImage) {
    return (
      <div className="flex items-center justify-center h-full bg-black text-white">
        Loading frame...
      </div>
    );
  }

  const stageWidth = frameData.frameImage.width * scaleFactor;
  const stageHeight = frameData.frameImage.height * scaleFactor;

  const setSelectedObject = (objectId: string | null) => {
    if (onSelectObject) {
      onSelectObject(objectId);
      return;
    }
    setInternalSelectedObjectId(objectId);
  };

  const unselectedBoxes: React.ReactNode[] = [];
  let selectedBox: React.ReactNode = null;

  frameData.objects.forEach((obj, idx) => {
    const isSelected = obj.id === selectedObjectId;
    const isHovered = obj.id === hoveredObjectId;

    const x = obj.bbox.leftX * scaleFactor;
    const y = obj.bbox.topY * scaleFactor;
    const w = (obj.bbox.rightX - obj.bbox.leftX) * scaleFactor;
    const h = (obj.bbox.bottomY - obj.bbox.topY) * scaleFactor;

    const strokeWidth = isSelected ? 4 : isHovered ? 3.5 : 2.5;

    const node = (
      <Rect
        key={obj.id || idx}
        x={x}
        y={y}
        width={w}
        height={h}
        fill="transparent"
        stroke={isSelected ? '#76b900' : '#ffffff'}
        strokeWidth={strokeWidth}
        shadowColor="#000000"
        shadowBlur={isSelected ? 14 : isHovered ? 10 : 6}
        shadowOpacity={1}
        shadowOffsetX={0}
        shadowOffsetY={0}
        onClick={() => setSelectedObject(isSelected ? null : obj.id)}
        onTap={() => setSelectedObject(isSelected ? null : obj.id)}
        onMouseEnter={(e) => {
          setHoveredObjectId(obj.id);
          const container = e.target.getStage()?.container();
          if (container) container.style.cursor = 'pointer';
        }}
        onMouseLeave={(e) => {
          setHoveredObjectId(null);
          const container = e.target.getStage()?.container();
          if (container) container.style.cursor = 'default';
        }}
      />
    );

    if (isSelected) {
      selectedBox = node;
    } else {
      unselectedBoxes.push(node);
    }
  });

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      {/* Canvas area */}
      <div
        ref={containerRef}
        className="relative flex flex-1 items-center justify-center overflow-hidden bg-black min-h-0 min-w-0"
        style={{ minHeight: 0 }}
      >
        <div ref={stageContainerRef} style={{ position: 'relative' }}>
          {scaleFactor > 0 && (
            <Stage width={stageWidth} height={stageHeight}>
              <Layer>
                <KonvaImage
                  image={frameData.frameImage}
                  x={0}
                  y={0}
                  width={stageWidth}
                  height={stageHeight}
                />
                {unselectedBoxes}
                {/* Render selected bbox last for highest z-order */}
                {selectedBox}
              </Layer>
            </Stage>
          )}
        </div>
      </div>
    </div>
  );
};
