// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { createRoot } from "react-dom/client";

import { Group, Path, Shape } from "react-konva";
import { Portal } from "react-konva-utils";
import wellknown from "wellknown";

import VizTooltip from "../generic/VizTooltip";
import { TopViewContext } from "./TopViewContext";
import {
  CAMERA_ICON_SVG_PATH_DIRECTIONAL,
  CAMERA_ICON_SVG_PATH_NON_DIRECTIONAL,
} from "../../utils/Constants";

// MDX Calibration file adds FOV polygon value as a WKT string.
// https://www.vertica.com/docs/9.3.x/HTML/Content/Authoring/AnalyzingData/Geospatial/Spatial_Definitions/WellknownTextWKT.htm
//
// The value could be a - polygon (with/without holes), multipolygons
//
// Need to parse an array
// example:
//   {
//     "name": "fieldOfViewPolygon",
//     "value": "POLYGON((32.39147622088676 27.72174026541681, 37.30339706547104 23.53302611402356, 23.888735595644498 26.37998053209448, 18.37785774763376 17.302857646758667, 15.06215357071955 1.9512522630593612, 38.851128391907295 1.7875684444191782, 32.39147622088676 27.72174026541681))"
// }
const ParseWktFov = (cameraId, wktString) => {
  try {
    const parsed = wellknown.parse(wktString);
    if (!parsed) {
      throw Error("Invalid WKT string");
    }
    if (parsed.type !== "Polygon" && parsed.type !== "MultiPolygon") {
      throw Error("Invalid shape type - Polygon/MultiPolygon allowed");
    }
    return parsed;
  } catch (error) {
    console.error(
      `${cameraId} Icon Draw : Error parsing WKT Fov Polygon Input: ${error.message}`
    );
    return null;
  }
};

export default class VizCameraIcon extends React.PureComponent {
  static contextType = TopViewContext;

  state = {
    tooltip: null,
    showFov: false,
  };

  tooltipContainerRef = null;
  tooltipRoot = null;

  componentDidMount() {
    this.tooltipContainerRef = document.createElement('div');
    document.body.appendChild(this.tooltipContainerRef);
    this.tooltipRoot = createRoot(this.tooltipContainerRef);
  }

  componentDidUpdate(prevProps, prevState) {
    if (this.state.tooltip !== prevState.tooltip && this.tooltipRoot) {
      if (this.state.tooltip) {
        this.tooltipRoot.render(
          <VizTooltip
            stagePosition={this.state.tooltip.stagePosition}
            text={this.state.tooltip.text}
            containerRef={this.state.tooltip.containerRef}
            scale={this.state.tooltip.scale}
          />
        );
      } else {
        this.tooltipRoot.render(null);
      }
    }
  }

  componentWillUnmount() {
    const root = this.tooltipRoot;
    const el = this.tooltipContainerRef;
    this.tooltipRoot = null;
    this.tooltipContainerRef = null;
    queueMicrotask(() => {
      root?.unmount();
      if (el && document.body.contains(el)) {
        document.body.removeChild(el);
      }
    });
  }

  /**
   * Hover is bound on the Group (not the icon Path alone). If it were only on the
   * icon, moving onto the FOV wedge would fire mouseOut on the icon → hide FOV →
   * pointer hits the icon again → show FOV, causing a tight flicker/re-render loop.
   */
  onCameraGroupMouseEnter = (event, cameraConfig) => {
    const stage = event.currentTarget.getStage();
    let tooltip = null;
    if (stage) {
      const containerEl = stage.container();
      tooltip = {
        stagePosition: stage.getPointerPosition(),
        text: cameraConfig.id,
        containerRef: containerEl,
        scale: this.context ? this.context.stageScale : 1,
      };
    }
    this.setState({ showFov: true, tooltip });
    this.props.onMouseOver && this.props.onMouseOver(event, cameraConfig);
  };

  onCameraGroupMouseLeave = (event, cameraConfig) => {
    this.setState({ showFov: false, tooltip: null });
    this.props.onMouseOut && this.props.onMouseOut(event, cameraConfig);
  };

  /**
   * Parse attributes from camera config
   */
  _parseAttributes() {
    const attributes = {};
    this.props.cameraConfig.attributes &&
      this.props.cameraConfig.attributes.forEach((attr) => {
        attributes[attr.name] = attr.value;
      });
    return attributes;
  }

