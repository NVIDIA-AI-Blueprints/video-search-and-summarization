// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import CssClasses from "./../styles/PlaceHeaderContent.module.css";

class Timer extends React.PureComponent {
  componentDidMount() {
    // Update timer value every second
    this.renderIntervalTimerId = setInterval(() => {
      this.forceUpdate();
    }, 1000);
  }

  render() {
    let timeString = "\u00A0\u00A0\u00A0\u00A0 -- \u00A0\u00A0\u00A0\u00A0";
    let currentTimeMs =
      this.props.currentTimeMs !== undefined
        ? this.props.currentTimeMs
        : Date.now();
    if (this.props.uiDelaySeconds) {
      currentTimeMs -= this.props.uiDelaySeconds * 1000;
    }

    if (currentTimeMs >= 0) {
      // Valid input time
      timeString = new Date(currentTimeMs).toLocaleString();
    }

    return (
      <div className={CssClasses.timer} title="Current Applicaiton Time">
        {timeString}
      </div>
    );
  }

  componentWillUnmount() {
    // Clear Interval timer
    clearInterval(this.renderIntervalTimerId);
  }
}

/**
 * UI content for Generic PlaceHeader Bar
 */
const PlaceHeaderContent = (props) => {
  return (
    <React.Fragment>
      <div
        className={CssClasses.titleContainer}
        onClick={props.onClickTitle}
        title={
          props.placeName +
          (props.showTooltip ? "\nClick to go back to previous view" : "")
        }
        style={{ cursor: props.showTooltip ? "pointer" : "initial" }}
      >
        <div
          className={CssClasses.titleText}
          title={
            props.placeName +
            (props.showTooltip ? "\nClick to go back to previous view" : "")
          }
        >
          {props.placeName}
        </div>
      </div>
      <div className={CssClasses.middleItems}>{props.children}</div>
      <Timer {...props} />
    </React.Fragment>
  );
};

export default PlaceHeaderContent;
