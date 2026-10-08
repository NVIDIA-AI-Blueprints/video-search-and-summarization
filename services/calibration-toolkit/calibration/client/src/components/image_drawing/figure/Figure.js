// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component, Fragment } from "react";
import PropTypes from "prop-types";
import { Polyline, Polygon, CircleMarker, Tooltip, Marker } from "react-leaflet";
import "./ImageDrawingStyles.css";
// import {iconCamera} from "./Icons.js";
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { iconCamera } from "./Icons";

delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: require('../../../assets/camera-no-orientation.png').default,
  iconUrl: require('../../../assets/camera-no-orientation.png').default,
  shadowUrl: require('../../../assets/marker-shadow.png').default

});
/**
 * Abstract class for polygon and polyline figure for use in image canvas.
 */
class Figure extends Component {
  constructor(props) {
    super(props);
    this.state = {
      dragging: false,
      draggedPoint: null
    };
  }
  /**
   * Abstract
   */
  calculateGuides() {
    return [];
  }

  /**
   * Abstract
   * @param {*} i
   */
  onPointClick(i) {}

  /**
   * Abstract
   * @param {*} i
   */
  onPointRightClick(i) {}

  /**
   * Abstract
   * @param {*} point
   * @param {*} i
   */
  onPointMoved(point, i) {}

  /**
   * Abstract
   */
  makeExtraElements() {
    return null;
  }

  /**
   * Abstract
   */
  leafletComponent() {
    return Polygon;
  }

  /**
   * Abstract
   * @param {*} points
   */
  getRenderPoints(points) {
    return points;
  }

  /**
   *
   */
  makeGuides() {
    const guides = this.calculateGuides();
    const { color } = this.props.options;
    return guides.map((pos, i) => (
      <Polyline
        key={i}
        positions={pos}
        color={color}
        opacity={0.7}
        dashArray="5"
      />
    ));
  }

  /**
   *
   */
  hasFill() {
    return true;
  }

  render() {
    const { figure, options, skipNextClick } = this.props;
    const { id, points } = figure;
    const {
      editing,
      finished,
      sketch,
      color,
      vertexColor,
      interactive,
      onSelect,
      showImageMarkers
    } = options;
    const { draggedPoint } = this.state;

    const renderPoints = this.getRenderPoints(points);
    // console.log("getrender", renderPoints)

    const dashArray = editing ? "10" : "1";

    const lineColor = editing && sketch ? color : vertexColor || color;
    const fillColor = editing && sketch ? "rgba(0,0,0,0)" : color;
    const weight = editing && sketch ? 1 : 2;

    const classes = ["vertex"];
    if (editing) classes.push("editing");
    if (finished) classes.push("finished");

    const vcolor = vertexColor || color;
    const smallVertices = renderPoints.map((pos, i) => (
      <CircleMarker
        key={"" + i}
        fill={true}
        radius={3}
        fillOpacity={1.0}
        weight={1.5}
        center={pos}
        color={color}
      >
        {showImageMarkers && (
          <Tooltip direction="top" permanent={true}>
            {i.toString()}
          </Tooltip>
        )}
      </CircleMarker>
    ));
    const vertices = renderPoints.map((pos, i) => (
      <CircleMarker
        className={classes.concat(!i ? ["first"] : []).join(" ")}
        key={id + "-" + i}
        center={pos}
        fillOpacity={0.0}
        opacity={0.0}
        radius={11}
        onClick={() => this.onPointClick(i)}
        onContextmenu={() => this.onPointRightClick(i)}
        draggable={editing}
        onDrag={e => {
          this.setState({
            draggedPoint: { point: e.target._latlng, index: i }
          });
        }}
        onDragstart={() => this.setState({ dragging: true })}
        onDragend={e => {
          this.onPointMoved(e.target.getLatLng(), i);
          this.setState({ dragging: false, draggedPoint: null });
        }}
      >
        <CircleMarker
          color={vcolor}
          fill={true}
          weight={1.5}
          fillColor={vcolor}
          fillRule={"evenodd"}
          fillOpacity={1.0}
          radius={4.5}
          center={
            draggedPoint && draggedPoint.index === i ? draggedPoint.point : pos
          }
        />
        {showImageMarkers && (
          <Tooltip className={"red_tooltip"} direction="top" permanent={true}>
            {i.toString()}
          </Tooltip>
        )}
      </CircleMarker>
    ));

    const guideLines = this.makeGuides();
    const PolyComp = this.leafletComponent();

    return (
      <Fragment key={id}>
        <PolyComp
          positions={renderPoints}
          color={lineColor}
          weight={weight}
          fill={this.hasFill()}
          fillColor={fillColor}
          opacity={sketch ? "0.7" : "1.0"}
          dashArray={dashArray}
          interactive={id !== "trace"} // always set interactive to true, to avoid bugs in leaflet-react
          onClick={() => {
            if (interactive) {
              onSelect();
              skipNextClick();
            }
          }}
        />
        {guideLines}
        {this.makeExtraElements()}
        {!finished || editing ? vertices : smallVertices}
      </Fragment>
    );
  }
}

