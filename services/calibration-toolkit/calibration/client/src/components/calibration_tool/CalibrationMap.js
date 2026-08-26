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
  DrawingManager,
  Polygon,
  Marker
} from "@react-google-maps/api";
import { Button } from "semantic-ui-react";
import DrawingMap, {
  libraries,
  LoadScriptOnlyIfNeeded
} from "../map_drawing/DrawingMap";
import { calibId, colorMapping } from "../common/utils";

import "./CalibrationStyles.css";

/**
 * Map with cabilities of drawing calibration and ROI polygons for sensor
 * calibration.
 */
export default class CalibrationMap extends DrawingMap {
  render() {
    const {
      apiKey,
      center,
      shapes,
      drawingMode,
      selected,
      hasHomography,
      mapZoom,
      toggles,
      showMapMarkers
    } = this.props;

    const options = {
      drawingControl: false,
      drawingControlOptions: {
        drawingModes: ["polygon"]
      },
      polygonOptions:
        selected === calibId
          ? {
              fillColor: colorMapping.red,
              strokeColor: colorMapping.red,
              fillOpacity: 0.5,
              strokeWeight: 2,
              clickable: true,
              editable: true,
              draggable: true,
              zIndex: 1
            }
          : {
              fillColor: colorMapping.blue,
              strokeColor: colorMapping.blue,
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
        id="script-loader"
        googleMapsApiKey={apiKey}
        libraries={libraries}
        language="en"
        region="us"
      >
        <GoogleMap
          mapContainerClassName="map"
          center={center}
          ref="map"
          zoom={mapZoom}
          options={{
            tilt: 0,
            mapTypeId: this.state.mapTypeId,
            minZoom: 11,
            fullscreenControl: false
          }}
          onZoomChanged={this._handleViewChanged}
          onCenterChanged={this._handleViewChanged}
          onMapTypeIdChanged={this.saveMapTypeId}
          version="weekly"
          mapTypeId="hybrid"
        >
          <DrawingManager
            drawingMode={drawingMode}
            options={options}
            onPolygonComplete={polygon =>
              this.handleShapeComplete(polygon, selected)
            }
          />
          {Object.values(shapes).map(categoryShapes => {
            if (categoryShapes.length > 0) {
              const renderedShape = categoryShapes.map(shape => {
                if (toggles[shape.category]) {
                  const completedOptions =
                    shape.category === calibId
                      ? {
                          fillColor: colorMapping.red,
                          strokeColor: colorMapping.red,
                          fillOpacity: 0.1,
                          strokeWeight: 2,
                          strokeOpacity: 0.8,
                          suppressUndo: true
                        }
                      : {
                          fillColor: colorMapping.blue,
                          strokeColor: colorMapping.blue,
                          fillOpacity: 0.1,
                          strokeWeight: 2,
                          strokeOpacity: 0.8,
                          suppressUndo: true
                        };
                  return (
                    <Polygon
                      data-testid="CalibrationPolygon"
                      key={shape.id}
                      id={shape.id}
                      options={completedOptions}
                      editable={!hasHomography}
                      draggable={false}
                      path={shape.points}
                      onMouseUp={point => this.handleShapeEdit(point, shape)}
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
            Object.values(shapes)[0].map(categoryShapes => {
              if ([categoryShapes].length > 0) {
                const renderedMarkers = [categoryShapes].map(shape => {
                  if (toggles[shape.category]) {
                    const renderedMarker = shape.points.map((point, key) => {
                      return (
                        <Marker
                          key={key}
                          label={"" + key}
                          position={point}
                          title={"[" + point.lat + "," + point.lng + "]"}
                          editable
                          opacity={0.9}
                          MarkerLabel={{ fontSize: "30px" }}
                        />
                      );
                    });
                    return renderedMarker;
                  } else return null;
                });
                return renderedMarkers;
              } else return null;
            })}
        </GoogleMap>
        <Button floated="left" color="purple" onClick={this.toggleModal}>
          Change Center
        </Button>

        {this.renderCoordModal()}
      </LoadScriptOnlyIfNeeded>
    );
  }
}
