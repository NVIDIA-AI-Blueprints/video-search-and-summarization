// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import PropTypes from "prop-types";
import { Map as LeafMap, ImageOverlay, ZoomControl } from "react-leaflet";
import { CRS } from "leaflet";
import Control from "react-leaflet-control";
import update from "immutability-helper";
import Hotkeys from "react-hot-keys";
import "leaflet.path.drag";
import { Icon } from "semantic-ui-react";

import { PolygonFigure, PolylineFigure, PointFigure } from "../figure/Figure";
import { convertPoint, lighten, colorMapping, cartCalibId, calibId, cartRoiId, tripDirId, tripwireId } from "../../common/utils";
import { withBounds, maxZoom } from "../HOC/CalcBoundsHOC";
import { roiId } from "../../common/utils";

import "leaflet/dist/leaflet.css";

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
    const { onChange, unfinishedFigure, figures, height, width, labels } = this.props;
    // console.log("canvas", height, width, labels)
    const drawing = checkIfDrawing(unfinishedFigure, figures, labels);

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
    const { unfinishedFigure, figures , labels} = this.props;
    const drawing = checkIfDrawing(unfinishedFigure, figures, labels);

    if (this.skipNextClickEvent) {
      // a hack, for whatever reason it is really hard to stop event propagation in leaflet
      this.skipNextClickEvent = false;
      return;
    }
    // console.log("canvas12 click", drawing, e.latlng, labels)

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
    // console.log("canvas", figure)
    let Comp = "" 
    switch(figure.type){
      case "polyline":
         Comp = PolylineFigure;
         return (
          <Comp
            key={figure.id}
            figure={figure}
            options={options}
            skipNextClick={() => (this.skipNextClickEvent = true)}
          />
        );
         
      case "polygon":
         Comp = PolygonFigure;
         return (
          <Comp
            key={figure.id}
            figure={figure}
            options={options}
            skipNextClick={() => (this.skipNextClickEvent = true)}
          />
        );
      
      case "point":
        Comp = PointFigure;
        return (
          <Comp
            key={figure.id}
            figure={figure}
            options={options}
            skipNextClick={() => (this.skipNextClickEvent = true)}
          />
        );
      
      default:
        Comp = PolylineFigure;
        return (
          <Comp
            key={figure.id}
            figure={figure}
            options={options}
            skipNextClick={() => (this.skipNextClickEvent = true)}
          />
        );
    } 


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
      showImageMarkers,
      labels
    } = this.props;
    const { zoom, selectedFigureId, cursorPos } = this.state;

    const drawing = checkIfDrawing(unfinishedFigure, figures, labels);

    const calcDistance = (p1, p2) => {
      const map = this.mapRef.current.leafletElement;
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
        interactive: !drawing,
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
              if (type === "point" && points.length >= 0) {
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
          onClick={this.handleClick}
          onZoom={e => this.setState({ zoom: e.target.getZoom() })}
          onMousemove={e => this.setState({ cursorPos: e.latlng })}
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
  hasHomography: PropTypes.bool,
    /** Possible drawing labels */
    labels: PropTypes.array,
};

/**
 * Check if there is currently something being drawn in the image.
 * @param {object} unfinishedFigure
 * @param {array} figures
 */
function checkIfDrawing(unfinishedFigure, figures, labels) {
  // console.log("check if drawing", labels, unfinishedFigure)
  if (!unfinishedFigure) {
    // console.log("check 1 exit")
    return !!unfinishedFigure;
  }
  // Don't allow drawing ROI on image or tripwire id and tripdir id in gIS calibration
  // Allow only one polygon on image
  const limited = labels.filter(label => label.limit === true)
  // console.log ("check limited", unfinishedFigure.id, limited, labels, figures)
  // console.log ("check limited", unfinishedFigure.id, (unfinishedFigure.id in labels))

  const drawId = labels.filter(label => label.id === unfinishedFigure.id)
  const calib = figures.filter(item => item.class === unfinishedFigure.id);
  // console.log ( "check draw id exists", drawId)
  if(!drawId.length ){
    // console.log("check 2 exit")

    return false
  }
  // console.log("check draw status", (drawId[0].limit === true &&
  //   !!(figures.filter(item => item.class === drawId[0].class).length)) , drawId[0], figures, !!figures.filter(item => item.class === drawId[0].class))
  // console.log( "check here",figures)
  // console.log("check here", calib.length, !!(limited.filter(label => label.id === drawId[0].id)).length, (figures.filter(item => item.class === drawId[0].class)))

// check if canvas should draw
  if ((drawId[0].draw === false )) {
    // console.log("check, drawing")
    // console.log("check 3 exit")

    return false;
  }
  else if (
    !!(limited.filter(label => label.id === drawId[0].id)).length && !!calib.length) {//checks if limited ) {
          // (figures.filter(item => item.class === drawId[0].id).length)){
    // if draw applicable, but already have 1
    // console.log("check draw limit check")
    // console.log("check 4 exit")


    return false
  }
  return !!unfinishedFigure

  // const cartCalib = figures.filter(item => item.class === cartCalibId);
  // const cartCalibCount = cartCalib.length;
  // const calib = figures.filter(item => item.class === calibId);
  // const calibCount = calib.length;

  // if (unfinishedFigure.id === roiId || unfinishedFigure.id === tripwireId || unfinishedFigure.id === tripDirId ) {
  //   return false;
  // }
  // else if (unfinishedFigure.id === cartCalibId && !!cartCalib.length) {
  //   return false;
  // }
  // else if ( unfinishedFigure.id === calibId && !!calib.length ){
  //   return false
  // }
  // return !!unfinishedFigure;
}
