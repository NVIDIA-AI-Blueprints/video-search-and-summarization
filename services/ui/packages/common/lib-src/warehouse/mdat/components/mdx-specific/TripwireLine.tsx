// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Line } from "react-konva";
import VizTooltip from "../generic/VizTooltip";

/**
 * TripwireLine component - Renders a tripwire line on the canvas with tooltip on hover.
 * 
 * @param {Object} props
 * @param {string} props.id - Unique identifier for the tripwire
 * @param {Array<number>} props.points - Array of coordinates [x1, y1, x2, y2]
 * @param {number} props.scaleFactor - Scale factor for stroke width and shadow
 * @param {string} [props.stroke="blue"] - Stroke color
 */
const TripwireLine = ({ id, points, scaleFactor, stroke = "blue" }) => {
  const [tooltipData, setTooltipData] = useState(null);
  const tooltipContainerRef = useRef(null);
  const tooltipRootRef = useRef(null);

  useEffect(() => {
    tooltipContainerRef.current = document.createElement('div');
    document.body.appendChild(tooltipContainerRef.current);
    tooltipRootRef.current = createRoot(tooltipContainerRef.current);

    return () => {
      const root = tooltipRootRef.current;
      const el = tooltipContainerRef.current;
      tooltipRootRef.current = null;
      tooltipContainerRef.current = null;
      queueMicrotask(() => {
        root?.unmount();
        if (el && document.body.contains(el)) {
          document.body.removeChild(el);
        }
      });
    };
  }, []);

  useEffect(() => {
    if (!tooltipRootRef.current) return;

    if (tooltipData) {
      tooltipRootRef.current.render(
        <VizTooltip
          clientPosition={tooltipData.clientPosition}
          stagePosition={tooltipData.stagePosition}
          text={`Tripwire: ${id}`}
          containerRef={tooltipData.containerRef}
        />
      );
    } else {
      tooltipRootRef.current.render(null);
    }
  }, [tooltipData, id]);

  const handleMouseEnter = (event) => {
    const stage = event.target.getStage();
    if (!stage) return;
    const evt = event.evt;
    const clientPosition =
      evt && typeof evt.clientX === "number"
        ? { x: evt.clientX, y: evt.clientY }
        : null;
    setTooltipData({
      clientPosition,
      stagePosition: stage.getPointerPosition(),
      containerRef: stage.container(),
    });
  };

  const handleMouseLeave = () => {
    setTooltipData(null);
  };

  // Only return the Konva Line element - tooltip is rendered separately
  return (
    <Line
      points={points}
      stroke={stroke}
      strokeWidth={10 * scaleFactor}
      shadowColor="#fff"
      shadowBlur={10 * scaleFactor}
      perfectDrawEnabled={false}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    />
  );
};

export default TripwireLine;
