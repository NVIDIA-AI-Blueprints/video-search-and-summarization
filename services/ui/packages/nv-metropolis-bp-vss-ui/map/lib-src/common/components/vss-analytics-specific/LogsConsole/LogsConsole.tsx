// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import {
  Cancel as CancelIcon,
  NotInterested as NotInterestedIcon,
} from "common/map/mui-icons";

import UserMessage from "../../../utils/UserMessage";

import Styles from "./LogsConsole.module.css";

/**
 * Presentational Logs Console to be added for any widget
 *
 * Uses UserMessage Type Logs in List as input
 * Has to be wrapped inside a container div
 */
const LogsConsole = (props) => {
  let listItems = <span className={Styles.listItem}>(empty)</span>; // default
  if (props.logList && props.logList.length !== 0) {
    listItems = props.logList.map((log, key) => {
      let fontColor;
      switch (log.getMessageLevel()) {
        case UserMessage.MESSAGE_LEVEL.INFO:
          fontColor = "blue";
          break;
        case UserMessage.MESSAGE_LEVEL.WARNING:
          fontColor = "orange";
          break;
        case UserMessage.MESSAGE_LEVEL.ERROR:
          fontColor = "red";
          break;
        default:
          break;
      }

      return (
        <span
          key={key}
          className={Styles.listItem}
          style={{ color: fontColor }}
        >
          {log.getLongString()}
          &nbsp;
          {log.getPostActionElement()}
        </span>
      );
    });
  }

  // TODO: Use Kaizen Buttons
  return (
    <div className={Styles.container}>
      <div className={Styles.buttonsBar}>
        {props.closeable ? (
          <CancelIcon
            className={Styles.closeBtn}
            title="Close"
            onClick={props.onClose}
          />
        ) : null}
        {props.onClearConsole ? (
          <NotInterestedIcon
            className={Styles.clearConsoleBtn}
            title="Clear Logs"
            onClick={props.onClearConsole}
          />
        ) : null}
      </div>
      <div className={Styles.list}>{listItems}</div>
    </div>
  );
};

export default LogsConsole;
