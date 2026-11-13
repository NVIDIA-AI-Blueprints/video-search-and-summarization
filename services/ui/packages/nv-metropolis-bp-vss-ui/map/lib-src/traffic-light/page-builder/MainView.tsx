// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { Component } from "react";

import { BeautifyNameString } from "common/warehouse";

import {
  PlaceHeader,
  PlaceHeaderContent,
  PlaceHeaderLiveControl,
  ReduxUtils,
  connect,
} from "common/warehouse";

import IntersectionView from "./IntersectionView/IntersectionView";
import Map from "../components/Map/Map";
import KPIsDisplayElement from "../components/KPIsDisplayElement/KPIsDisplayElement";
import KpiPlaceTitle from "../components/KpiPlaceTitle/KpiPlaceTitle";
import TrajectoryClusterView from "../components/TrajectoryClusterView";
import UiStateReduxUtil from "../utils/UiStateReduxUtil";

class MainView extends Component {
  cameraClickHandler = (cameraId) => {
    this.props.updateUiState({
      selectedSensorId: cameraId,
    });
  };

  cameraRightClickHandler = (cameraId) => {
    this.props.updateUiState({
      selectedSensorId: cameraId,
      viewType: "sensor-level-clustering",
    });
  };

  intersectionMarkerOnClickCallback = (intersectionName) => {
    this.props.updateUiState({
      selectedIntersection: intersectionName,
      selectedSensorId:
        this.props.metadata.intersectionsMap.get(intersectionName).sensorIds[0],
      viewType: "intersection-level-live",
    });
  };

  intersectionBackBtnHandler = () => {
    this.props.updateUiState({
      viewType: "city-level-live",
      startAnomalyAnimation: false,
      selectedAnomaly: undefined,
    });
  };

  sensorClusteringViewBackBtnHandler = () => {
    this.props.updateUiState({
      viewType: "intersection-level-live",
    });
  };

  onCorridorChangeCallback = (newCorridorName) => {
    this.props.updateUiState({
      selectedCorridor: newCorridorName,
    });
  };

  onClickButtonBackToLive = () => {
    this.props.updateUiState({
      viewType: "intersection-level-live",
      startAnomalyAnimation: false,
      selectedAnomaly: undefined,
    });
  };

  liveModeHeaderControl() {
    if (this.props.uiState.viewType === "intersection-level-alert") {
      return (
        <PlaceHeaderLiveControl
          isLive={false}
          onGoBackLive={this.onClickButtonBackToLive}
        />
      );
    }
    if (
      this.props.uiState.viewType === "intersection-level-live" ||
      this.props.uiState.viewType === "city-level-live"
    ) {
      return <PlaceHeaderLiveControl isLive={true} />;
    }
    return null;
  }

  render() {
    const cityName = this.props.metadata?.cityConfig?.name;
    if (!cityName) {
      return null;
    }

    let kpiPanel;
    let kpiPlaceTitle;
    let placeHeaderContent;
    let intersectionViewOverlay;

    if (this.props.uiState.viewType === "city-level-live") {
      if (this.props.uiState.selectedCorridor) {
        kpiPanel = (
          <KPIsDisplayElement
            place={
              "city=" +
              cityName +
              "/corridor=" +
              this.props.uiState.selectedCorridor
            }
          />
        );

        kpiPlaceTitle = (
          <KpiPlaceTitle
            title={BeautifyNameString(this.props.uiState.selectedCorridor)}
          />
        );
      }
      placeHeaderContent = (
        <PlaceHeaderContent
          uiDelaySeconds={this.props.appConfig.uiDelaySeconds}
          placeName={BeautifyNameString(cityName)}
        >
          {this.liveModeHeaderControl()}
        </PlaceHeaderContent>
      );
    } else if (this.props.uiState.viewType === "sensor-level-clustering") {
      placeHeaderContent = (
        <PlaceHeaderContent
          uiDelaySeconds={this.props.appConfig.uiDelaySeconds}
          placeName={BeautifyNameString(
            cityName +
              " > " +
              this.props.uiState.selectedIntersection +
              " > " +
              this.props.uiState.selectedSensorId
          )}
          showTooltip={true}
          onClickTitle={this.sensorClusteringViewBackBtnHandler.bind(this)}
        />
      );
    } else {
      intersectionViewOverlay = <IntersectionView />;
      placeHeaderContent = (
        <PlaceHeaderContent
          uiDelaySeconds={this.props.appConfig.uiDelaySeconds}
          placeName={BeautifyNameString(
            cityName +
              " > " +
              this.props.uiState.selectedIntersection
          )}
          showTooltip={true}
          onClickTitle={this.intersectionBackBtnHandler.bind(this)}
        >
          {this.liveModeHeaderControl()}
        </PlaceHeaderContent>
      );
      kpiPlaceTitle = (
        <KpiPlaceTitle
          title={BeautifyNameString(this.props.uiState.selectedIntersection)}
        />
      );

      if (this.props.uiState.viewType === "intersection-level-live") {
        kpiPanel = (
          <KPIsDisplayElement
            place={
              "city=" +
              cityName +
              "/intersection=" +
              this.props.uiState.selectedIntersection
            }
          />
        );
      }
    }

    return (
      <React.Fragment>
        <PlaceHeader>{placeHeaderContent}</PlaceHeader>
        {this.props.uiState.viewType === "sensor-level-clustering" ? (
          <TrajectoryClusterView />
        ) : (
          <div
            style={{
              position: "relative",
              width: "100%",
              height: "100%",
              minHeight: 0,
            }}
          >
            <Map
              onCameraClick={this.cameraClickHandler.bind(this)}
              onCameraRightClick={this.cameraRightClickHandler.bind(this)}
              intersectionMarkerOnClickCallback={
                this.intersectionMarkerOnClickCallback
              }
              onCorridorChangeCallback={this.onCorridorChangeCallback}
            />
            {kpiPanel}
            {kpiPlaceTitle}
            {intersectionViewOverlay}
          </div>
        )}
      </React.Fragment>
    );
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    metadata: state.metadata,
    uiState: state.uiState,
  }),
  ReduxUtils.combineDispatchToProps(UiStateReduxUtil.slice.actions)
)(MainView);
