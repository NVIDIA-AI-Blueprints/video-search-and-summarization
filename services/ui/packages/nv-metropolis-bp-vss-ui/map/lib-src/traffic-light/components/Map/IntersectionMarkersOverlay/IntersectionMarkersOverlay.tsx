// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { Component } from "react";

import {
  ParseMdxPlaceString,
  UserMessage,
  joinUrl,
  isAbortError,
  readJson,
  connect,
} from "common/warehouse";
import IntersectionMarker from "./IntersectionMarker/IntersectionMarker";
import MarkerMenu from "./MarkerMenu/MarkerMenu";

import {
  AppLogsConsoleReduxUtil,
  AppConstants,
  ReduxUtils,
  AlertsSevereUrl,
  IncidentsSevereUrl,
  MetricsLastProcessedTimestampUrl,
} from "common/warehouse";

/**
 * Intersection Markers Layer. Shows intersections of interest on the Google Maps.
 */
class IntersectionMarkersOverlay extends Component {
  constructor(props) {
    super(props);

    this.onLeftClickMarker = this.onLeftClickMarker.bind(this);
    this.onRightClickMarker = this.onRightClickMarker.bind(this);
  }

  state = {
    displayMarkerMenu: false,
    intersectionsLastUpdatedTimeMap: new Map(),
    highlightedCorridor: null,
  };

  fetchSevereData = (url, place, dataType, fromTimestamp, toTimestamp, newQueryTimeMs) => {
    fetch(joinUrl(url, {
      place: place,
      fromTimestamp: fromTimestamp,
      toTimestamp: toTimestamp,
    }), {
      signal: this.abortControllerForAlertStates?.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        if (dataType === "alerts" && data.severeAlerts && data.severeAlerts.places) {
          data.severeAlerts.places.forEach((loc) => {
            let parsedNames = ParseMdxPlaceString(loc);
            this.intersectionsAlertStateMap.set(parsedNames.intersection, true);
          });
        } else if (dataType === "incidents" && data.severeIncidents && data.severeIncidents.places) {
          data.severeIncidents.places.forEach((loc) => {
            let parsedNames = ParseMdxPlaceString(loc);
            this.intersectionsAlertStateMap.set(parsedNames.intersection, true);
          });
        }

        this.lastIntersectionStatsUpdateTimeMs = newQueryTimeMs;
        this.forceUpdate();
      })
      .catch((err) => {
        this.stopIntersectionsAlertStateUpdate();
        if (isAbortError(err)) return;
        if (!err.config) throw err;
        let msg = new UserMessage(
          UserMessage.SRC_TYPE.HTTP_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          `Query ${dataType === "alerts" ? "Alerts" : "Incidents"} Severe for place: ` +
            "city=" +
            this.props.metadata.cityConfig.name,
          err,
          "Retry",
          this.resetUpdateIntersectionsAlertState.bind(this)
        );

        this.props["addLogTo" + AppConstants.APP_LOGS_CONSOLE_NAME](msg);
        console.error(msg.getLongString());
      });
  }

  updateIntersectionsAlertState() {
    const newQueryTimeMs =
      Date.now() - this.props.appConfig.uiDelaySeconds * 1000;
    const fromTimestamp = new Date(
      this.lastIntersectionStatsUpdateTimeMs
    ).toISOString();
    const toTimestamp = new Date(newQueryTimeMs).toISOString();

    this.fetchSevereData(AlertsSevereUrl, "city=" + this.props.metadata.cityConfig.name, "alerts", fromTimestamp, toTimestamp, newQueryTimeMs);
    this.fetchSevereData(IncidentsSevereUrl, "city=" + this.props.metadata.cityConfig.name, "incidents", fromTimestamp, toTimestamp, newQueryTimeMs);
  }