  /**
   * Convert world coordinates to canvas coordinates
   */
  _convertCoordinates(worldTranslationOffset) {
    let loc = Object.assign({}, this.props.cameraConfig.coordinates);
    loc.x += worldTranslationOffset.x;
    loc.x *= this.props.m2PxScaleFactor;
    loc.y += worldTranslationOffset.y;
    loc.y *= this.props.m2PxScaleFactor;
    return this.props.coordinateConverter.convert(loc);
  }

  /**
   * Process a single coordinate in a sub-polygon
   */
  _processSubPolygonCoordinate(
    coord,
    subPolygonKey,
    worldTranslationOffset,
    subPolygonCanvasCoords
  ) {
    const canvasCoord = this.props.coordinateConverter.convert({
      x:
        (coord[0] + worldTranslationOffset.x) *
        this.props.m2PxScaleFactor,
      y:
        (coord[1] + worldTranslationOffset.y) *
        this.props.m2PxScaleFactor,
    });

    if (subPolygonKey === 0) {
      // outer points of the polygon
      subPolygonCanvasCoords.push(canvasCoord.x);
      subPolygonCanvasCoords.push(canvasCoord.y);
    } else {
      // holes of the polygon
      subPolygonCanvasCoords.unshift(canvasCoord.y);
      subPolygonCanvasCoords.unshift(canvasCoord.x);
    }
  }

  /**
   * Process a single sub-polygon
   */
  _processSubPolygon(
    subPolygon,
    subPolygonKey,
    worldTranslationOffset
  ) {
    const subPolygonCanvasCoords = [];
    subPolygon.forEach((coord) => {
      this._processSubPolygonCoordinate(
        coord,
        subPolygonKey,
        worldTranslationOffset,
        subPolygonCanvasCoords
      );
    });
    return subPolygonCanvasCoords;
  }

  /**
   * Process a single polygon and create Shape element
   */
  _processPolygon(polygon, polygonKey, worldTranslationOffset) {
    const subPolygonsCanvasCoords = [];
    polygon.forEach((subPolygon, subPolygonKey) => {
      const subPolygonCanvasCoords = this._processSubPolygon(
        subPolygon,
        subPolygonKey,
        worldTranslationOffset
      );
      subPolygonsCanvasCoords.push(subPolygonCanvasCoords);
    });

    return (
      <Shape
        key={`${this.props.cameraConfig.id}-${polygonKey}-fov-polygon`}
        sceneFunc={(context, shape) => {
          context.beginPath();
          subPolygonsCanvasCoords.forEach((points) => {
            context.moveTo(points[0], points[1]);
            points.forEach((point, index) => {
              if (index % 2 === 0) {
                context.lineTo(point, points[index + 1]);
              }
            });
            context.closePath();
          });
          context.lineJoin = "bevel";
          context.fillStrokeShape(shape);
        }}
        fill="#b8d7f8"
        opacity={0.5}
      />
    );
  }

  /**
   * Render FOV polygons from WKT string
   */
  _renderFovPolygons(attributes, worldTranslationOffset) {
    if (
      !attributes.fieldOfViewPolygon ||
      attributes.fieldOfViewPolygon === ""
    ) {
      return [];
    }

    const fovParsed = ParseWktFov(
      this.props.cameraConfig.id,
      attributes.fieldOfViewPolygon
    );

    if (!fovParsed) {
      return [];
    }

    let parsedPolygonsCoordinates = [];
    if (fovParsed.type === "Polygon") {
      parsedPolygonsCoordinates.push(fovParsed.coordinates);
    } else {
      // MultiPolygon
      parsedPolygonsCoordinates = fovParsed.coordinates;
    }

    return parsedPolygonsCoordinates.map((polygon, polygonKey) =>
      this._processPolygon(polygon, polygonKey, worldTranslationOffset)
    );
  }

