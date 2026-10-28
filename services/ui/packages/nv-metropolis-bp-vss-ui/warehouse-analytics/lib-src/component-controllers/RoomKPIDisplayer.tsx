// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { Axios } from "common/warehouse";
import React from "react";
import { connect } from "react-redux";

import { Widget, UserMessage, MdxPlaceArray2String } from "common/warehouse";
import {
  AppLogsConsoleReduxUtil,
  AppConstants,
  ReduxUtils,
  MetricsOccupancyTripwireUrl,
  KPI_REQUEST_TIMEOUT_MS,
} from "common/warehouse";

import RoomKPIsDisplayElement from "./../components/RoomKPIsDisplayElement/RoomKPIsDisplayElement";
import KPIStatusDisplay from "./../components/KPIStatusDisplay/KPIStatusDisplay";

class RoomKPIDisplayer extends React.PureComponent {
  // Request state tracking
  isRequestInProgress = false;
  lastRequestCompletedTimeMs = 0;

  state = {
    data: null,
    isLoading: true,
    hasError: false,
    lastSuccessfulUpdateTimeMs: null,
  };

  // TODO: Refactor into a single place with duplicate function in ObjectsDataVisualizer
  getCurrentTimeMs = () => {
    return Date.now() - this.props.appConfig.uiDelaySeconds * 1000;
  };

  getPlaceStringForRoom = (props) => {
    // Get a sensor from the selected room to access its place array
    const room = props.metadata.roomsMap.get(props.uiState.selectedRoom);
    if (!room || room.sensorIds.size === 0) {
      return null;
    }
    const sensorId = room.sensorIds.values().next().value;
    const sensor = props.metadata.sensorsMap.get(sensorId);
    if (!sensor || !sensor.place) {
      return null;
    }
    // Filter out city level and use MdxPlaceArray2String for the place string
    const placeWithoutCity = sensor.place.filter((p) => p.name !== "city");
    return MdxPlaceArray2String(placeWithoutCity, "room");
  };

  /**
   * Extract IN and OUT counts from events array
   */
  _extractInOutCounts = (events) => {
    let inCount = 0;
    let outCount = 0;
    if (!events || !Array.isArray(events)) {
      return { inCount, outCount };
    }
    events.forEach((event) => {
      if (event.type === "IN") {
        inCount = event.count;
      } else if (event.type === "OUT") {
        outCount = event.count;
      }
    });
    return { inCount, outCount };
  };

