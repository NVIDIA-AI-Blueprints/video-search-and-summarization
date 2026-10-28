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

import SensorsLevelView from "./SensorsLevelView";
import PlaceMarker from "./../components/Map/PlaceMarker/PlaceMarker";
import MapDefaults from "./../utils/MapDefaults";
import UiStateReduxUtil from "./../utils/UiStateReduxUtil";

import PlaceMarkerIcon from "../assets/map-marker-place-default.png";
import { resolveAssetUrl } from "../utils/resolveAssetUrl";
import { joinPlacePath } from "../utils/joinPlacePath";

class RoomsLevelView extends Component {
  constructor(props) {
    super(props);

    // TODO: change to static getter
    // TODO: refactor into one place for similar views
    this.placeMarkerIcon = {
      url: resolveAssetUrl(PlaceMarkerIcon) || PlaceMarkerIcon,
      scaledSize: {
        width: 40,
        height: 40,
      },
    };
  }

  onClickRoom = (room) => {
    this.props.updateUiState({
      selectedRoom: room,
      viewType: "room-level-live",
    });
  };

  onClickForBackToRoomsView = () => {
    this.props.updateUiState({
      viewType: "building-level-live",
    });
  };

  render() {
    let content = (
      <SensorsLevelView
        onClickForBackToRoomsView={this.onClickForBackToRoomsView.bind(this)}
      />
    );

    if (this.props.uiState.viewType === "building-level-live") {
      let placeMarkers = [];
      this.props.metadata.buildingsMap
        .get(this.props.uiState.selectedBuilding)
        .rooms.forEach((roomName) => {
          let config = this.props.metadata.roomsMap.get(roomName);
          placeMarkers.push(
            <PlaceMarker
              key={config.name}
              position={config.geoLocation}
              icon={this.placeMarkerIcon}
              onLeftClick={() => this.onClickRoom(config.name)}
              title={
                "Left Click to select room: " + BeautifyNameString(config.name)
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
              )}
              onClickTitle={this.props.onClickForBackToBuildingsView}
              showTooltip={true}
            />
          </PlaceHeader>
          <Map
            {...MapDefaults}
            options={{
              ...MapDefaults.options,
              maxZoom: this.props.appConfig.mapOptions.building.zoom.max,
              minZoom: this.props.appConfig.mapOptions.building.zoom.min,
              zoom: this.props.appConfig.mapOptions.building.zoom.default,
              center: this.props.metadata.buildingsMap.get(
                this.props.uiState.selectedBuilding
              ).geoLocation,
            }}
            mapTypeId={this.props.appConfig.mapOptions.building.mapType.id}
            enableSpiderfier={true}
          >
            {placeMarkers}
          </Map>
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
)(RoomsLevelView);
