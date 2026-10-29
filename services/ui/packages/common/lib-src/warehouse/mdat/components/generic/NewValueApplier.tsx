// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import { Button, IconButton } from "common/warehouse/mui";
import Styles from "./LightForm.module.css";

class NewValueApplier extends React.PureComponent {
  state = {
    showError: "",
  };

  value = this.props.defaultValue;

  onValueChanged = (value) => {
    if (this.props.validateValue) {
      const result = this.props.validateValue(value);
      if (result !== true) {
        this.setState({ showError: result });
        return;
      }
      this.setState({ showError: "" });
      this.value = value;
      return;
    }
    this.value = value;
  };

  onSubmit = () => {
    this.props.onNewValueSelected(this.value);
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
        <label className={Styles.field}>
          <span className={Styles.label}>{this.props.label}</span>
          <input
            className={`${Styles.input} ${this.state.showError ? Styles.inputError : ""}`}
            type="text"
            defaultValue={this.props.defaultValue}
            autoFocus={this.props.autoFocus !== false}
            onChange={(event) => this.onValueChanged(event.target.value)}
          />
        </label>
        {this.state.showError ? (
          <div className={Styles.error}>{this.state.showError}</div>
        ) : null}
        <Button
          variant="contained"
          size="small"
          className={Styles.apply}
          onClick={this.onSubmit}
          disabled={this.state.showError !== ""}
        >
          Apply
        </Button>
        {this.props.infoString ? (
          <div className={Styles.info}>{this.props.infoString}</div>
        ) : null}
      </div>
    );
  }
}

export default NewValueApplier;