  startRequestCyclePeopleDataForRoom = (props) => {
    let place = this.getPlaceStringForRoom(props);
    if (!place) {
      console.warn("Could not determine place string for room KPI request");
      this.setState({ isLoading: false, hasError: true });
      return;
    }
    this.cancelCyclesForPeopleDataForOldPlace();
    this.startRequestTimeMsPeopleKpiDataForRoom = this.getCurrentTimeMs();
    this.newQueryTimeMs = this.getCurrentTimeMs();
    this.isRequestInProgress = false;
    this.lastRequestCompletedTimeMs = 0;

    // Reset to loading state when starting new cycle
    this.setState({ isLoading: true, hasError: false, data: null });

    // Function to fetch KPI data
    const fetchKpiData = () => {
      // Skip if a request is already in progress
      if (this.isRequestInProgress) {
        return;
      }

      // Ensure minimum interval between requests (use api refresh interval, default 1000ms)
      const minIntervalMs = (props.appConfig.apiRefreshIntervalSeconds || 1) * 1000;
      const timeSinceLastRequest = Date.now() - this.lastRequestCompletedTimeMs;
      if (this.lastRequestCompletedTimeMs > 0 && timeSinceLastRequest < minIntervalMs) {
        return;
      }

      this.isRequestInProgress = true;
      this.newQueryTimeMs = this.getCurrentTimeMs();

      const timestampISO = new Date(this.newQueryTimeMs).toISOString();

      Axios.get(MetricsOccupancyTripwireUrl, {
        params: {
          place: place,
          timestamp: timestampISO,
        },
        timeout: KPI_REQUEST_TIMEOUT_MS,
      })
        .then((res) => {
          this.isRequestInProgress = false;
          this.lastRequestCompletedTimeMs = Date.now();

          let processedData = {};

          // Handle the API response structure
          // Response format: { place, timestamp, occupancy: Array }
          // Each occupancy item: { objectType, count, details: { events: [{type, count}] } }
          const occupancyData = res.data.occupancy;
          if (occupancyData && Array.isArray(occupancyData) && occupancyData.length > 0) {
            // Process first item for backward compatibility
            const firstItem = occupancyData[0];
            
            // Extract IN/OUT from details.events
            const { inCount, outCount } = this._extractInOutCounts(
              firstItem.details?.events
            );
            
            processedData["type"] = firstItem.objectType; // for backwards compatibility
            processedData["inCount"] = inCount;
            processedData["outCount"] = outCount;
            processedData["occupancy"] = firstItem.count; // count is the occupancy
            
            // Build objectTypes array for multi-type support
            processedData.objectTypes = occupancyData.map((item) => {
              const { inCount: itemIn, outCount: itemOut } =
                this._extractInOutCounts(item.details?.events);
              return {
                type: item.objectType || "UNKNOWN",
                inCount: itemIn,
                outCount: itemOut,
                occupancy: item.count,
              };
            });
          } else {
            // Empty occupancy array - no data available
            processedData["occupancy"] = null;
            processedData["inCount"] = null;
            processedData["outCount"] = null;
            processedData["type"] = null;
            processedData["objectTypes"] = [];
          }
          
          this.setState({ data: processedData, isLoading: false, hasError: false, lastSuccessfulUpdateTimeMs: Date.now() });
        })
        .catch((thrown) => {
          this.isRequestInProgress = false;
          this.lastRequestCompletedTimeMs = Date.now();

          // Keep existing data, just mark as error - don't cancel the interval so retries continue
          this.setState((prevState) => ({ isLoading: false, hasError: true, data: prevState.data }));

          // Handle timeout specifically
          if (thrown.code === "ECONNABORTED") {
            console.log(
              "[RoomKPIDisplayer] Request timed out after " +
                KPI_REQUEST_TIMEOUT_MS / 1000 +
                "s - will retry for place: " +
                place
            );
            return;
          }

          // Log other errors
          if (thrown.config) {
            let userMessage = new UserMessage(
              UserMessage.SRC_TYPE.AXIOS_ERROR,
              UserMessage.MESSAGE_LEVEL.ERROR,
              "Fetch Tripwire KPIs for place: " + place,
              thrown,
              "Retry",
              () => {
                this.startRequestCyclePeopleDataForRoom(props);
              }
            );
            props["addLogTo" + AppConstants.APP_LOGS_CONSOLE_NAME](userMessage);
            console.error(userMessage.getLongString());
          } else {
            let userMessage = new UserMessage(
              UserMessage.SRC_TYPE.GENERIC,
              UserMessage.MESSAGE_LEVEL.ERROR,
              "Fetch Tripwire KPIs for place: " + place,
              thrown
            );
            console.error(userMessage.getLongString());
          }
        });
    };

    // Make immediate request, then set up interval for subsequent requests
    fetchKpiData();
    this.requestPeopleKpiDataForRoomCycleIntervalId = setInterval(
      fetchKpiData,
      props.appConfig.apiRefreshIntervalSeconds * 1000
    );
  };

  cancelCyclesForPeopleDataForOldPlace = () => {
    clearInterval(this.requestPeopleKpiDataForRoomCycleIntervalId);
    this.isRequestInProgress = false;
  };

  UNSAFE_componentWillMount() {
    // Start requesting data from backend for the people for current camera
    this.startRequestCyclePeopleDataForRoom(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    this.startRequestCyclePeopleDataForRoom(nextProps);
  }

  render() {
    const { data, isLoading, hasError, lastSuccessfulUpdateTimeMs } = this.state;

    let content;
    if (hasError && data) {
      // Show last good data with error indicator
      content = <RoomKPIsDisplayElement kpiData={data} hasError={true} lastSuccessfulUpdateTimeMs={lastSuccessfulUpdateTimeMs} />;
    } else if (hasError) {
      // No data available, show error state
      content = <KPIStatusDisplay type="error" />;
    } else if (isLoading || !data) {
      content = <KPIStatusDisplay type="loading" />;
    } else {
      content = <RoomKPIsDisplayElement kpiData={data} />;
    }

    return (
      <Widget
        // Setting bottom to non-zero since sometimes flickering due to scroll bars
        // used to occur.
        bottom="0"
        left="0"
        right="0"
        height="3rem"
        width="100%"
        zIndex="15"
        showShadow={true}
      >
        {content}
      </Widget>
    );
  }

  componentWillUnmount() {
    this.cancelCyclesForPeopleDataForOldPlace();
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    uiState: state.uiState,
    metadata: state.metadata,
  }),
  ReduxUtils.combineDispatchToProps(AppLogsConsoleReduxUtil.slice.actions)
)(RoomKPIDisplayer);
