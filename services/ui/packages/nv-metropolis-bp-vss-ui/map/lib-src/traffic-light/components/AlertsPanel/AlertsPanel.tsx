// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { Component } from "react";
import {
  InfoOutlined as InfoOutlinedIcon,
  DoNotDisturbOnOutlined as DoNotDisturbOnOutlinedIcon,
} from "common/warehouse/mui-icons";
import { List } from "common/warehouse/mui";

import {
  UserMessage,
  EventCard,
  LastUpdateTimeDisplay,
  ReduxUtils,
  AlertsUrl,
  IncidentsUrl,
  MetricsLastProcessedTimestampUrl,
  FramesUrl,
  TrackerBehaviorLocationsUrl,
  Utils,
  joinUrl,
  isAbortError,
  readJson,
  connect,
} from "common/warehouse";

import UiStateReduxUtil from "../../utils/UiStateReduxUtil";
import {
  anomalyHasTrajectory,
  anomalyTimeWindow,
  behaviorIdsForAnomaly,
  mergeBehaviorLocations,
  prepareAnomalyForReplay,
  takeAnomalyList,
} from "../../utils/anomalyReplay";

import classes from "./AlertsPanel.module.css";

/**
 * Panel to display a list of useful items
 */
class AlertsPanel extends Component {
  state = {
    liveModeAnomalyList: [],
    liveAlertList: [],
    liveIncidentList: [],
  };

  generateCurrentAnomalyList(data) {
    return takeAnomalyList(data, this.props.appConfig.alertListLength);
  }

  fetchData = (url, place, dataType, fromTimestamp, toTimestamp) => {
    fetch(joinUrl(url, {
      place: place,
      fromTimestamp: fromTimestamp,
      toTimestamp: toTimestamp,
    }), {
      signal: this.abortController?.signal,
    })
    .then((response) => readJson(response))
    .then((data) => {
      const list = this.generateCurrentAnomalyList(
        dataType === "alerts" ? data?.alerts : data?.incidents,
      );
      this.setState(
        dataType === "alerts"
          ? { liveAlertList: list }
          : { liveIncidentList: list },
      );
    })
    .catch((thrown) => {
      if (isAbortError(thrown)) {
        return;
      }
      let userMessage = new UserMessage(
        UserMessage.SRC_TYPE.HTTP_ERROR,
        UserMessage.MESSAGE_LEVEL.ERROR,
        "Fetch Alerts/Incidents for place: " + place,
        thrown
      );
      console.error(userMessage.getLongString());
    });
  }

  updateAlerts = (props) => {
    this.place =
      "city=" +
      this.props.metadata.cityConfig.name +
      "/intersection=" +
      props.uiState.selectedIntersection;
    const newQueryTimeMs = Date.now() - props.appConfig.uiDelaySeconds * 1000;
    const queryStartTimeMs =
      newQueryTimeMs -
      props.appConfig.alertQueryDurationInHours * 60 * 60 * 1000;
    const fromTimestamp = new Date(queryStartTimeMs).toISOString();
    const toTimestamp = new Date(newQueryTimeMs).toISOString();
    this.fetchData(AlertsUrl, this.place, "alerts", fromTimestamp, toTimestamp);
    this.fetchData(IncidentsUrl, this.place, "incidents", fromTimestamp, toTimestamp);
  };

  alertClickHandler(alertData) {
    this.props.alertClickHandler && this.props.alertClickHandler(alertData);

    const prepared = prepareAnomalyForReplay(
      alertData,
      Boolean(this.props.appConfig.debugAnomaly),
    );

    this.stopAlertsUpdateCycles();
    this.live = false;
    this.type = prepared.type;
    this.props.updateUiState({
      selectedSensorId: prepared.sensorId || prepared.sensor?.id,
      selectedAnomaly: prepared,
      viewType: "intersection-level-alert",
      startAnomalyAnimation: false,
      anomalyReplaySeq: (this.props.uiState.anomalyReplaySeq || 0) + 1,
    });
    this._enrichAnomalyLocations(prepared);
  }

  _enrichAnomalyLocations(prepared) {
    this._locationAbortController && this._locationAbortController.abort("canceled");
    if (anomalyHasTrajectory(prepared)) {
      return;
    }
    const window = anomalyTimeWindow(prepared);
    const behaviorIds = behaviorIdsForAnomaly(prepared);
    const globalId = prepared.globalId;
    if (!window || (behaviorIds.length === 0 && !globalId)) {
      return;
    }
    const params = new URLSearchParams();
    params.append("fromTimestamp", window.fromTimestamp);
    params.append("toTimestamp", window.toTimestamp);
    if (behaviorIds.length > 0) {
      behaviorIds.forEach((id) => params.append("behaviorIds", id));
    } else {
      params.append("globalId", String(globalId));
    }
    this._locationAbortController = new AbortController();
    fetch(joinUrl(TrackerBehaviorLocationsUrl, params), {
      signal: this._locationAbortController.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        if (this.live) return;
        const merged = mergeBehaviorLocations(
          prepared,
          data?.behaviors,
          Boolean(this.props.appConfig.debugAnomaly),
        );
        this.props.updateUiState({ selectedAnomaly: merged });
      })
      .catch((thrown) => {
        if (isAbortError(thrown)) return;
        console.error(
          new UserMessage(
            UserMessage.SRC_TYPE.HTTP_ERROR,
            UserMessage.MESSAGE_LEVEL.ERROR,
            "Fetch behavior locations for event replay",
            thrown,
          ).getLongString(),
        );
      });
  }