  updateIntersectionsLastUpdatedTime() {
    fetch(joinUrl(MetricsLastProcessedTimestampUrl, {
      place: "city=" + this.props.metadata.cityConfig.name,
    }), {
      signal: this.abortControllerForLastUpdatedTime?.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        let intersectionsLastUpdatedTimeMap = new Map();
        data.details &&
          data.details.forEach((entry) => {
            let parsedNames = ParseMdxPlaceString(entry.place);
            intersectionsLastUpdatedTimeMap.set(
              parsedNames.intersection,
              new Date(entry.timestamp).getTime()
            );
          });
        this.setState({
          intersectionsLastUpdatedTimeMap: new Map([
            ...this.state.intersectionsLastUpdatedTimeMap,
            ...intersectionsLastUpdatedTimeMap,
          ]),
        });
      })
      .catch((err) => {
        clearInterval(this.intersectionsLastUpdatedTimeUpdateIntervalTimerId);
        if (isAbortError(err)) return;
        if (!err.config) throw err;
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.HTTP_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          "Query Last Update Time of Intersections at place: " +
            "city=" +
            this.props.metadata.cityConfig.name,
          err,
          "Retry",
          this.resetUpdateIntersectionsLastUpdatedTime.bind(this)
        );
        this.props["addLogTo" + AppConstants.APP_LOGS_CONSOLE_NAME](
          userMessage
        );
        console.error(userMessage.getLongString());
      });
  }

  onLeftClickMarker = (intersectionName) => {
    this.intersectionsAlertStateMap.set(intersectionName, false);
    this.props.intersectionMarkerOnClickCallback(intersectionName);
  };

  onRightClickMarker = (e, itemsList) => {
    if (itemsList.length === 0) return;
    if (itemsList.length === 1) {
      this.props.onCorridorChangeCallback(itemsList[0]);
      this.onMarkerMenuClose();
      return;
    }
    this.setState({
      displayMarkerMenu: true,
      markerMenuPos: e.latLng,
      markerMenuItemsList: itemsList,
    });
  };

  onClickMarkerMenuItem = (itemString) => {
    this.onMarkerMenuClose();
    this.props.onCorridorChangeCallback(itemString);
  };

  onMouseOverMarkerMenuItem = (itemString) => {
    this.setState({ highlightedCorridor: itemString });
  };

  onMouseOutOfMarkerMenuItem = () => {
    this.setState({ highlightedCorridor: null });
  };

  onMarkerMenuClose = () => {
    this.setState({
      displayMarkerMenu: false,
      markerMenuPos: null,
      highlightedCorridor: null,
    });
  };

  stopIntersectionsAlertStateUpdate = () => {
    this.abortControllerForAlertStates &&
      this.abortControllerForAlertStates.abort("canceled");
    clearInterval(this.intersectionAlertStateUpdateIntervalTimerId);
  };

  resetUpdateIntersectionsAlertState = () => {
    this.stopIntersectionsAlertStateUpdate();

    this.intersectionsAlertStateMap = new Map();

    this.abortControllerForAlertStates = new AbortController();
    this.lastIntersectionStatsUpdateTimeMs =
      Date.now() -
      this.props.appConfig.uiDelaySeconds * 1000 -
      this.props.appConfig.apiRefreshIntervalSeconds * 1000;
    this.updateIntersectionsAlertState();
    this.intersectionAlertStateUpdateIntervalTimerId = setInterval(() => {
      this.updateIntersectionsAlertState();
    }, this.props.appConfig.apiRefreshIntervalSeconds * 1000);
  };

  stopUpdateIntersectionsLastUpdatedTime = () => {
    this.abortControllerForLastUpdatedTime &&
      this.abortControllerForLastUpdatedTime.abort("canceled");
    clearInterval(this.intersectionsLastUpdatedTimeUpdateIntervalTimerId);
  };

  resetUpdateIntersectionsLastUpdatedTime = () => {
    this.stopUpdateIntersectionsLastUpdatedTime();
    this.abortControllerForLastUpdatedTime = new AbortController();
    this.updateIntersectionsLastUpdatedTime();
    this.intersectionsLastUpdatedTimeUpdateIntervalTimerId = setInterval(() => {
      this.updateIntersectionsLastUpdatedTime();
    }, this.props.appConfig.apiRefreshIntervalSeconds * 1000);
  };

  reset = () => {
    this.resetUpdateIntersectionsAlertState();
    this.resetUpdateIntersectionsLastUpdatedTime();
  };

  UNSAFE_componentWillMount() {
    this.reset();
  }

  render() {
    if (this.props.uiState.viewType !== "city-level-live") return null;

    let markers = [];
    for (const [intersectionName, intersectionConfig] of this.props.metadata.intersectionsMap) {
      let type = 0;
      let showHighlightedCorridor = null;

      if (
        !this.state.intersectionsLastUpdatedTimeMap.has(intersectionName) ||
        this.state.intersectionsLastUpdatedTimeMap.get(intersectionName) <
          Date.now() - this.props.appConfig.intersectionInactiveInSec * 1000
      ) {
        type = 3;
      }

      if (this.intersectionsAlertStateMap.get(intersectionName) === true) {
        type = 1;
      } else if (
        intersectionConfig.corridorsSet.has(this.props.uiState.selectedCorridor)
      ) {
        type = 2;
      }

      if (
        this.state.highlightedCorridor !== null &&
        intersectionConfig.corridorsSet.has(this.state.highlightedCorridor)
      ) {
        showHighlightedCorridor = this.state.highlightedCorridor;
      }

      markers.push(
        <IntersectionMarker
          key={"intersection-marker" + intersectionName}
          data={intersectionConfig}
          type={type}
          showHighlightedCorridor={showHighlightedCorridor}
          zoomLevel={this.props.zoom}
          onLeftClick={this.onLeftClickMarker}
          onRightClick={this.onRightClickMarker}
          enableSpiderfier={this.props.enableSpiderfier}
          onLoad={this.props.onLoad}
        />
      );
    }

    return (
      <>
        {markers}
        {this.state.displayMarkerMenu === true ? (
          <MarkerMenu
            itemsStringList={this.state.markerMenuItemsList}
            position={this.state.markerMenuPos}
            onClickItem={this.onClickMarkerMenuItem.bind(this)}
            onClose={this.onMarkerMenuClose.bind(this)}
            onMouseOverItem={this.onMouseOverMarkerMenuItem.bind(this)}
            onMouseOutOfItem={this.onMouseOutOfMarkerMenuItem.bind(this)}
          />
        ) : null}
      </>
    );
  }

  componentWillUnmount() {
    this.stopIntersectionsAlertStateUpdate();
    this.stopUpdateIntersectionsLastUpdatedTime();
  }
}

let myConnect = connect(
  (state) => ({
    appConfig: state.appConfig,
    metadata: state.metadata,
    uiState: state.uiState,
  }),
  ReduxUtils.combineDispatchToProps(AppLogsConsoleReduxUtil.slice.actions)
);

const ConnectedIntersectionMarkersOverlay = myConnect(IntersectionMarkersOverlay);

export default function IntersectionMarkersOverlayWrapper(props) {
  return <ConnectedIntersectionMarkersOverlay {...props} />;
}
