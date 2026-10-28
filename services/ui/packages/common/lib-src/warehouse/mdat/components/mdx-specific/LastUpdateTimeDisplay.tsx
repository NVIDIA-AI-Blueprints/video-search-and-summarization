// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { joinUrl, isAbortError, readJson } from "common/warehouse";
import React, { PureComponent } from "react";
import _ from "lodash";

import UserMessage from "../../utils/UserMessage";

/**
 * Displays Last Update Time for a place or sensor
 *
 * props:
 *    apiUrl
 *    apiRefreshIntervalSeconds (optional)
 *    placeOrSensorId
 */
class LastUpdateTimeDisplay extends PureComponent {
  static DEFAULT_TIME_STR = "<unknown>";

  state = {
    timeStr: LastUpdateTimeDisplay.DEFAULT_TIME_STR,
  };

  updateLastUpdateTime = (props) => {
    fetch(joinUrl(props.apiUrl, props.placeOrSensorId), {
      signal: this.abortController?.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        if (data && data.latestTimestamp) {
          this.setState({
            timeStr: new Date(
              data.latestTimestamp.timestamp
            ).toLocaleString(),
          });
        } else {
          this.setState({
            timeStr: LastUpdateTimeDisplay.DEFAULT_TIME_STR,
          });
        }
      })
      .catch((thrown) => {
        this.stoptLastTimeUpdateCycle();

        this.setState({
          timeStr: LastUpdateTimeDisplay.DEFAULT_TIME_STR,
        });

        const purposeMsg =
          "Query Last Updated time for " +
          JSON.stringify(props.placeOrSensorId);

        if (thrown.config) {
          let userMessage = new UserMessage(
            UserMessage.SRC_TYPE.HTTP_ERROR,
            UserMessage.MESSAGE_LEVEL.ERROR,
            purposeMsg,
            thrown,
            "Retry",
            () => this.startLastTimeUpdateCycle(props)
          );

          if (isAbortError(thrown)) {
            console.log(userMessage.getLongString());
          } else {
            console.error(userMessage.getLongString());
          }
        } else {
          let userMessage = new UserMessage(
            UserMessage.SRC_TYPE.GENERIC,
            UserMessage.MESSAGE_LEVEL.ERROR,
            purposeMsg,
            thrown
          );
          console.error(userMessage.getLongString());
        }
      });
  };

  startLastTimeUpdateCycle = (props) => {
    this.stoptLastTimeUpdateCycle();
    this.setState({ timeStr: LastUpdateTimeDisplay.DEFAULT_TIME_STR });
    this.abortController = new AbortController();
    this.updateLastUpdateTime(props);
    clearInterval(this.updateCycleIntervalTimerId);
    /* make ajax call regularly */
    this.updateCycleIntervalTimerId = setInterval(
      () => {
        this.updateLastUpdateTime(props);
      },
      (props.apiRefreshIntervalSeconds ? props.apiRefreshIntervalSeconds : 1) *
        1000
    );
  };

  stoptLastTimeUpdateCycle = () => {
    this.abortController &&
      this.abortController.abort(
        `${this.constructor.name} - API Call - Canceling on Unmount`
      );

    clearInterval(this.updateCycleIntervalTimerId);
  };

  UNSAFE_componentWillMount() {
    this.startLastTimeUpdateCycle(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (!_.isEqual(this.props, nextProps)) {
      this.startLastTimeUpdateCycle(nextProps);
    }
  }

  render() {
    return <div>Last Updated: {this.state.timeStr}</div>;
  }

  componentWillUnmount() {
    this.stoptLastTimeUpdateCycle();
  }
}

export default LastUpdateTimeDisplay;