  _isLiveView(props = this.props) {
    return props.uiState.viewType !== "intersection-level-alert";
  }

  startAlertsUpdateCycles = (props) => {
    this.stopAlertsUpdateCycles();
    this.abortController = new AbortController();
    this.updateAlerts(props);

    this.intervalTimerId = setInterval(() => {
      this.updateAlerts(props);
    }, props.appConfig.apiRefreshIntervalSeconds * 1000);
  };

  stopAlertsUpdateCycles() {
    clearInterval(this.intervalTimerId);
    this.abortController && this.abortController.abort("canceled");
  }

  UNSAFE_componentWillMount() {
    this.live = this._isLiveView();
    this.type = "mdx-alerts";
    if (this.live) {
      this.startAlertsUpdateCycles(this.props);
    }
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    const nextLive = this._isLiveView(nextProps);
    if (this.live !== nextLive) {
      this.live = nextLive;
      if (nextLive) {
        this._locationAbortController &&
          this._locationAbortController.abort("canceled");
        this.startAlertsUpdateCycles(nextProps);
      } else {
        this.stopAlertsUpdateCycles();
      }
      return;
    }
    if (this.live !== true) {
      return;
    }
    if (
      nextProps.uiState.selectedIntersection ===
        this.props.uiState.selectedIntersection &&
      nextProps.appConfig?.apiRefreshIntervalSeconds ===
        this.props.appConfig?.apiRefreshIntervalSeconds &&
      nextProps.appConfig?.alertQueryDurationInHours ===
        this.props.appConfig?.alertQueryDurationInHours
    ) {
      return;
    }
    this.startAlertsUpdateCycles(nextProps);
  }

  componentWillUnmount() {
    this.stopAlertsUpdateCycles();
    this._locationAbortController && this._locationAbortController.abort("canceled");
  }

  render() {
    let items,
      subTitle = "",
      titleDiv;

    if (!this._isLiveView()) {
      const selectedAnomaly = this.props.uiState.selectedAnomaly;
      titleDiv = (
        <div className={classes.title}>
          Selected {this.type === "mdx-alerts" ? "Alert" : "Incident"}
        </div>
      );
      subTitle = (
        <>
          <InfoOutlinedIcon />
          &nbsp;Click the event card to replay
        </>
      );
      const selectedSensorId =
        selectedAnomaly?.sensorId || selectedAnomaly?.sensor?.id;
      items = selectedAnomaly ? (
        <EventCard
          {...{
            endpoints: {
              framesUrl: FramesUrl,
              mediaServerBaseUrl: Utils.getMediaServiceBaseUrl(
                this.props.metadata.sensorsMap.get(selectedSensorId)
              ),
            },
            data: selectedAnomaly,
            sensorConfig: this.props.metadata.sensorsMap.get(selectedSensorId),
            highlighted: true,
            isRequiredThumbnailImage: true,
            onClick: () => this.alertClickHandler(selectedAnomaly),
          }}
        />
      ) : null;
    } else {
      titleDiv = <div className={classes.title}>Alerts & Incidents</div>;
      subTitle = (
        <LastUpdateTimeDisplay
          apiUrl={MetricsLastProcessedTimestampUrl}
          placeOrSensorId={{ place: this.place }}
          apiRefreshIntervalSeconds={
            this.props.appConfig.apiRefreshIntervalSeconds
          }
        />
      );
      const combinedList = [...this.state.liveAlertList, ...this.state.liveIncidentList];
      if (combinedList.length === 0) {
        items = (
          <div className={classes.empty} data-testid="alerts-panel-empty">
            <DoNotDisturbOnOutlinedIcon fontSize={48} htmlColor="#9ca3af" />
            <span>No alerts or incidents in this window</span>
          </div>
        );
      } else {
        items = combinedList.map((singleData, index) => {
          return (
            <React.Fragment key={index}>
              <EventCard
                {...{
                  endpoints: {
                    framesUrl: FramesUrl,
                    mediaServerBaseUrl: Utils.getMediaServiceBaseUrl(
                      this.props.metadata.sensorsMap.get(singleData.sensorId || singleData.sensor.id)
                    ),
                  },
                  data: singleData,
                  sensorConfig: this.props.metadata.sensorsMap.get(
                    singleData.sensorId || singleData.sensor.id
                  ),
                  highlighted: false,
                  onClick: () => this.alertClickHandler(singleData),
                  isRequiredThumbnailImage: true,
                }}
              />
              <hr className={classes.separator} />
            </React.Fragment>
          );
        });
      }
    }

    return (
      <div className={classes.alerts}>
        <div className={classes.alertsHeader}>
          {titleDiv}
          <div className={classes.subtitle}>{subTitle}</div>
        </div>
        <div className={classes.list}>
          {this._isLiveView() &&
          this.state.liveAlertList.length === 0 &&
          this.state.liveIncidentList.length === 0 ? (
            items
          ) : (
            <List>{items}</List>
          )}
        </div>
      </div>
    );
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    uiState: state.uiState,
    metadata: state.metadata,
  }),
  ReduxUtils.combineDispatchToProps(
    UiStateReduxUtil.slice.actions
  )
)(AlertsPanel);