Figure.propTypes = {
  /** Figure geometry to render */
  figure: PropTypes.object,
  /** Figure drawing options */
  options: PropTypes.object,
  /**
   * A hack function, for whatever reason it is really hard to stop event
   * propagation in leaflet
   */
  skipNextClick: PropTypes.func
};

export class PolygonFigure extends Figure {
  constructor(props) {
    super(props);

    this.onPointClick = this.onPointClick.bind(this);
  }

  leafletComponent() {
    const {
      options: { finished }
    } = this.props;
    return finished ? Polygon : Polyline;
  }

  calculateGuides() {
    const { figure, options } = this.props;
    const { points } = figure;
    const { newPoint, finished } = options;
    const { draggedPoint } = this.state;

    const guides = [];
    if (draggedPoint) {
      const { point, index } = draggedPoint;
      const { length } = points;
      guides.push(
        [point, points[(index + 1) % length]],
        [point, points[(index - 1 + length) % length]]
      );
    }

    const additionalGuides =
      !finished && points.length > 0
        ? [[points[points.length - 1], newPoint]]
        : [];

    return guides.concat(additionalGuides);
  }

  makeExtraElements() {
    const { figure, options, skipNextClick } = this.props;
    const { id, points } = figure;
    const { editing, finished, calcDistance, onChange } = options;

    const { dragging } = this.state;

    if (!finished || !editing || dragging) {
      return [];
    }

    const midPoints = points
      .map((pos, i) => [pos, points[(i + 1) % points.length], i])
      .filter(([a, b]) => calcDistance(a, b) > 40)
      .map(([a, b, i]) => (
        <CircleMarker
          key={id + "-" + i + "-mid"}
          className="midpoint"
          center={midPoint(a, b)}
          radius={8}
          opacity={0.0}
          fillOpacity={0.0}
          onMousedown={e => {
            onChange("add", { point: midPoint(a, b), pos: i + 1, figure });
            skipNextClick();
          }}
        >
          <CircleMarker
            radius={3}
            color="white"
            fill={true}
            fillOpacity={0.5}
            opacity={0.5}
            center={midPoint(a, b)}
          />
        </CircleMarker>
      ));

    return midPoints;
  }

  onPointMoved(point, index) {
    const {
      figure,
      options: { onChange }
    } = this.props;
    onChange("move", { point, pos: index, figure });
  }

  onPointClick(i) {
    const { figure, options, skipNextClick } = this.props;
    const { points } = figure;
    const { finished, onChange } = options;

    if (!finished && (i === 0 || i === points.length - 1)) {
      if (points.length >= 3) {
        onChange("end", {});
      }
      skipNextClick();
      return false;
    }
  }

  onPointRightClick(i) {
    const { figure, options, skipNextClick } = this.props;
    const { points } = figure;
    const { finished, editing, onChange } = options;

    if (finished && editing) {
      if (points.length > 3) {
        onChange("remove", { pos: i, figure });
      }
      skipNextClick();
      return false;
    }
  }
}

export class PolylineFigure extends Figure {
  constructor(props) {
    super(props);

    this.onPointClick = this.onPointClick.bind(this);
  }

  leafletComponent() {
    return Polyline;
  }

