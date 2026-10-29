// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { Component } from "react";
import { connect } from "react-redux";

import { BeautifyNameString, Map } from "common/warehouse";
import {
  PlaceHeader,
  PlaceHeaderContent,
  ReduxUtils,
} from "common/warehouse";

import SensorLevelView from "./SensorLevelView";
import PlaceMarker from "./../components/Map/PlaceMarker/PlaceMarker";
import PlaceContentSettings from "./../components/PlaceContentSettings/PlaceContentSettings";
import RoomKPIDisplayer from "./../component-controllers/RoomKPIDisplayer";
import MapDefaults from "./../utils/MapDefaults";
import UiStateReduxUtil from "./../utils/UiStateReduxUtil";

import SensorMarkerIcon from "../assets/camera-no-orientation.png";
import { resolveAssetUrl } from "../utils/resolveAssetUrl";
import { joinPlacePath } from "../utils/joinPlacePath";

class SensorsLevelView extends Component {
  constructor(props) {
    super(props);

    // TODO: change to static getter
    this.sensorMarkerIcon = {
      url: resolveAssetUrl(SensorMarkerIcon) || SensorMarkerIcon,
      scaledSize: {
        width: 40,
        height: 40,
      },
    };
  }

  SPIDERFIER_OPTIONS = {
    nearbyDistance: 30,
  };

  onClickSensor = (sensorId) => {
    this.props.updateUiState({
      selectedSensorId: sensorId,
      viewType: "sensor-level-live",
    });
  };

  onRightClickSensor = (sensorId) => {
    this.props.updateUiState({
      selectedSensorId: sensorId,
      viewType: "sensor-level-clustering",
    });
  };

  onClickForBackToSensorsView = () => {
    this.props.updateUiState({
      viewType: "room-level-live",
    });
  };

  render() {
    if (!this.props.metadata?.roomsMap) {
      return null;
    }

    let content = (
      <SensorLevelView
        onClickForBackToSensorsView={this.onClickForBackToSensorsView.bind(
          this
        )}
      />
    );

    if (this.props.uiState.viewType === "room-level-live") {
      let sensorMarkers = [];

      this.props.metadata.roomsMap
        .get(this.props.uiState.selectedRoom)
        .sensorIds.forEach((id) => {
          let config = this.props.metadata.sensorsMap.get(id);
          sensorMarkers.push(
            <PlaceMarker
              key={config.id}
              position={config.geoLocation}
              icon={this.sensorMarkerIcon}
              onLeftClick={() => this.onClickSensor(config.id)}
              onRightClick={() => this.onRightClickSensor(config.id)}
              title={
                BeautifyNameString(config.id) +
                "\nLeft click for live view" +
                "\nRight click for clustering view"
              }
            />
          );
        });

      content = (
        <React.Fragment>
          <PlaceHeader>
            <PlaceHeaderContent
              uiDelaySeconds={this.props.appConfig.uiDelaySeconds}
              placeName={joinPlacePath(
                BeautifyNameString(this.props.metadata.cityConfig.name),
                BeautifyNameString(this.props.uiState.selectedBuilding),
                BeautifyNameString(this.props.uiState.selectedRoom),
              )}
              showTooltip={true}
              onClickTitle={this.props.onClickForBackToRoomsView}
            />
          </PlaceHeader>
          <Map
            {...MapDefaults}
            options={{
              ...MapDefaults.options,
              maxZoom: this.props.appConfig.mapOptions.room.zoom.max,
              minZoom: this.props.appConfig.mapOptions.room.zoom.min,
              zoom: this.props.appConfig.mapOptions.room.zoom.default,
              center: this.props.metadata.roomsMap.get(
                this.props.uiState.selectedRoom
              ).geoLocation,
            }}
            mapTypeId={this.props.appConfig.mapOptions.room.mapType.id}
            spiderfierOptions={this.SPIDERFIER_OPTIONS}
            enableSpiderfier={true}
          >
            {sensorMarkers}
          </Map>
          <RoomKPIDisplayer />
          <PlaceContentSettings
            config={{
              place:
                (this.props.metadata.cityConfig.name
                  ? "city=" + this.props.metadata.cityConfig.name + "/"
                  : "") +
                "building=" +
                this.props.uiState.selectedBuilding +
                "/room=" +
                this.props.uiState.selectedRoom,
            }}
          />
        </React.Fragment>
      );
    }

    return content;
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    metadata: state.metadata,
    uiState: state.uiState,
  }),
  ReduxUtils.combineDispatchToProps(UiStateReduxUtil.slice.actions)
)(SensorsLevelView);
