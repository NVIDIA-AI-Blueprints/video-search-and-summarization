// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import Styles from "./LightForm.module.css";

const pad = (n) => String(n).padStart(2, "0");

const toLocalInputValue = (date) => {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

const fromLocalInputValue = (value) => {
  const next = new Date(value);
  return Number.isNaN(next.getTime()) ? null : next;
};

class StartEndDateTimeSelector extends React.PureComponent {
  constructor(props) {
    super(props);
    this.state = {
      startDateTime: props.defaultStartDateTime || new Date(),
      endDateTime: props.defaultEndDateTime || new Date(),
      showComparisonError: false,
    };
  }

  onChangeStartDate = (date) => {
    if (!date) return;
    this.setState({ startDateTime: date, showComparisonError: false });
    this.props.onTimeRangeChanged &&
      this.props.onTimeRangeChanged(date, this.state.endDateTime);
  };

  onChangeEndDate = (date) => {
    if (!date) return;
    this.setState({ endDateTime: date, showComparisonError: false });
    this.props.onTimeRangeChanged &&
      this.props.onTimeRangeChanged(this.state.startDateTime, date);
  };

  compareDateTimes = () => {
    if (this.state.startDateTime >= this.state.endDateTime) {
      this.setState({ showComparisonError: true });
      return false;
    }
    if (this.state.showComparisonError) {
      this.setState({ showComparisonError: false });
    }
    return true;
  };

  onSubmit = () => {
    if (!this.compareDateTimes()) return;
    this.props.onTimeRangeSelected &&
      this.props.onTimeRangeSelected(
        this.state.startDateTime,
        this.state.endDateTime
      );
  };

  render() {
    return (
      <div className={Styles.panel}>
        {this.props.onClose ? (
          <div className={Styles.header}>
            <button
              type="button"
              className={Styles.close}
              onClick={this.props.onClose}
              aria-label="Close"
              title="Close"
            >
              ×
            </button>
          </div>
        ) : null}
        <label className={Styles.field}>
          <span className={Styles.label}>Start Time</span>
          <input
            className={Styles.input}
            type="datetime-local"
            step="1"
            value={toLocalInputValue(this.state.startDateTime)}
            max={this.props.disableFuture ? toLocalInputValue(new Date()) : undefined}
            onChange={(event) => {
              const next = fromLocalInputValue(event.target.value);
              if (next) this.onChangeStartDate(next);
            }}
          />
        </label>
        <label className={Styles.field}>
          <span className={Styles.label}>End Time</span>
          <input
            className={Styles.input}
            type="datetime-local"
            step="1"
            value={toLocalInputValue(this.state.endDateTime)}
            max={this.props.disableFuture ? toLocalInputValue(new Date()) : undefined}
            onChange={(event) => {
              const next = fromLocalInputValue(event.target.value);
              if (next) this.onChangeEndDate(next);
            }}
          />
        </label>
        {this.state.showComparisonError ? (
          <div className={Styles.error}>Start time must be before end time</div>
        ) : null}
        {this.props.onTimeRangeSelected !== undefined ? (
          <button type="button" className={Styles.apply} onClick={this.onSubmit}>
            Apply
          </button>
        ) : null}
      </div>
    );
  }
}

export default StartEndDateTimeSelector;