  calculateGuides() {
    const { figure, options } = this.props;
    const { points } = figure;
    const { newPoint, finished } = options;
    const { draggedPoint } = this.state;

    const guides = [];
    if (draggedPoint) {
      const { point, index } = draggedPoint;
      const { length } = points;
      guides.push(
        [point, points[(index + 1) % length]],
        [point, points[(index - 1 + length) % length]]
      );
    }

    const additionalGuides =
      !finished && points.length > 0
        ? [[points[points.length - 1], newPoint]]
        : [];

    return guides.concat(additionalGuides);
  }

  makeExtraElements() {
    const { figure, options, skipNextClick } = this.props;
    const { id, points } = figure;
    const { editing, finished, calcDistance, onChange } = options;

    const { dragging } = this.state;

    if (!finished || !editing || dragging) {
      return [];
    }

    const midPoints = points
      .map((pos, i) => [pos, points[(i + 1) % points.length], i])
      .filter(([a, b]) => calcDistance(a, b) > 40)
      .map(([a, b, i]) => (
        <CircleMarker
          key={id + "-" + i + "-mid"}
          className="midpoint"
          center={midPoint(a, b)}
          radius={8}
          opacity={0.0}
          fillOpacity={0.0}
          onMousedown={e => {
            onChange("add", { point: midPoint(a, b), pos: i + 1, figure });
            skipNextClick();
          }}
        >
          <CircleMarker
            radius={3}
            color="white"
            fill={true}
            fillOpacity={0.5}
            opacity={0.5}
            center={midPoint(a, b)}
          />
        </CircleMarker>
      ));

    return midPoints;
  }

  onPointMoved(point, index) {
    const {
      figure,
      options: { onChange }
    } = this.props;
    onChange("move", { point, pos: index, figure });
  }

  onPointClick(i) {
    const { figure, options, skipNextClick } = this.props;
    const { points } = figure;
    const { finished, onChange } = options;

    if (!finished && i === points.length - 1) {
      if (points.length >= 2) {
        onChange("end", {});
      }
      skipNextClick();
      return false;
    }
  }

  onPointRightClick(i) {
    const { figure, options, skipNextClick } = this.props;
    const { points } = figure;
    const { finished, editing, onChange } = options;

    if (finished && editing) {
      if (points.length > 3) {
        onChange("remove", { pos: i, figure });
      }
      skipNextClick();
      return false;
    }
  }
}

function midPoint(p1, p2) {
  return {
    lat: (p1.lat + p2.lat) / 2,
    lng: (p1.lng + p2.lng) / 2
  };
}

export class PointFigure extends Figure {
  constructor(props) {
    super(props);

    this.onPointClick = this.onPointClick.bind(this);
  }

  leafletComponent() {
    return Polyline;
  }

  calculateGuides() {
    const { figure, options } = this.props;
    const { points } = figure;
    const { newPoint, finished } = options;
    const { draggedPoint } = this.state;

    const guides = [];
    if (draggedPoint) {
      const { point, index } = draggedPoint;
      const { length } = points;
      guides.push(
        [point, points[(index + 1) % length]],
        [point, points[(index - 1 + length) % length]]
      );
    }

    const additionalGuides =
      !finished && points.length > 0
        ? [[points[points.length - 1], newPoint]]
        : [];

    return guides.concat(additionalGuides);
  }

  makeExtraElements() {
    const { figure, options, skipNextClick } = this.props;
    const { id, points } = figure;
    const { editing, finished, calcDistance, onChange } = options;

    const { dragging } = this.state;

    if (!finished || !editing || dragging) {
      return [];
    }

    const midPoints = points
      .map((pos, i) => [pos, points[(i + 1) % points.length], i])
      .filter(([a, b]) => calcDistance(a, b) > 40)
      .map(([a, b, i]) => (
        <CircleMarker
          key={id + "-" + i + "-mid"}
          className="midpoint"
          center={midPoint(a, b)}
          radius={8}
          opacity={0.0}
          fillOpacity={0.0}
          onMousedown={e => {
            onChange("add", { point: midPoint(a, b), pos: i + 1, figure });
            skipNextClick();
          }}
        >
          <CircleMarker
            radius={3}
            color="white"
            fill={true}
            fillOpacity={0.5}
            opacity={0.5}
            center={midPoint(a, b)}
          />
        </CircleMarker>
      ));

    return midPoints;
  }

