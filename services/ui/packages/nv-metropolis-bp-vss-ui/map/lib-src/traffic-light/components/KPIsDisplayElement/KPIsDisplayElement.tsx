// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React, { Component } from "react";

import {
  UserMessage,
  Widget,
  AppLogsConsoleReduxUtil,
  AppConstants,
  ReduxUtils,
  MetricsAverageSpeedWithFlowrateUrl,
  MetricsAverageSpeedWithTravelTimeUrl,
  joinUrl,
  isAbortError,
  readJson,
  connect,
} from "common/map";

import classes from "./KPIsDisplayElement.module.css";

const EMPTY_PAIRS = [
  ["", ""],
  ["", ""],
  ["", ""],
];

const formatKpiName = (name) =>
  String(name || "").replace(/([a-z])([A-Z])/g, "$1 $2");

const tuplePairs = (metric) => {
  if (!metric) {
    return EMPTY_PAIRS;
  }
  const entries = Object.entries(metric).slice(0, 3);
  while (entries.length < 3) {
    entries.push(["", ""]);
  }
  return entries;
};

/**
 * Display Panel for KPIs for a location
 */
class KPIsDisplayElement extends Component {
  state = {
    kpis: [],
  };

  eraseDisplayedKpis() {
    this.setState({ kpis: [] });
  }

  updateKpis = (props) => {
    const newQueryTimeMs = Date.now() - props.appConfig.uiDelaySeconds * 1000;
    const fromTimestamp = new Date(this.lastKpisUpdateTimeMs).toISOString();
    const toTimestamp = new Date(newQueryTimeMs).toISOString();
    let metricsUrl;

    if (props.place.includes("corridor")) {
      metricsUrl = MetricsAverageSpeedWithTravelTimeUrl;
    } else {
      metricsUrl = MetricsAverageSpeedWithFlowrateUrl;
    }

    fetch(joinUrl(metricsUrl, {
      place: props.place,
      fromTimestamp: fromTimestamp,
      toTimestamp: toTimestamp,
    }), {
      signal: this.abortController?.signal,
    })
      .then((response) => readJson(response))
      .then((data) => {
        this.lastKpisUpdateTimeMs = newQueryTimeMs;
        this.setState({ kpis: data.metrics || [] });
      })
      .catch((thrown) => {
        clearInterval(this.interval);

        if (isAbortError(thrown)) {
          return;
        }

        let userMessage = new UserMessage(
          thrown.config
            ? UserMessage.SRC_TYPE.HTTP_ERROR
            : UserMessage.SRC_TYPE.GENERIC,
          UserMessage.MESSAGE_LEVEL.ERROR,
          "Fetch KPIs for place: " + props.place,
          thrown,
          "Retry",
          () => this.startKpiUpdatesCycle(props)
        );
        props["addLogTo" + AppConstants.APP_LOGS_CONSOLE_NAME](userMessage);

        this.eraseDisplayedKpis();
        console.error(userMessage.getLongString());
      });
  };

  cancelRequest = () => {
    this.abortController && this.abortController.abort("canceled");
  };

  startKpiUpdatesCycle = (props) => {
    this.eraseDisplayedKpis();
    this.abortController = new AbortController();
    this.lastKpisUpdateTimeMs =
      Date.now() -
      props.appConfig.uiDelaySeconds * 1000 -
      props.appConfig.apiRefreshIntervalSeconds * 1000;
    this.updateKpis(props);

    this.interval = setInterval(() => {
      this.updateKpis(props);
    }, props.appConfig.apiRefreshIntervalSeconds * 1000);
  };

  UNSAFE_componentWillMount() {
    this.startKpiUpdatesCycle(this.props);
  }

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (
      nextProps.place === this.props.place &&
      nextProps.appConfig?.apiRefreshIntervalSeconds ===
        this.props.appConfig?.apiRefreshIntervalSeconds &&
      nextProps.appConfig?.uiDelaySeconds === this.props.appConfig?.uiDelaySeconds
    ) {
      return;
    }
    clearInterval(this.interval);
    this.cancelRequest();
    this.startKpiUpdatesCycle(nextProps);
  }

  componentWillUnmount() {
    clearInterval(this.interval);
    this.cancelRequest();
  }

  render() {
    const kpisToBeDisplayed = this.state.kpis
      .slice(0, 2)
      .sort((elem1, elem2) => {
        if (elem1.direction < elem2.direction) return 1;
        else if (elem1.direction > elem2.direction) return -1;
        return 0;
      });

    const rightPairs = tuplePairs(kpisToBeDisplayed[0]);
    const leftPairs = tuplePairs(kpisToBeDisplayed[1]);

    const renderTuple = (pairs, keyPrefix) => (
      <div className={classes.kpiTuple}>
        {pairs.map(([name, value], index) => (
          <div className={classes.kpiPair} key={`${keyPrefix}-${index}`}>
            <div className={classes.kpiName}>{formatKpiName(name)}</div>
            <div className={classes.kpiValue}>{value}</div>
          </div>
        ))}
      </div>
    );

    return (
      <Widget
        bottom="0"
        right="0"
        height="2.75rem"
        width="73%"
        zIndex="25"
      >
        <div className={classes.kpiPanel} title="Traffic Metrics">
          {renderTuple(leftPairs, "left")}
          {renderTuple(rightPairs, "right")}
        </div>
      </Widget>
    );
  }
}

let myConnect = connect(
  (state) => ({
    appConfig: state.appConfig,
  }),
  ReduxUtils.combineDispatchToProps(AppLogsConsoleReduxUtil.slice.actions)
);

export default myConnect(KPIsDisplayElement);
