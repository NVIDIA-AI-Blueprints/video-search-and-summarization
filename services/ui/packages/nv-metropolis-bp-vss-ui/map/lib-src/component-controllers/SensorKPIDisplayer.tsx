// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { joinUrl, isAbortError, readJson } from "common/warehouse";
import React from "react";
import { connect } from "react-redux";

import { Widget, UserMessage } from "common/warehouse";
import {
  AppLogsConsoleReduxUtil,
  AppConstants,
  ReduxUtils,
  MetricsTripwireCountsUrl,
  KPI_REQUEST_TIMEOUT_MS,
} from "common/warehouse";

import SensorKPIsDisplayElement from "./../components/SensorKPIsDisplayElement/SensorKPIsDisplayElement";
import KPIStatusDisplay from "./../components/KPIStatusDisplay/KPIStatusDisplay";

class SensorKPIDisplayer extends React.PureComponent {
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
  getCurrentTimeMs = (props) => {
    return Date.now() - this.props.appConfig.uiDelaySeconds * 1000;
  };

  // TODO: make interval time configurable from backend
  startRequestCyclePeopleDataForSensor = (props) => {
    this.cancelCyclesForPeopleDataForOldSensor();
    this.newQueryTimeMs = this.getCurrentTimeMs(props);
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
      this.newQueryTimeMs = this.getCurrentTimeMs(props);

      // Use appStartTimeMs as constant fromTimestamp for sensor level KPI
      const fromTimestampMs = props.uiState.appStartTimeMs || Date.now();
      const toTimestampMs = this.newQueryTimeMs;
      const fromTimestampISO = new Date(fromTimestampMs).toISOString();
      const toTimestampISO = new Date(toTimestampMs).toISOString();
      
      // Calculate interval in minutes for display
      const intervalMinutes = Math.round((toTimestampMs - fromTimestampMs) / 60000);
      this.currentIntervalMinutes = intervalMinutes;

      fetch(joinUrl(MetricsTripwireCountsUrl, {
          fromTimestamp: fromTimestampISO,
          toTimestamp: toTimestampISO,
          sensorId: props.uiState.selectedSensorId,
          ...(props.uiState.selectedSensorId.includes("-cv") ? { objectType: "Object" } : {}),
        }), {
        signal: AbortSignal.timeout(KPI_REQUEST_TIMEOUT_MS),
      })
        .then((response) => readJson(response))
        .then((data) => {
          this.isRequestInProgress = false;
          this.lastRequestCompletedTimeMs = Date.now();

          // Group events by objectType
          // Use null-prototype object to prevent prototype pollution from external data
          const eventsByObjectType = Object.create(null);
          data.aggregatedMetrics.events.forEach((event) => {
            // Validate objectType is a string to prevent object pollution from external data
            const rawObjType = event.objectType;
            const objType = typeof rawObjType === "string" ? rawObjType : "UNKNOWN";
            // Skip prototype-polluting keys to prevent object pollution attacks
            if (objType === "__proto__" || objType === "constructor" || objType === "prototype") {
              return;
            }
            if (!eventsByObjectType[objType]) {
              eventsByObjectType[objType] = { type: String(objType) };
            }
            // Map IN/OUT to inCount/outCount
            // Validate count is a finite number to prevent object pollution from external data
            const count = typeof event.count === "number" && Number.isFinite(event.count) ? event.count : 0;
            // Validate event.type is a string before comparison
            const eventType = typeof event.type === "string" ? event.type : "";
            if (eventType === "IN") {
              eventsByObjectType[objType].inCount = count;
            } else if (eventType === "OUT") {
              eventsByObjectType[objType].outCount = count;
            }
          });

          // Convert to objectTypes array
          const objectTypes = Object.values(eventsByObjectType);

          // Build processedData with objectTypes array and backwards-compatible top-level fields
          const firstObjectType = objectTypes[0] || {};
          const processedData = {
            intervalMinutes: this.currentIntervalMinutes,
            objectTypes,
            // Backwards compatibility: top-level type, inCount, outCount from first objectType
            type: firstObjectType.type,
            inCount: firstObjectType.inCount,
            outCount: firstObjectType.outCount,
          };
          
          this.setState({ data: processedData, isLoading: false, hasError: false, lastSuccessfulUpdateTimeMs: Date.now() });
        })
        .catch((thrown) => {
          this.isRequestInProgress = false;
          this.lastRequestCompletedTimeMs = Date.now();

          // Keep existing data, just mark as error - don't cancel the interval so retries continue
          this.setState((prevState) => ({ isLoading: false, hasError: true, data: prevState.data }));

          // Handle timeout specifically
          if (isAbortError(thrown) || thrown.name === "TimeoutError") {
            console.log(
              "[SensorKPIDisplayer] Request timed out after " +
                KPI_REQUEST_TIMEOUT_MS / 1000 +
                "s - will retry for sensor: " +
                props.uiState.selectedSensorId
            );
            return;
          }

          // Log other errors
          let userMessage = new UserMessage(
            UserMessage.SRC_TYPE.HTTP_ERROR,
            UserMessage.MESSAGE_LEVEL.ERROR,
            "Fetch KPIs for sensor: " + props.uiState.selectedSensorId,
            thrown,
            "Retry",
            () => {
              this.startRequestCyclePeopleDataForSensor(props);
            }
          );
          props["addLogTo" + AppConstants.APP_LOGS_CONSOLE_NAME](userMessage);
          console.error(userMessage.getLongString());
        });
    };

    // Don't make immediate request - wait for first interval so there's a time
    // difference between fromTimestamp and toTimestamp (API requires from < to)
    this.requestPeopleKpiDataForSensorCycleIntervalId = setInterval(
      fetchKpiData,
      props.appConfig.apiRefreshIntervalSeconds * 1000
    );
  };

  cancelCyclesForPeopleDataForOldSensor = () => {
    clearInterval(this.requestPeopleKpiDataForSensorCycleIntervalId);
    this.isRequestInProgress = false;
  };

  UNSAFE_componentWillMount() {
    // Start requesting data from backend for the people for current camera
    this.startRequestCyclePeopleDataForSensor(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    this.startRequestCyclePeopleDataForSensor(nextProps);
  }

  render() {
    const { data, isLoading, hasError, lastSuccessfulUpdateTimeMs } = this.state;

    let content;
    if (hasError && data) {
      // Show last good data with error indicator
      content = <SensorKPIsDisplayElement kpiData={data} hasError={true} lastSuccessfulUpdateTimeMs={lastSuccessfulUpdateTimeMs} />;
    } else if (hasError) {
      // No data available, show error state
      content = <KPIStatusDisplay type="error" />;
    } else if (isLoading || !data) {
      content = <KPIStatusDisplay type="loading" />;
    } else {
      content = <SensorKPIsDisplayElement kpiData={data} />;
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
    this.cancelCyclesForPeopleDataForOldSensor();
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    metadata: state.metadata,
    uiState: state.uiState,
  }),
  ReduxUtils.combineDispatchToProps(AppLogsConsoleReduxUtil.slice.actions)
)(SensorKPIDisplayer);
