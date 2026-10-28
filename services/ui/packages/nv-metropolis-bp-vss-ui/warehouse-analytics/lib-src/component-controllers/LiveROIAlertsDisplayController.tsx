// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Axios } from "common/warehouse";
import { connect } from "react-redux";
import { Divider as Divider } from "common/warehouse/mui";
import { DoNotDisturbOnOutlined as DoNotDisturbOnOutlinedIcon } from "common/warehouse/mui-icons";
import { Tooltip as Tooltip } from "common/warehouse/mui";

import { EventCard, UserMessage } from "common/warehouse";
import {
  AppLogsConsoleReduxUtil,
  AppConstants,
  ReduxUtils,
  EventsRoiUrl,
  FramesUrl,
  Utils,
} from "common/warehouse";

import AlertsPanel from "../components/AlertsPanel/AlertsPanel";

/**
 * Communicates with Web-Api and Manages the content to be shown in the ROI Events Panel in live mode
 */
class LiveROIAlertsDisplayController extends React.PureComponent {
  state = {
    anomalyList: [],
  };

  // TODO: Allow user to select the list length in UI?
  generateCurrentAnomalyList(data) {
    const events = Array.isArray(data) ? data : [];
    const numMaxItems = this.props.appConfig.alertListLength;
    if (events.length >= numMaxItems) {
      return events.slice(0, numMaxItems);
    }
    return events;
  }

  updateAlerts = (props) => {
    const newQueryTimeMs = Date.now() - props.appConfig.uiDelaySeconds * 1000;
    const queryStartTimeMs =
      newQueryTimeMs -
      props.appConfig.alertQueryDurationInHours * 60 * 60 * 1000;
    const fromTimestamp = new Date(queryStartTimeMs).toISOString();
    const toTimestamp = new Date(newQueryTimeMs).toISOString();

    Axios.get(EventsRoiUrl, {
      params: {
        sensorId: props.uiState.selectedSensorId,
        fromTimestamp: fromTimestamp,
        toTimestamp: toTimestamp,
        ...(props.uiState.selectedSensorId.includes("-cv") ? { objectType: "Object" } : {}),
      },
      cancelToken: this.source.token,
    })
      .then((res) => {
        this.setState({
          anomalyList: this.generateCurrentAnomalyList(res.data.roiEvents),
        });
      })
      .catch((thrown) => {
        this.stopAlertsUpdateCycles();
        let userMessage = new UserMessage(
          UserMessage.SRC_TYPE.AXIOS_ERROR,
          UserMessage.MESSAGE_LEVEL.ERROR,
          "Fetch ROI Alerts for sensor: " + props.uiState.selectedSensorId,
          thrown,
          "Retry",
          () => this.startAlertsUpdateCycles(props)
        );
        props["addLogTo" + AppConstants.APP_LOGS_CONSOLE_NAME](userMessage);

        if (Axios.isCancel(thrown)) {
          console.log(userMessage.getLongString());
        } else {
          console.error(userMessage.getLongString());
        }
      });
  };

  startAlertsUpdateCycles = (props) => {
    // Stop previous cycle
    this.stopAlertsUpdateCycles();
    this.source = Axios.CancelToken.source();
    this.updateAlerts(props);

    /* make ajax call regularly */
    this.intervalTimerId = setInterval(() => {
      this.updateAlerts(props);
    }, props.appConfig.apiRefreshIntervalSeconds * 1000);
  };

  stopAlertsUpdateCycles = () => {
    if (this.source) {
      this.source.cancel();
    }
    clearInterval(this.intervalTimerId);
  };

  componentDidMount() {
    this.startAlertsUpdateCycles(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (
      nextProps.uiState.selectedSensorId !== this.props.uiState.selectedSensorId ||
      nextProps.appConfig.apiRefreshIntervalSeconds !==
        this.props.appConfig.apiRefreshIntervalSeconds ||
      nextProps.appConfig.alertQueryDurationInHours !==
        this.props.appConfig.alertQueryDurationInHours
    ) {
      this.startAlertsUpdateCycles(nextProps);
    }
  }

  componentWillUnmount() {
    this.stopAlertsUpdateCycles();
  }

  render() {
    let items;
    
    if (this.state.anomalyList.length === 0) {
      items = (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', width: '100%' }}>
          <Tooltip title="None">
            <DoNotDisturbOnOutlinedIcon style={{ color: 'gray', fontSize: '3rem' }} />
          </Tooltip>
        </div>
      );
    } else {
      items = this.state.anomalyList.map((singleData, index) => {
        return (
          <React.Fragment key={index}>
            <EventCard
              {...{
                endpoints: {
                  framesUrl: FramesUrl,
                  mediaServerBaseUrl: Utils.getMediaServiceBaseUrl(
                    this.props.metadata.sensorsMap.get(singleData.sensor.id)
                  ),
                },
                data: singleData,
                sensorConfig: this.props.metadata.sensorsMap.get(
                  singleData.sensor.id
                ),
                highlighted: false,
                isRemoveDashCV: this.props.appConfig.isRemoveDashCV,
                onClick: this.props.onSelectAlert,
              }}
            />
            <Divider />
          </React.Fragment>
        );
      });
    }

    return (
      <React.Fragment>
        <AlertsPanel 
          title="ROI Events"
          left={this.props.left}
          top={this.props.top}
          height={this.props.height}
          width={this.props.width}
        >
          {items}
        </AlertsPanel>
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
  ReduxUtils.combineDispatchToProps(AppLogsConsoleReduxUtil.slice.actions)
)(LiveROIAlertsDisplayController);