  onPointMoved(point, index) {
    const {
      figure,
      options: { onChange }
    } = this.props;
    onChange("move", { point, pos: index, figure });
  }

  onPointClick(i) {
    const { figure, options, skipNextClick } = this.props;
    const { points } = figure;
    const { finished, onChange } = options;

    if (!finished && i === points.length - 1) {
      if (points.length >= 2) {
        onChange("end", {});
      }
      skipNextClick();
      return false;
    }
  }

  onPointRightClick(i) {
    const { figure, options, skipNextClick } = this.props;
    const { points } = figure;
    const { finished, editing, onChange } = options;

    if (finished && editing) {
      if (points.length > 3) {
        onChange("remove", { pos: i, figure });
      }
      skipNextClick();
      return false;
    }
  }

  render() {
    // console.log("point do i get here")
    const { figure, options, skipNextClick } = this.props;
    const { id, points } = figure;
    const {
      editing,
      finished,
      sketch,
      color,
      vertexColor,
      interactive,
      onSelect,
      showImageMarkers
    } = options;
    const { draggedPoint } = this.state;

    const renderPoints = this.getRenderPoints(points);
    // console.log("getrender", renderPoints,id)

    const dashArray = editing ? "10" : "1";

    const lineColor = editing && sketch ? color : vertexColor || color;
    const fillColor = editing && sketch ? "rgba(0,0,0,0)" : color;
    const weight = editing && sketch ? 1 : 2;

    const classes = ["vertex"];
    if (editing) classes.push("editing");
    if (finished) classes.push("finished");

    const vcolor = vertexColor || color;
    const smallVertices = renderPoints.map((pos, i) => (
      <Marker
        position={pos}
        icon={iconCamera}
      >
        {showImageMarkers && (
          <Tooltip className={"camera_marker"}  direction="top" permanent={true}>
            {id}
          </Tooltip>
        )}
      </Marker>
    ));
    const vertices = renderPoints.map((pos, i) => (
      <CircleMarker
        className={classes.concat(!i ? ["first"] : []).join(" ")}
        key={id + "-" + i}
        center={pos}
        fillOpacity={0.0}
        opacity={0.0}
        radius={11}
        onClick={() => this.onPointClick(i)}
        onContextmenu={() => this.onPointRightClick(i)}
        draggable={editing}
        onDrag={e => {
          this.setState({
            draggedPoint: { point: e.target._latlng, index: i }
          });
        }}
        onDragstart={() => this.setState({ dragging: true })}
        onDragend={e => {
          this.onPointMoved(e.target.getLatLng(), i);
          this.setState({ dragging: false, draggedPoint: null });
        }}
      >
        <CircleMarker
          color={vcolor}
          fill={true}
          weight={1.5}
          fillColor={vcolor}
          fillRule={"evenodd"}
          fillOpacity={1.0}
          radius={4.5}
          center={
            draggedPoint && draggedPoint.index === i ? draggedPoint.point : pos
          }
        />
        {showImageMarkers && (
          <Tooltip className={"red_tooltip"} direction="top" permanent={true}>
            {i.toString()}
          </Tooltip>
        )}
      </CircleMarker>
    ));

    const guideLines = this.makeGuides();
    const PolyComp = this.leafletComponent();

    return (
      <Fragment key={id}>
        <PolyComp
          positions={renderPoints}
          color={lineColor}
          weight={weight}
          fill={this.hasFill()}
          fillColor={fillColor}
          opacity={sketch ? "0.7" : "1.0"}
          dashArray={dashArray}
          interactive={id !== "trace"} // always set interactive to true, to avoid bugs in leaflet-react
          onClick={() => {
            if (interactive) {
              onSelect();
              skipNextClick();
            }
          }}
        />
        {guideLines}
        {this.makeExtraElements()}
        {!finished || editing ? vertices : smallVertices}
      </Fragment>
    );
  }
}

