// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { OverlayView, Polyline, Marker } from "@react-google-maps/api";

import {
  animationStepMs,
  trajectoryLatLngs,
} from "../../../utils/anomalyReplay";

import CssClasses from "./AnomalyTrajectory.module.css";

/**
 * Line on map with a circle depicting vehicle with its captured trajectory and object id.
 * The line color remains #005DB5.
 */
class AnomalyTrajectory extends React.PureComponent {
  state = {
    animationIconGeoCoordinate: undefined,
  };

  animateAnomaly = (anomalyData) => {
    this.stopPreviousAnimation();

    const path = trajectoryLatLngs(anomalyData?.locations);
    if (path.length === 0) {
      this.setState({
        animationIconGeoCoordinate: undefined,
        lastAnimatedCoordinateIndex: undefined,
      });
      return;
    }

    this.animationTimeoutIds = [];
    const perStepTimeoutMs = animationStepMs(anomalyData, path.length);
    path.forEach((coordinate, index) => {
      this.animationTimeoutIds.push(
        setTimeout(() => {
          this.setState({
            animationIconGeoCoordinate: coordinate,
            lastAnimatedCoordinateIndex: index,
          });
        }, index * perStepTimeoutMs),
      );
    });
  };

  stopPreviousAnimation = () => {
    this.animationTimeoutIds &&
      this.animationTimeoutIds.forEach((timerId) => {
        clearTimeout(timerId);
      });
  };

  componentDidMount() {
    this.animateAnomaly(this.props.data);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (this.props.data === nextProps.data) return;
    this.animateAnomaly(nextProps.data);
  }

  render() {
    const path = trajectoryLatLngs(this.props.data?.locations);
    if (path.length === 0) {
      return null;
    }

    let animationIconColor = "#005DB5";
    if (this.state.lastAnimatedCoordinateIndex === path.length - 1) {
      animationIconColor = "#6D6B6A";
    }
    let objectId = "";
    if (this.props.data.objectId) {
      objectId = this.props.data.objectId;
    } else if (this.props.data.info && this.props.data.info.primaryObjectId) {
      objectId = this.props.data.info.primaryObjectId;
    } else {
      objectId = this.props.data.object?.id || "";
    }

    const mapsApi = window.google && window.google.maps;
    const markerPosition =
      this.state.animationIconGeoCoordinate || path[0];
    // SymbolPath.CIRCLE is 0 — must not be used as a boolean gate.
    const circlePath = mapsApi && mapsApi.SymbolPath.CIRCLE;
    const scale = Math.max(4, this.props.currentMapZoom / 2);

    return (
      <React.Fragment>
        <Polyline
          path={path}
          geodesic={true}
          options={{
            strokeColor: "#005DB5",
            strokeWeight: this.props.polylineStrokeWeight,
            zIndex: 100,
            clickable: false,
          }}
        />
        {mapsApi && markerPosition && (
          <>
            {objectId ? (
              <OverlayView
                mapPaneName={OverlayView.OVERLAY_MOUSE_TARGET}
                position={markerPosition}
              >
                <div className={CssClasses.label}>{String(objectId)}</div>
              </OverlayView>
            ) : null}
            <Marker
              position={markerPosition}
              zIndex={101}
              icon={{
                path: circlePath,
                strokeWeight: this.props.polylineStrokeWeight * 4,
                fillColor: animationIconColor,
                strokeColor: animationIconColor,
                scale,
              }}
            />
          </>
        )}
      </React.Fragment>
    );
  }

  componentWillUnmount() {
    this.stopPreviousAnimation();
  }
}

export default AnomalyTrajectory;
