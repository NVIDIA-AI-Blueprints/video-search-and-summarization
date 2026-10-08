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
  Polyline,
  Marker
} from "@react-google-maps/api";
import { Button } from "semantic-ui-react";
import DrawingMap, {
  libraries,
  LoadScriptOnlyIfNeeded
} from "../map_drawing/DrawingMap";
import { colorMapping } from "../common/utils";

import "./LinksStyles.css";

/**
 * Map used for drawing road links for generating the road network from map
 * matching.
 */
export default class LinksMap extends DrawingMap {
  render() {
    const {
      apiKey,
      center,
      shapes,
      drawingMode,
      selected,
      editable,
      mapZoom,
      toggles,
      showMapMarkers
    } = this.props;

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
            onPolylineComplete={polyline =>
              this.handleShapeComplete(polyline, selected)
            }
          />
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
        </GoogleMap>
        <Button floated="left" color="purple" onClick={this.toggleModal}>
          Change Center
        </Button>

        {this.renderCoordModal()}
      </LoadScriptOnlyIfNeeded>
    );
  }
}
