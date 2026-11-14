// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { Component } from "react";

import { LoaderDisplay, Map, getEnv, connect } from "common/map";

import AnomalyTrajectory from "./AnomalyTrajectory/AnomalyTrajectory";
import CameraMarkersOverlay from "./CameraMarkersOverlay/CameraMarkersOverlay";
import RoadNetworkOverlay from "./RoadNetworkOverlay/RoadNetworkOverlay";
import IntersectionMarkersOverlay from "./IntersectionMarkersOverlay/IntersectionMarkersOverlay";
import { SpeedLegendsStripComponent } from "../../utils/SpeedLegendsStripComponent";

/**
 * Component wrapped around @react-google-maps/api GoogleMap component.
 *
 * Draws - ITS intersection markers layer, the ITS road network - at the city level;
 * Cameras, road network, anomaly trajectory at the intersection level.
 */
class MyMap extends Component {
  constructor(props) {
    super(props);
    this.resetPlaceConfigs(this.props);
    this.state = {
      zoom: this.placeMapOptions.zoom.default,
    };
  }

  polylineStrokeWeight = () => {
    let weight = 2;

    switch (this.state.zoom) {
      case 12:
        break;
      case 13:
      case 14:
      case 15:
      case 16:
        weight = 3;
        break;
      case 17:
      case 18:
        weight = 4;
        break;
      case 19:
        weight = 5;
        break;
      case 20:
      case 21:
      default:
        weight = 6;
        break;
    }

    return weight;
  };

  resetPlaceConfigs(props) {
    if (props.uiState.viewType === "city-level-live") {
      this.placeMapOptions = props.appConfig.mapOptions.city;
      this.mapCenter = props.metadata?.cityConfig?.geoLocation;
    } else {
      this.placeMapOptions = props.appConfig.mapOptions.intersection;
      this.mapCenter = props.metadata.intersectionsMap
        ?.get(props.uiState.selectedIntersection)
        ?.geoLocation;
    }

    if (this.myMap) {
      this.myMap.setOptions({
        center: this.mapCenter,
        zoom: this.placeMapOptions.zoom.default,
      });
      this.myMap.minZoom = this.placeMapOptions.zoom.min;
      this.myMap.maxZoom = this.placeMapOptions.zoom.max;
    }
  }

  onZoomChanged = () => {
    if (this.myMap) {
      let newZoom = this.myMap.getZoom();
      if (this.state.zoom !== newZoom) {
        this.setState({ zoom: newZoom });
      }
    }
  };

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (
      this.props.uiState.viewType !== nextProps.uiState.viewType &&
      nextProps.uiState.viewType !== "intersection-level-alert"
    ) {
      this.resetPlaceConfigs(nextProps);
    }
  }

  render() {
    let markers = [];
    let anomalyTrajectory;
    const polylineStrokeWeight = this.polylineStrokeWeight();

    if (this.props.uiState.viewType === "intersection-level-live") {
      markers.push(
        <CameraMarkersOverlay
          onClickCamera={this.props.onCameraClick}
          onRightClickCamera={this.props.onCameraRightClick}
          forIntersection={this.props.uiState.selectedIntersection}
          key="camera-markers-overlay"
          currentMapZoom={this.state.zoom}
        />
      );
    } else if (this.props.uiState.viewType === "intersection-level-alert") {
      markers.push(
        <CameraMarkersOverlay
          forIntersection={this.props.uiState.selectedIntersection}
          key="camera-markers-overlay"
          currentMapZoom={this.state.zoom}
        />
      );
      if (this.props.uiState.startAnomalyAnimation === true) {
        anomalyTrajectory = (
          <AnomalyTrajectory
            key={this.props.uiState.anomalyReplaySeq || "anomaly"}
            data={this.props.uiState.selectedAnomaly}
            currentMapZoom={this.state.zoom}
            polylineStrokeWeight={polylineStrokeWeight}
          />
        );
      }
      const geoCoordinates =
        this.props.uiState.selectedAnomaly?.locations?.geoCoordinates;
      if (Array.isArray(geoCoordinates) && geoCoordinates.length > 0) {
        this.mapCenter =
          geoCoordinates[Math.floor(geoCoordinates.length / 2)];
      }
    }

    const showStartingReplayOverlay =
      this.props.uiState.viewType === "intersection-level-alert" &&
      this.props.uiState.selectedAnomaly &&
      this.props.uiState.startAnomalyAnimation !== true;

    return (
      <React.Fragment>
        {SpeedLegendsStripComponent()}
        <div
          style={{
            position: "relative",
            width: "100%",
            height: "100%",
            minHeight: 0,
          }}
        >
          <Map
            apiKey={getEnv("REACT_APP_GOOGLE_MAPS_API_KEY")}
            options={{
              maxZoom: this.placeMapOptions.zoom.max,
              minZoom: this.placeMapOptions.zoom.min,
            }}
            zoom={this.placeMapOptions.zoom.default}
            center={this.mapCenter}
            mapTypeId={this.placeMapOptions.mapType.id}
            onZoomChanged={this.onZoomChanged}
            onLoad={(map) => (this.myMap = map)}
            enableSpiderfier={true}
          >
            {markers}
            {anomalyTrajectory}
            <IntersectionMarkersOverlay
              key={"intersection-markers-overlay"}
              zoom={this.state.zoom}
              intersectionMarkerOnClickCallback={
                this.props.intersectionMarkerOnClickCallback
              }
              onCorridorChangeCallback={this.props.onCorridorChangeCallback}
            />
            <RoadNetworkOverlay
              key="road-network-overlay"
              lineStrokeWeight={polylineStrokeWeight}
            />
          </Map>
          {showStartingReplayOverlay && (
            <div
              style={{
                position: "absolute",
                top: "2.25rem",
                right: 0,
                bottom: 0,
                left: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                backgroundColor: "rgba(221, 221, 221, 0.85)",
                zIndex: 16,
                pointerEvents: "none",
              }}
              data-testid="starting-event-replay-map"
            >
              <LoaderDisplay
                type={LoaderDisplay.TYPE.LOADING}
                size={LoaderDisplay.SIZE.MEDIUM}
                userMessage="Starting event replay"
                backgroundOpacity={0}
              />
            </div>
          )}
        </div>
      </React.Fragment>
    );
  }
}

export default connect((state) => ({
  appConfig: state.appConfig,
  metadata: state.metadata,
  uiState: state.uiState,
}))(MyMap);
