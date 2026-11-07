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

import RoomsLevelView from "./RoomsLevelView";
import PlaceMarker from "./../components/Map/PlaceMarker/PlaceMarker";
import MapDefaults from "./../utils/MapDefaults";
import UiStateReduxUtil from "./../utils/UiStateReduxUtil";

import PlaceMarkerIcon from "../assets/map-marker-place-default.png";
import { resolveAssetUrl } from "../utils/resolveAssetUrl";

class BuildingsLevelView extends Component {
  constructor(props) {
    super(props);

    // TODO: change to static getter
    this.placeMarkerIcon = {
      url: resolveAssetUrl(PlaceMarkerIcon) || PlaceMarkerIcon,
      scaledSize: {
        width: 40,
        height: 40,
      },
    };
  }

  onClickBuilding = (building) => {
    this.props.updateUiState({
      selectedBuilding: building,
      viewType: "building-level-live",
    });
  };

  onClickForBackToBuildingsView = () => {
    this.props.updateUiState({ viewType: "city-level-live" });
  };

  render() {
    if (!this.props.metadata?.buildingsMap) {
      return null;
    }

    let content = (
      <RoomsLevelView
        onClickForBackToBuildingsView={this.onClickForBackToBuildingsView.bind(
          this
        )}
      />
    );

    if (this.props.uiState.viewType === "city-level-live") {
      let placeMarkers = [];
      this.props.metadata.buildingsMap.forEach((config) => {
        placeMarkers.push(
          <PlaceMarker
            key={config.name}
            position={config.geoLocation}
            icon={this.placeMarkerIcon}
            onLeftClick={() => this.onClickBuilding(config.name)}
            title={
              "Left Click to select building: " +
              BeautifyNameString(config.name)
            }
          />
        );
      });

      content = (
        <React.Fragment>
          <PlaceHeader>
            <PlaceHeaderContent
              uiDelaySeconds={this.props.appConfig.uiDelaySeconds}
              placeName={this.props.metadata.cityConfig.name}
            />
          </PlaceHeader>
          <Map
            {...MapDefaults}
            options={{
              ...MapDefaults.options,
              maxZoom: this.props.appConfig.mapOptions.city.zoom.max,
              minZoom: this.props.appConfig.mapOptions.city.zoom.min,
              zoom: this.props.appConfig.mapOptions.city.zoom.default,
              center: this.props.metadata.cityConfig.geoLocation,
            }}
            mapTypeId={this.props.appConfig.mapOptions.city.mapType.id}
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
)(BuildingsLevelView);
