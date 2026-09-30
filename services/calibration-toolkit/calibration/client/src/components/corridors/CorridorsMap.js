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


import React from "react";
import {
  GoogleMap,
  Marker,
  InfoWindow,
  DrawingManager,
  Polyline
} from "@react-google-maps/api";
import { Button } from "semantic-ui-react";
import DrawingMap, {
  libraries,
  LoadScriptOnlyIfNeeded
} from "../map_drawing/DrawingMap";
import { directions, colorMapping } from "../common/utils";

import "./CorridorsStyles.css";
import DOMPurify from "dompurify";

const defaultIconColor = "#1E90FF";
const google = window.google;


/**
 * Map for drawing cooridor geometry and for viewing sensors within the corridor
 */
export default class CorridorsMap extends DrawingMap {
  constructor(props) {
    super(props);

    this.defaultSensorIcon = {
      // SVG path for top view video sensor icon
      path: "M 25 50 L 0 50 L 0 200 L 100 200 L 100 50 L 75 50 L 100 0 L 0 0 Z",
      // Above SVG path fits in 100 x 200. Center of SVG is at 50, 100
      // anchor: new google.maps.Point(50, 100),
      fillColor: defaultIconColor,
      strokeColor: defaultIconColor,
      strokeWeight: 2,
      // fillOpacity: 0.8,
      scale: 0.125
    };
  }

  generateSensorFieldOfViewSVGIcon(sensor) {
    // Arc SVG to show field of view for the current sensor

    // Per pixel distance in screen pixels at 0 zoom level, given at
    // 0 zoom level Google Maps draws world in {0-256}, {0-256} px
    // and radius of earth used by Google Maps v3 is 6378137 meters.
    // https://developers.google.com/maps/documentation/javascript/reference/geometry#spherical
    // https://developers.google.com/maps/documentation/javascript/coordinates
    //
    // 256 pixels = 2 * pi * 6378137 meters
    const metersPerPixelAtZeroZoom = 156543.033928;
    // Pixel resolution changes as per higher zoom following the equation:
    // pixelCoordinate = worldCoordinate * 2zoomLevel
    // https://developers.google.com/maps/documentation/javascript/coordinates
    const metersPerPixelAtCurrentZoom =
      metersPerPixelAtZeroZoom / Math.pow(2, this.props.currentZoom);
    // Radius of current sensor's field of view circular arc in pixels at current
    // zoom level. Sensor depth value is provided in feet. Using
    // 1 Foot = .3048 Meters for conversion.
    const fieldOfViewRadiusInPixelsAtCurrentZoom =
      (sensor.depth * 0.3048) / metersPerPixelAtCurrentZoom;
    // SVG path to draw field of view
    // Assume the center of SVG arc is at (radius, radius)
    // Coordinates of Arc's Start Point =
    // {radius * (1 + cos(startAngle)), radius * (1 + sin(startAngle))}
    // Coordinates of Arc's End Point =
    // {radius * (1 + cos(endAngle)), radius * (1 + sin(endAngle))}
    const arcStartAngleRadians =
      ((sensor.direction - sensor.fieldOfView / 2) * Math.PI) / 180;
    const arcEndAngleRadians =
      ((sensor.direction + sensor.fieldOfView / 2) * Math.PI) / 180;
    const fieldOfViewSVGPathArray = [
      // MoveTo Center of the Circular arc
      "M",
      fieldOfViewRadiusInPixelsAtCurrentZoom,
      fieldOfViewRadiusInPixelsAtCurrentZoom,
      // MoveTo start of the arc
      "L",
      fieldOfViewRadiusInPixelsAtCurrentZoom *
        (1 + Math.cos(arcStartAngleRadians)),
      fieldOfViewRadiusInPixelsAtCurrentZoom *
        (1 + Math.sin(arcStartAngleRadians)),
      // Draw Circular Arc with x-radius, y-radius = fieldOfViewRadiusInPixelsAtCurrentZoom to
      // the end point of the arc.
      "A",
      fieldOfViewRadiusInPixelsAtCurrentZoom,
      fieldOfViewRadiusInPixelsAtCurrentZoom,
      0,
      0,
      1,
      fieldOfViewRadiusInPixelsAtCurrentZoom *
        (1 + Math.cos(arcEndAngleRadians)),
      fieldOfViewRadiusInPixelsAtCurrentZoom *
        (1 + Math.sin(arcEndAngleRadians)),
      "Z"
    ];

    return {
      // SVG path for top view video sensor defaultIcon
      path: fieldOfViewSVGPathArray.join(" "),
      // Above SVG path fits in arcRadius x arcRadius size.
      // Center of SVG is at (arcRadius, arcRadius)
      anchor: new google.maps.Point(
        fieldOfViewRadiusInPixelsAtCurrentZoom,
        fieldOfViewRadiusInPixelsAtCurrentZoom
      ),
      fillOpacity: 0.05,
      fillColor: defaultIconColor,
      strokeColor: defaultIconColor,
      strokeWeight: 0.5,
      // Sensor's coordinate system and SVG element's coordinate system are rotated
      // w.r.t. each other. To compensate that rotate the SVG before drawing.
      rotation: 270
    };
  }

