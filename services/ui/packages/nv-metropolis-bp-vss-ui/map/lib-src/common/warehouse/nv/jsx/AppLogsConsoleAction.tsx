// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { connect } from "react-redux";
import {
  Info as InfoIcon,
  Circle as CircleIcon,
} from "common/warehouse/mui-icons";

import { LogsConsole } from "common/warehouse/mdat";

import { AppConstants } from "../js/UrlConstants";
import AppLogsConsoleReduxUtil from "../js/AppLogsConsoleReduxUtil";

import Styles from "../styles/AppLogsConsoleAction.module.css";

/**
 * Application wide Logs Console
 * Includes hide/show Logs Console Button with main logs console
 */
class AppLogsConsoleAction extends React.PureComponent {
  state = {
    showLogsConsole: false,
  };

  // Record last observed list length before next render.
  lastLogListLength = 0;

  onClickIcon = () => {
    this.setState({ showLogsConsole: !this.state.showLogsConsole });
  };

  onClearConsole = () => {
    this.props["clearLogsFrom" + AppConstants.APP_LOGS_CONSOLE_NAME]();
  };

  render() {
    const showNotificationIcon =
      !this.state.showLogsConsole &&
      this.lastLogListLength !== this.props.logList.length;

    // Update last recorded list length
    this.lastLogListLength = this.props.logList.length;

    return (
      <React.Fragment>
        {/* Icon to hide/show logs console */}
        <div
          className={Styles.iconContainer}
          onClick={this.onClickIcon.bind(this)}
        >
          <InfoIcon
            title="Show/hide App Logs Console"
            className={Styles.icon}
          />
          {showNotificationIcon ? (
            <CircleIcon className={Styles.notificationIcon} />
          ) : null}
        </div>
        {this.state.showLogsConsole ? (
          <div className={Styles.logsConsoleContainer}>
            <LogsConsole
              logList={this.props.logList}
              onClearConsole={this.onClearConsole.bind(this)}
            />
          </div>
        ) : null}
      </React.Fragment>
    );
  }
}

export default connect(
  (state) => ({ logList: state[AppConstants.APP_LOGS_CONSOLE_NAME].logList }),
  AppLogsConsoleReduxUtil.slice.actions
)(AppLogsConsoleAction);
