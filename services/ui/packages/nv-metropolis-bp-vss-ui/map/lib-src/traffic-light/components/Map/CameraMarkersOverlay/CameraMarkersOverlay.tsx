// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { PureComponent } from "react";
import { Marker } from "@react-google-maps/api";
import {
  CAMERA_ICON_SVG_PATH_DIRECTIONAL,
  CAMERA_ICON_SVG_PATH_NON_DIRECTIONAL,
  connect,
} from "common/warehouse";

const defaultIconColor = "#1E90FF";

/**
 * Marker layer on the map with camera icons with basic shape of a camera along with
 * its orientation w.r.t. the real world and a surrounding SVG based arc representing
 * its field of view in the real world.
 */
class CameraMarkersOverlay extends PureComponent {
  generateCameraFieldOfViewSVGIcon(camera) {
    const metersPerPixelAtZeroZoom = 156543.033928;
    const metersPerPixelAtCurrentZoom =
      metersPerPixelAtZeroZoom / Math.pow(2, this.props.currentMapZoom);
    const fieldOfViewRadiusInPixelsAtCurrentZoom =
      (camera.depth * 0.3048) / metersPerPixelAtCurrentZoom;
    const arcStartAngleRadians =
      ((camera.direction - camera.fieldOfView / 2) * Math.PI) / 180;
    const arcEndAngleRadians =
      ((camera.direction + camera.fieldOfView / 2) * Math.PI) / 180;
    const fieldOfViewSVGPathArray = [
      "M",
      fieldOfViewRadiusInPixelsAtCurrentZoom,
      fieldOfViewRadiusInPixelsAtCurrentZoom,
      "L",
      fieldOfViewRadiusInPixelsAtCurrentZoom *
        (1 + Math.cos(arcStartAngleRadians)),
      fieldOfViewRadiusInPixelsAtCurrentZoom *
        (1 + Math.sin(arcStartAngleRadians)),
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
      "Z",
    ];

    return {
      path: fieldOfViewSVGPathArray.join(" "),
      anchor: new window.google.maps.Point(
        fieldOfViewRadiusInPixelsAtCurrentZoom,
        fieldOfViewRadiusInPixelsAtCurrentZoom
      ),
      fillOpacity: 0.05,
      fillColor: defaultIconColor,
      strokeColor: defaultIconColor,
      strokeWeight: 0.5,
      rotation: 270,
    };
  }

  render() {
    let markers = [];

    this.props.metadata.intersectionsMap.get(this.props.forIntersection) &&
      this.props.metadata.intersectionsMap
        .get(this.props.forIntersection)
        .sensorIds.forEach((sensorId) => {
          const camera = this.props.metadata.sensorsMap.get(sensorId);
          let cameraIcon = {
            path: CAMERA_ICON_SVG_PATH_NON_DIRECTIONAL,
            anchor: new window.google.maps.Point(50, 50),
            fillColor: defaultIconColor,
            strokeColor: defaultIconColor,
            strokeWeight: 2,
            scale: 0.175,
          };

          if (camera.direction) {
            cameraIcon = {
              ...cameraIcon,
              ...{
                rotation: camera.direction,
                path: CAMERA_ICON_SVG_PATH_DIRECTIONAL,
              },
            };
          }

          if (camera.id === this.props.uiState.selectedSensorId) {
            cameraIcon.fillOpacity = 0.8;

            camera.direction &&
              camera.fieldOfView &&
              camera.depth &&
              markers.push(
                <Marker
                  key={camera.id + "FOV"}
                  position={{
                    lat: camera.geoLocation.lat,
                    lng: camera.geoLocation.lng,
                  }}
                  icon={this.generateCameraFieldOfViewSVGIcon(camera)}
                  clickable={false}
                />
              );
          }

          let markerIconTitle = camera.id;
          if (this.props.onRightClickCamera) {
            markerIconTitle += "\nRight Click for Clustering Behavior View";
          }

          markers.push(
            <Marker
              key={camera.id}
              title={markerIconTitle}
              position={{
                lat: camera.geoLocation.lat,
                lng: camera.geoLocation.lng,
              }}
              icon={cameraIcon}
              onClick={() => {
                if (!this.props.enableSpiderfier && this.props.onClickCamera) {
                  this.props.onClickCamera(camera.id);
                }
              }}
              onRightClick={(e) => {
                if (e && e.domEvent) {
                  e.domEvent.preventDefault();
                  e.domEvent.stopPropagation();
                }
                if (!this.props.enableSpiderfier && this.props.onRightClickCamera) {
                  this.props.onRightClickCamera(camera.id);
                }
              }}
              onLoad={(marker) => {
                if (this.props.onLoad) {
                  this.props.onLoad(marker, {
                    leftClick: this.props.onClickCamera
                      ? () => this.props.onClickCamera(camera.id)
                      : undefined,
                    rightClick: this.props.onRightClickCamera
                      ? () => this.props.onRightClickCamera(camera.id)
                      : undefined,
                  });
                }
              }}
            />
          );
        });

    return markers;
  }
}

export default connect((state) => ({
  metadata: state.metadata,
  uiState: state.uiState,
}))(CameraMarkersOverlay);