  render() {
    const {
      apiKey,
      center,
      mapZoom,
      drawingMode,
      selected,
      shapes,
      editable,
      toggles,
      showMapMarkers,
      sensors
    } = this.props;


    let markers = "";
    let cleanSensors = JSON.parse(DOMPurify.sanitize(JSON.stringify(sensors)));
    // let currentFieldOfViewMarker = '';
    if (cleanSensors.length !== 0) {
      markers = cleanSensors.map(sensor => {
        let targetSensorIcon = Object.assign(
          { rotation: directions[sensor.cardinalDirection] },
          this.defaultSensorIcon
        );

        if (sensor === this.state.selectedSensor) {
          // Fill color for the selected sensor
          targetSensorIcon.fillOpacity = 0.8;

          // // Draw behind Sensor Icons layer to allow them to be clickable when
          // // overlapping with current field of view area.
          // currentFieldOfViewMarker = (
          //   <Marker
          //     key={sensor.id + 'FOV'}
          //     position={{ lat: sensor.originLat, lng: sensor.originLng }}
          //     icon={this.generateSensorFieldOfViewSVGIcon(sensor)}
          //     // Set false to allow elements to be clickable in Map Panes beneath
          //     // 'markerLayer' Map Pane and the other markers in the same layer.
          //     // https://developers.google.com/maps/documentation/javascript/reference/overlay-view#MapPanes
          //     clickable={false}
          //   />
          // );
        }

        return (
          <Marker
            key={sensor.id}
            title={sensor.id + ""}
            position={{ lat: sensor.originLat, lng: sensor.originLng }}
            icon={targetSensorIcon}
            onClick={() => this.setState({ selectedSensor: sensor })}
          />
        );
      });
    }
    // markers.push(currentFieldOfViewMarker);

    const { selectedSensor } = this.state;
    const options = {
      drawingControl: false,
      drawingControlOptions: {
        drawingModes: ["polyline"]
      },
      polylineOptions: {
        fillColor: colorMapping.red,
        strokeColor: colorMapping.red,
        fillOpacity: 0.5,
        strokeWeight: 2,
        clickable: true,
        editable: true,
        draggable: true,
        zIndex: 1
      }
    };

    return (
      <LoadScriptOnlyIfNeeded
        id={"script-loader"}
        googleMapsApiKey={apiKey}
        libraries={libraries}
        language={"en"}
        region={"us"}
      >
        <GoogleMap
          mapContainerClassName={"map"}
          center={center}
          zoom={mapZoom}
          ref={"map"}
          options={{ tilt: 0, mapTypeId: this.state.mapTypeId, minZoom: 11 }}
          onZoomChanged={this._handleViewChanged}
          onCenterChanged={this._handleViewChanged}
          onMapTypeIdChanged={this.saveMapTypeId}
          version={"weekly"}
          mapTypeId={"hybrid"}
        >
          <DrawingManager
            drawingMode={drawingMode}
            options={options}
            onPolylineComplete={polyline =>
              this.handleShapeComplete(polyline, selected)
            }
          />
          {markers}
          {Object.values(shapes).map(categoryShapes => {
            if (categoryShapes.length > 0) {
              const renderedShape = categoryShapes.map(shape => {
                if (toggles[shape.category]) {
                  return (
                    <Polyline
                      key={shape.id}
                      id={shape.id}
                      options={{
                        fillColor: colorMapping.red,
                        strokeColor: colorMapping.red,
                        fillOpacity: 0.5,
                        strokeWeight: 2,
                        strokeOpacity: 0.8,
                        suppressUndo: true
                      }}
                      // Make the Polygon editable / draggable
                      editable={editable}
                      draggable={false}
                      path={shape.points}
                      // Event used when manipulating and adding points
                      onMouseUp={point => this.handleShapeEdit(point, shape)}
                      // Event used when right clicking vertex
                      onRightClick={point =>
                        this.handleDeletePoint(point, shape)
                      }
                    />
                  );
                } else return null;
              });
              return renderedShape;
            } else return null;
          })}
          {showMapMarkers &&
            Object.values(shapes).map(categoryShapes => {
              if (categoryShapes.length > 0) {
                const renderedMarkers = categoryShapes.map(shape => {
                  if (toggles[shape.category]) {
                    const renderedMarker = shape.points.map((point, key) => {
                      return (
                        <Marker
                          key={key}
                          label={"" + key}
                          position={point}
                          title={"[" + point.lat + "," + point.lng + "]"}
                          editable
                        />
                      );
                    });
                    return renderedMarker;
                  } else return null;
                });
                return renderedMarkers;
              } else return null;
            })}
          {selectedSensor && (
            <InfoWindow
              onCloseClick={() => this.setState({ selectedSensor: null })}
              position={{
                lat: selectedSensor.originLat,
                lng: selectedSensor.originLng
              }}
            >
              <div>
                <h3>{`Sensor: ${selectedSensor.sensorId}`}</h3>
                {/* <p>{`${selectedSensor.deviceId}`}</p> */}
              </div>
            </InfoWindow>
          )}
        </GoogleMap>
        <Button floated="left" color="purple" onClick={this.toggleModal}>
          Change Center
        </Button>
        {this.renderCoordModal()}
      </LoadScriptOnlyIfNeeded>
    );
  }
}