  /**
   * Render FOV SVG arc
   */
  _renderFovSvgArc(direction, depth, fieldOfView, loc) {
    const fovRadiusInPixels = depth * this.props.m2PxScaleFactor;
    const arcStartAngleRadians =
      ((direction - fieldOfView / 2) * Math.PI) / 180;
    const arcEndAngleRadians =
      ((direction + fieldOfView / 2) * Math.PI) / 180;
    const fieldOfViewSVGPathArray = [
      "M",
      fovRadiusInPixels,
      fovRadiusInPixels,
      "L",
      fovRadiusInPixels * (1 + Math.cos(arcStartAngleRadians)),
      fovRadiusInPixels * (1 + Math.sin(arcStartAngleRadians)),
      "A",
      fovRadiusInPixels,
      fovRadiusInPixels,
      0,
      0,
      1,
      fovRadiusInPixels * (1 + Math.cos(arcEndAngleRadians)),
      fovRadiusInPixels * (1 + Math.sin(arcEndAngleRadians)),
      "Z",
    ];

    return (
      <Path
        x={loc.x}
        y={loc.y}
        data={fieldOfViewSVGPathArray.join(" ")}
        stroke="#b8d7f8"
        fill="#b8d7f8"
        opacity={0.5}
        rotation={270}
        scale={{
          x: this.props.scaleFactor,
          y: this.props.scaleFactor,
        }}
        offset={{
          x: fovRadiusInPixels,
          y: fovRadiusInPixels,
        }}
      />
    );
  }

  /**
   * Render FOV elements (polygons or SVG arc)
   */
  _renderFovElements(attributes, worldTranslationOffset, loc) {
    if (!this.state.showFov) {
      return { fovPolygonEls: [], fovSvgEl: null };
    }

    const direction = parseInt(attributes.direction);
    const fieldOfView = parseInt(attributes.fieldOfView);
    const depth = parseInt(attributes.depth);

    const fovPolygonEls = this._renderFovPolygons(attributes, worldTranslationOffset);

    if (fovPolygonEls.length > 0) {
      return { fovPolygonEls, fovSvgEl: null };
    }

    if (!isNaN(direction) && !isNaN(depth) && !isNaN(fieldOfView)) {
      const fovSvgEl = this._renderFovSvgArc(direction, depth, fieldOfView, loc);
      return { fovPolygonEls: [], fovSvgEl };
    }

    console.warn(
      `${this.props.cameraConfig.id} Icon Draw - No valid info found to draw Field of View Arc or Polygons`
    );
    return { fovPolygonEls: [], fovSvgEl: null };
  }

  render() {
    if (
      !this.props.cameraConfig.coordinates ||
      !this.props.coordinateConverter ||
      !this.props.m2PxScaleFactor ||
      !this.props.scaleFactor
    ) {
      console.error(
        `${this.props.cameraConfig.id} Icon Draw - Missing info to process camera coordinates`
      );
      return null;
    }

    const iconMult = this.props.iconScaleMultiplier ?? 1;
    const iconScale = {
      x: 0.15 * this.props.scaleFactor * iconMult,
      y: 0.15 * this.props.scaleFactor * iconMult,
    };

    const worldTranslationOffset = this.props.worldTranslationOffset || {
      x: 0,
      y: 0,
    };
    const loc = this._convertCoordinates(worldTranslationOffset);
    const attributes = this._parseAttributes();
    const direction = parseInt(attributes.direction);

    const { fovPolygonEls, fovSvgEl } = this._renderFovElements(
      attributes,
      worldTranslationOffset,
      loc
    );

    // Icon + FOV on dedicated Stage layer (see TopView: camera-viz-layer)
    return (
      <Portal selector=".camera-viz-layer">
        <Group
          key={this.props.cameraConfig.id}
          name={`camera-viz-${this.props.cameraConfig.id}`}
          onMouseEnter={(e) => {
            this.onCameraGroupMouseEnter(e, this.props.cameraConfig);
          }}
          onMouseLeave={(e) => {
            this.onCameraGroupMouseLeave(e, this.props.cameraConfig);
          }}
        >
          <Path
            x={loc.x}
            y={loc.y}
            data={
              isNaN(direction)
                ? CAMERA_ICON_SVG_PATH_NON_DIRECTIONAL
                : CAMERA_ICON_SVG_PATH_DIRECTIONAL
            }
            stroke="#150861"
            strokeWidth={12.5}
            fill="#348ceb"
            opacity={0.5}
            rotation={isNaN(direction) ? 0 : direction}
            scale={
              isNaN(direction)
                ? { x: iconScale.x * 1.25, y: iconScale.y * 1.25 }
                : iconScale
            }
            offset={{
              x: 50,
              y: 100,
            }}
          />
          {fovSvgEl}
          {fovPolygonEls}
        </Group>
      </Portal>
    );
  }
}
