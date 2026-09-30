/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */

import update from "immutability-helper";
import { CRS } from "leaflet";
import "leaflet.path.drag";
import "leaflet/dist/leaflet.css";
import PropTypes from "prop-types";
import React from "react";
import Hotkeys from "react-hot-keys";
import { ImageOverlay, Map as LeafMap, ZoomControl } from "react-leaflet";
import Control from "react-leaflet-control";
import { Icon } from "semantic-ui-react";
import { calibId, cartCalibId, colorMapping, convertPoint, lighten, roiId } from "../../common/utils";
import { PolygonFigure, PolylineFigure } from "../figure/Figure";
import { maxZoom, withBounds } from "../HOC/CalcBoundsHOC";



class Canvas extends React.Component {
  constructor(props, context) {
    super(props, context);

    this.state = {
      zoom: -1,
      selectedFigureId: null,
      cursorPos: { lat: 0, lng: 0 }
    };
    this.prevSelectedFigure = null;
    this.skipNextClickEvent = false;

    this.mapRef = React.createRef();
    this.handleChange = this.handleChange.bind(this);
    this.handleClick = this.handleClick.bind(this);
  }

  componentDidUpdate(prevProps) {
    const { onSelectionChange } = this.props;
    const { selectedFigureId } = this.state;

    if (this.prevSelectedFigureId !== selectedFigureId && onSelectionChange) {
      this.prevSelectedFigureId = selectedFigureId;
      onSelectionChange(selectedFigureId);
    }
  }

  getSelectedFigure() {
    const { selectedFigureId } = this.state;
    const { figures } = this.props;
    return figures.find(f => f.id === selectedFigureId);
  }

  handleChange(eventType, { point, pos, figure, points }) {
    const { onChange, unfinishedFigure, figures, height, width } = this.props;
    // console.log("canv", height,width)
    const drawing = checkIfDrawing(unfinishedFigure, figures);

    if (point) {
      if (point.lat < 0) {
        point.lat = 0;
      } else if (point.lat > height) {
        point.lat = height;
      }

      if (point.lng < 0) {
        point.lng = 0;
      } else if (point.lng > width) {
        point.lng = width;
      }
    }

    switch (eventType) {
      case "add":
        if (drawing) {
          let newState = unfinishedFigure.points;
          newState = update(newState, { $push: [point] });

          onChange(
            "unfinished",
            update(unfinishedFigure, {
              points: {
                $set: newState
              }
            })
          );
        } else {
          onChange(
            "replace",
            update(figure, { points: { $splice: [[pos, 0, point]] } })
          );
        }
        break;

      case "end":
        let f = unfinishedFigure;
        f.id = null;
        onChange("new", f);
        break;

      case "move":
        onChange(
          "replace",
          update(figure, { points: { $splice: [[pos, 1, point]] } })
        );
        const tempSelectedFigureId = this.state.selectedFigureId;

        // Hack to refresh selected figure to properly render points
        this.setState({ selectedFigureId: null });
        this.setState({ selectedFigureId: tempSelectedFigureId });
        break;

      case "replace":
        onChange("replace", update(figure, { points: { $set: points } }));
        break;

      case "remove":
        onChange(
          "replace",
          update(figure, { points: { $splice: [[pos, 1]] } })
        );
        break;

      default:
        throw new Error("unknown event type " + eventType);
    }
  }

  handleClick(e) {
    const { unfinishedFigure, figures } = this.props;
    const drawing = checkIfDrawing(unfinishedFigure, figures);

    if (this.skipNextClickEvent) {
      // a hack, for whatever reason it is really hard to stop event propagation in leaflet
      this.skipNextClickEvent = false;
      return;
    }

    if (drawing) {
      this.handleChange("add", { point: convertPoint(e.latlng) });
      return;
    }

    if (!drawing) {
      this.setState({ selectedFigureId: null });
      return;
    }
  }

  renderFigure(figure, options) {
    const Comp = figure.type === "polyline" ? PolylineFigure : PolygonFigure;
    console.log("1",figure)
    return (
      <Comp
        key={figure.id}
        figure={figure}
        options={options}
        skipNextClick={() => (this.skipNextClickEvent = true)}
      />
    );
  }

