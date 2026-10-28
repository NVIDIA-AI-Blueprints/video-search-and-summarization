// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { LastUpdateTimeDisplay, Widget, Window } from "common/warehouse";
import { List as List } from "common/warehouse/mui";
import { connect } from "react-redux";
import { InfoOutlined as InfoOutlinedIcon } from "common/warehouse/mui-icons";

import { MetricsLastProcessedTimestampUrl } from "common/warehouse";

import classes from "./AlertsPanel.module.css";

/**
 * Panel to display Alerts/Anomalies related data
 */
const AlertsPanel = (props) => {
  // Default positioning if not provided
  const left = props.left || "0.6%";
  const top = props.top || "12%";
  const height = props.height || "70%";
  const width = props.width || "20%";

  // Check if this is a Selected Event panel (for vertical centering)
  const isSelectedEventPanel = props.title === "Selected Event" || 
                                props.title === "Selected Tripwire Event" || 
                                props.title === "Selected ROI Event";

  const listClassName = isSelectedEventPanel ? classes.listCentered : classes.list;

  return (
    <Widget left={left} top={top} height={height} width={width} showShadow={true}>
      <Window titleText={props.title}>
        <div className={classes.alerts}>
          <div className={classes.alertsHeader}>
            {(props.title === "Tripwire Events" || props.title === "ROI Events") && (
              <div className={classes.subtitle}>
                <LastUpdateTimeDisplay
                  apiUrl={MetricsLastProcessedTimestampUrl}
                  placeOrSensorId={{ sensorId: props.uiState.selectedSensorId }}
                  apiRefreshIntervalSeconds={
                    props.appConfig.apiRefreshIntervalSeconds
                  }
                />
              </div>
            )}
            {isSelectedEventPanel && (
              <div className={classes.subtitle}>
                <InfoOutlinedIcon />&nbsp;Click the event card to replay
              </div>
            )}
          </div>
          <div className={listClassName}>
            <List>{props.children}</List>
          </div>
        </div>
      </Window>
    </Widget>
  );
};

export default connect((state) => ({
  appConfig: state.appConfig,
  uiState: state.uiState,
}))(AlertsPanel);
