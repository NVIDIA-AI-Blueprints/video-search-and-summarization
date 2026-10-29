// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import { Button, DateTimePicker, IconButton } from "common/warehouse/mui";
import Styles from "./LightForm.module.css";

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
            <IconButton
              className={Styles.close}
              onClick={this.props.onClose}
              aria-label="Close"
              title="Close"
            >
              ×
            </IconButton>
          </div>
        ) : null}
        <DateTimePicker
          className={Styles.field}
          label="Start Time"
          value={this.state.startDateTime}
          disableFuture={this.props.disableFuture}
          onChange={this.onChangeStartDate}
        />
        <DateTimePicker
          className={Styles.field}
          label="End Time"
          value={this.state.endDateTime}
          disableFuture={this.props.disableFuture}
          onChange={this.onChangeEndDate}
        />
        {this.state.showComparisonError ? (
          <div className={Styles.error}>Start time must be before end time</div>
        ) : null}
        {this.props.onTimeRangeSelected !== undefined ? (
          <Button variant="contained" size="small" className={Styles.apply} onClick={this.onSubmit}>
            Apply
          </Button>
        ) : null}
      </div>
    );
  }
}

export default StartEndDateTimeSelector;