  render() {
    const {
      imageUrl,
      bounds,
      center,
      figures,
      unfinishedFigure,
      onChange,
      onReassignment,
      style,
      hasHomography,
      showImageMarkers
    } = this.props;
    const { zoom, selectedFigureId, cursorPos } = this.state;
    // console.log("canv", bounds)
    const drawing = checkIfDrawing(unfinishedFigure, figures);

    const calcDistance = (p1, p2) => {
      const map = this.mapRef.current.leafletElement;
      // console.log( "calc", map.latLngToLayerPoint(p1).distanceTo(map.latLngToLayerPoint(p2)))
      return map.latLngToLayerPoint(p1).distanceTo(map.latLngToLayerPoint(p2));
    };

    const unfinishedDrawingDOM = drawing
      ? this.renderFigure(unfinishedFigure, {
          finished: false,
          editing: false,
          interactive: false,
          color: colorMapping[unfinishedFigure.color],
          onChange: this.handleChange,
          calcDistance,
          newPoint: cursorPos
        })
      : null;

    const getColor = f =>
      f.tracingOptions && f.tracingOptions.enabled
        ? lighten(colorMapping[f.color], 80)
        : colorMapping[f.color];
    const figuresDOM = figures.map((f, i) =>
      this.renderFigure(f, {
        hasHomography: hasHomography,
        editing: hasHomography ? false : selectedFigureId === f.id && !drawing,
        finished: true,
        interactive: false,
        sketch: f.tracingOptions && f.tracingOptions.enabled,
        color: getColor(f),
        vertexColor: colorMapping[f.color],
        onSelect: () => {
          this.setState({ selectedFigureId: null });
          this.setState({ selectedFigureId: f.id });
        },
        onChange: this.handleChange,
        calcDistance,
        showImageMarkers: showImageMarkers
      })
    );

    const hotkeysDOM = (
      <Hotkeys
        keyName="backspace,del,c,f,-,=,left,right,up,down"
        onKeyDown={key => {
          const tagName = document.activeElement
            ? document.activeElement.tagName.toLowerCase()
            : null;
          if (["input", "textarea"].includes(tagName)) {
            return false;
          }
          if (drawing) {
            if (key === "f") {
              const { type, points } = unfinishedFigure;
              if (type === "polygon" && points.length >= 3) {
                this.handleChange("end", {});
              }
            }
          } else {
            if (key === "c") {
              if (selectedFigureId && this.getSelectedFigure()) {
                onReassignment(this.getSelectedFigure().type);
              }
            } else if (key === "backspace" || key === "del") {
              if (selectedFigureId && this.getSelectedFigure()) {
                onChange("delete", this.getSelectedFigure());
              }
            }
          }

          const map = this.mapRef.current.leafletElement;
          if (key === "left") {
            map.panBy([80, 0]);
          }
          if (key === "right") {
            map.panBy([-80, 0]);
          }
          if (key === "up") {
            map.panBy([0, 80]);
          }
          if (key === "down") {
            map.panBy([0, -80]);
          }
          if (key === "=") {
            map.setZoom(map.getZoom() + 0.1);
          }
          if (key === "-") {
            map.setZoom(map.getZoom() - 0.1);
          }
        }}
      />
    );

    let renderedTrace = null;
    const selectedFigure = this.getSelectedFigure();
    if (selectedFigure && selectedFigure.type === "polygon") {
      const trace = selectedFigure.tracingOptions
        ? selectedFigure.tracingOptions.trace || []
        : [];
      const figure = {
        id: "trace",
        type: "line",
        points: trace
      };
      const traceOptions = {
        editing: false,
        finished: true,
        color: colorMapping[selectedFigure.color]
      };
      renderedTrace = <PolygonFigure figure={figure} options={traceOptions} />;
    }

    return (
      <div
        style={{
          cursor: drawing ? "crosshair" : "grab",
          height: "100%",
          ...style
        }}
      >
        <LeafMap
          crs={CRS.Simple}
          minZoom={-50}
          maxZoom={maxZoom}
          center={center}
          zoom={zoom}
          zoomAnimation={false}
          zoomSnap={0.1}
          zoomControl={false}
          zoomDelta={0.2}
          wheelPxPerZoomLevel={300}
          keyboard={false}
          attributionControl={false}
        //   onClick={this.handleClick}
          onZoom={e => this.setState({ zoom: e.target.getZoom() })}
        //   onMousemove={e => this.setState({ cursorPos: e.latlng })}
          ref={this.mapRef}
          onContextmenu={() => null}
          doubleClickZoom={false}
        >
          <ZoomControl position="bottomright" />
          <Control className="leaflet-bar" position="bottomright">
            <a
              role="button"
              title="Zoom reset"
              href="# "
              onClick={() => {
                const map = this.mapRef.current.leafletElement;
                map.setView(map.options.center, map.options.zoom);
              }}
            >
              <Icon name="redo" fitted style={{ fontSize: "1.2em" }} />
            </a>
          </Control>
          <ImageOverlay url={imageUrl} bounds={bounds} />
          {unfinishedDrawingDOM}
          {renderedTrace}
          {figuresDOM}
          {hotkeysDOM}
        </LeafMap>
      </div>
    );
  }
}

export default withBounds(Canvas);

Canvas.propTypes = {
  /** Height of the image being used in the canvas */
  height: PropTypes.number,
  /** Width of the image being used in the canvas */
  width: PropTypes.number,
  /** Center of the image being used in the canvas */
  center: PropTypes.arrayOf(PropTypes.number),
  /** URL of the image being used in the canvas */
  imageUrl: PropTypes.string,
  /** Indicator wether or not to show vertex number labels */
  showImageMarkers: PropTypes.bool,
  /** Handle the action of a figure being changed*/
  onChange: PropTypes.func,
  /** Handle changing the selected figure */
  onSelectionChange: PropTypes.func,
  /** Figures that are currently drawn on the canvas */
  figues: PropTypes.array,
  /** Unfinished figure currently being drawn on the canvas */
  unfinishedFigure: PropTypes.object,
  /** Styles */
  style: PropTypes.object,
  /** Indicator wether or not the image has a homography matrix */
  hasHomography: PropTypes.bool
};

/**
 * Check if there is currently something being drawn in the image.
 * @param {object} unfinishedFigure
 * @param {array} figures
 */
function checkIfDrawing(unfinishedFigure, figures) {
  if (!unfinishedFigure) {
    return !!unfinishedFigure;
  }
  // Don't allow drawing ROI on image
  // Allow only one polygon on image


  const cartCalib = figures.filter(item => item.class === cartCalibId);
  const cartCalibCount = cartCalib.length;
  const calib = figures.filter(item => item.class === calibId);
  const calibCount = calib.length;

  if (unfinishedFigure.id === roiId ) {
    return false;
  }
  else if (unfinishedFigure.id === cartCalibId && !!cartCalib.length) {
    return false;
  }
  else if ( unfinishedFigure.id === calibId && !!calib.length ){
    return false
  }
  return !!unfinishedFigure;
}
