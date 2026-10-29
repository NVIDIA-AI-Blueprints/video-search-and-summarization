// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { useEffect, useRef, useState } from "react";

/**
 * Tooltip for 2D Canvas Viz Elements in MDX apps.
 * Renders as a floating HTML div to prevent clipping at canvas edges.
 */
const VizTooltip = (props) => {
  const tooltipRef = useRef(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // Prefer viewport coordinates (e.g. from evt.clientX/clientY) for reliable positioning
    if (props.clientPosition) {
      const padding = 10;
      let x = props.clientPosition.x + 10;
      let y = props.clientPosition.y + 10;
      // Set position before showing so we never flash at (0,0) or stale position
      setPosition({ x, y });
      setVisible(true);
      setTimeout(() => {
        if (tooltipRef.current) {
          const tooltipRect = tooltipRef.current.getBoundingClientRect();
          let x2 = x;
          let y2 = y;
          if (x2 + tooltipRect.width + padding > window.innerWidth) {
            x2 = props.clientPosition.x - tooltipRect.width - 10;
          }
          if (y2 + tooltipRect.height + padding > window.innerHeight) {
            y2 = props.clientPosition.y - tooltipRect.height - 10;
          }
          if (x2 < padding) x2 = padding;
          if (y2 < padding) y2 = padding;
          if (x2 !== x || y2 !== y) setPosition({ x: x2, y: y2 });
        }
      }, 0);
      return;
    }

    if (
      !props.stagePosition ||
      !props.containerRef ||
      (props.stagePosition.x === 0 && props.stagePosition.y === 0)
    ) {
      setVisible(false);
      return;
    }

    const container = props.containerRef;
    const canvasRect = container.getBoundingClientRect();
    
    // Get the scale from CSS transform if not provided as prop
    let scale = props.scale || 1;
    
    // If no scale prop, try to extract it from the container's parent transform
    if (!props.scale && container.parentElement) {
      const computedStyle = window.getComputedStyle(container.parentElement);
      const transform = computedStyle.transform;
      if (transform && transform !== 'none') {
        // Extract scale from matrix transform
        const matrixMatch = transform.match(/matrix\(([^)]+)\)/);
        if (matrixMatch) {
          const matrixValues = matrixMatch[1].split(',').map(parseFloat);
          // For 2D transform, scale is the first value (scaleX)
          scale = matrixValues[0];
        }
      }
    }
    
    // Calculate initial position relative to viewport
    // Stage position needs to be multiplied by scale because the wrapper is CSS-scaled
    let x = canvasRect.left + (props.stagePosition.x * scale) + 10;
    let y = canvasRect.top + (props.stagePosition.y * scale) + 10;
    setPosition({ x, y });
    setVisible(true);

    // Use setTimeout to refine position so tooltip stays in viewport
    setTimeout(() => {
      if (tooltipRef.current) {
        const tooltipRect = tooltipRef.current.getBoundingClientRect();
        const padding = 10;

        // Adjust horizontal position if tooltip would go off right edge
        if (x + tooltipRect.width + padding > window.innerWidth) {
          x = canvasRect.left + (props.stagePosition.x * scale) - tooltipRect.width - 10;
        }

        // Adjust vertical position if tooltip would go off bottom edge
        if (y + tooltipRect.height + padding > window.innerHeight) {
          y = canvasRect.top + (props.stagePosition.y * scale) - tooltipRect.height - 10;
        }

        // Ensure tooltip doesn't go off left edge
        if (x < padding) {
          x = padding;
        }

        // Ensure tooltip doesn't go off top edge
        if (y < padding) {
          y = padding;
        }

        setPosition({ x, y });
      }
    }, 0);
  }, [props.stagePosition, props.clientPosition, props.containerRef, props.scale]);

  if (!visible || !props.text) {
    return null;
  }

  return (
    <div
      ref={tooltipRef}
      style={{
        position: "fixed",
        left: `${position.x}px`,
        top: `${position.y}px`,
        backgroundColor: "rgba(0, 0, 0, 0.85)",
        color: "white",
        padding: "6px 10px",
        borderRadius: "4px",
        fontSize: "14px",
        fontFamily: "Arial, sans-serif",
        pointerEvents: "none",
        zIndex: 10000,
        boxShadow: "0 2px 8px rgba(0, 0, 0, 0.3)",
        whiteSpace: "pre-wrap",
        maxWidth: "400px",
        overflowWrap: "break-word",
        wordBreak: "break-word",
        transition: "opacity 0.1s ease-in-out",
      }}
    >
      {props.text}
    </div>
  );
};

export default VizTooltip;
