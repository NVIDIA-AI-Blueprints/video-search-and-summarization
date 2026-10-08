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

import {
  DrawingManager, GoogleMap, Marker, Polygon, Polyline
} from "@react-google-maps/api";
import React from "react";
import { Button } from "semantic-ui-react";
import { colorMapping, roiId } from "../common/utils";
import DrawingMap, {
  libraries,
  LoadScriptOnlyIfNeeded
} from "../map_drawing/DrawingMap";
import "./CartesianValidationStyles.css";



/**
 * Map used for rendering the polyline calculated by projecting the validation
 * trajectory drawn in the image into the google map coordinate frame. This map
 * does not allow drawing.
 */
export default class CartesianValidationMap extends DrawingMap {
  render() {
    const {
      apiKey,
      center,
      mapZoom,
      shapes,
      selected,
      drawingMode,
      toggles,
      showMapMarkers
    } = this.props;

    const options = {
      drawingControl: false,
      drawingControlOptions: {
        drawingModes: ["polygon"]
      },
      polygonOptions: {
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
          ref="map"
          center={center}
          zoom={mapZoom}
          options={{ tilt: 0, mapTypeId: this.state.mapTypeId, minZoom: 11 }}
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
              const renderedShape = categoryShapes.map((shape, key) => {
                if (shape.category !== roiId && toggles[shape.category]) {
                  return (
                    <Polyline
                      key={key}
                      id={shape.id}
                      options={{
                        fillColor: colorMapping.red,
                        strokeColor: colorMapping.red,
                        fillOpacity: 0.5,
                        strokeWeight: 2,
                        strokeOpacity: 0.8,
                        suppressUndo: true
                      }}
                      editable={false}
                      draggable={false}
                      path={shape.points}
                    />
                  );
                } else return null;
              });
              return renderedShape;
            } else return null;
          })}
          {Object.values(shapes).map(categoryShapes => {
            if (categoryShapes.length > 0) {
              const renderedShape = categoryShapes.map((shape, key) => {
                if (shape.category === roiId && toggles[shape.category]) {
                  const completedOptions = {
                    fillColor: colorMapping.blue,
                    strokeColor: colorMapping.blue,
                    fillOpacity: 0.5,
                    strokeWeight: 2,
                    strokeOpacity: 0.8,
                    suppressUndo: true
                  };
                  return (
                    <Polygon
                      key={key}
                      id={shape.id}
                      options={completedOptions}
                      editable={true}
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
                        />
                      );
                    });
                    return renderedMarker;
                  } else return null;
                });
                return renderedMarkers;
              } else {
                return null;
              }
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
