// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { readJson } from "common/warehouse";
import React from "react";

import { MetricsOccupancyResetUrl } from "common/warehouse";
import CssClasses from "./RoomOccupancySettingPanel.module.css";

const InputTitle = "Type +ve Integer (Max. 5 digits)";
const InputRegex = /^\d{1,5}$/; // only 5 digits long +ve integer allowed

/**
 * Room Occupancy Setting List Item Panel implementation for the PlaceContentSettings parent
 */

export default class RoomOccupancySettingPanel extends React.PureComponent {
  state = {
    notification: null,
    inputValue: null,
    objectType: "",
  };

  displayNotification = (type, msg, timeoutMs = 3000) => {
    clearTimeout(this._displayNotificationTimeoutId);
    this.setState({ notification: { type: type, msg: msg } });
    if (timeoutMs > 0) {
      this._displayNotificationTimeoutId = setTimeout(
        () => this.setState({ notification: null }),
        timeoutMs
      );
    }
  };

  onSubmit = () => {
    let json = {
      place: this.props.config.place,
      occupancyReset: this.state.inputValue,
      timestamp: new Date(Date.now()).toISOString(),
      objectType: this.state.objectType,
    };
    // Set UI message before making the post call
    // TODO: upgrade to latest bootstrap, use a small Spinner to show waiting
    this.displayNotification(
      this.NotificatonType.INFO,
      "Request sent. Waiting for Response...",
      -1
    );
    // Send request
    fetch(MetricsOccupancyResetUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(json),
    })
      .then((response) => readJson(response, "POST"))
      .then(() =>
        this.displayNotification(this.NotificatonType.SUCCESS, "Success")
      )
      .catch((err) => {
        this.displayNotification(
          this.NotificatonType.ERROR,
          "Failure [check console logs]"
        );
        console.error(
          this.LOG_TAG + err.message + " -> " + (err.response?.data?.error || err.message)
        );
      });
  };

  getValidationErrors = () => {
    const errors = [];
    if (this.state.objectType === "") {
      errors.push("No Object Type");
    }
    if (this.state.inputValue === null) {
      errors.push("No count");
    }
    return errors;
  };

  render() {
    let notificationComponent = null;
    const validationErrors = this.getValidationErrors();

    if (this.state.notification !== null) {
      let style;
      if (this.state.notification.type === this.NotificatonType.ERROR) {
        style = { color: "red" };
      } else if (
        this.state.notification.type === this.NotificatonType.SUCCESS
      ) {
        style = { color: "green" };
      } else if (
        this.state.notification.type === this.NotificatonType.WARNING
      ) {
        style = { color: "orange" };
      } else if (
        this.state.notification.type === this.NotificatonType.INFO
      ) {
        style = { color: "blue" };
      }

      notificationComponent = (
        <div className={CssClasses.notification} style={style}>
          {this.state.notification.msg}
        </div>
      );
    } else if (validationErrors.length > 0) {
      notificationComponent = (
        <div className={CssClasses.notification} style={{ color: "red" }}>
          {validationErrors.join(". ")}
        </div>
      );
    }

    return (
      <div className={CssClasses.mainContainer}>
        <label className={CssClasses.label} htmlFor="room-occupancy-object-type">
          Object Type:
        </label>
        <input
          id="room-occupancy-object-type"
          className={`${CssClasses.inputTextBox} ${this.state.objectType === "" ? CssClasses.inputError : ""}`}
          type="text"
          placeholder="e.g. Person"
          value={this.state.objectType}
          onChange={(event) => {
            this.setState({ objectType: event.target.value });
          }}
          required
        />
        <label className={CssClasses.label} htmlFor="room-occupancy-count">
          Count:
        </label>
        <input
          id="room-occupancy-count"
          className={`${CssClasses.inputTextBox} ${this.state.inputValue == null ? CssClasses.inputError : ""}`}
          type="text"
          title={InputTitle}
          placeholder={InputTitle}
          onChange={(event) => {
            if (!InputRegex.test(event.target.value)) {
              this.setState({ inputValue: null });
            } else {
              this.setState({ inputValue: parseInt(event.target.value) });
            }
          }}
          required
        />
        <button
          type="button"
          onClick={this.onSubmit.bind(this)}
          className={CssClasses.submitButton}
          disabled={validationErrors.length > 0}
          title={validationErrors.length > 0 ? validationErrors.join(". ") : ""}
        >
          Submit
        </button>
        {notificationComponent}
      </div>
    );
  }

  componentWillUnmount() {
    clearTimeout(this._displayNotificationTimeoutId);
  }
}

RoomOccupancySettingPanel.prototype.PANEL_TITLE = "Reset Room Occupancy";
RoomOccupancySettingPanel.prototype.LOG_TAG = "RoomOccupancySettingPanel: ";
RoomOccupancySettingPanel.prototype.NotificatonType = {
  SUCCESS: 1,
  ERROR: 2,
  WARNING: 3,
  INFO: 4,
};
