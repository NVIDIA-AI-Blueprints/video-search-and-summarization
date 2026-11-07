// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import CssClasses from "./LiveSwitchButton.module.css";

export default class LiveSwitchButton extends React.PureComponent {
  constructor(props) {
    super(props);

    this.state = {
      isLive: props.defaultIsLive === "undefined" ? true : props.defaultIsLive,
    };
  }

  onClick = (e) => {
    if (this.props.clickable !== undefined && this.props.clickable === false) {
      return;
    }
    this.switchState();
    this.props.onClick && this.props.onClick(e, this.state);
  };

  switchState = () => {
    this.setState({ isLive: !this.state.isLive });
  };

  UNSAFE_componentWillUpdate(nextProps, nextState) {
    if (this.state !== nextState) {
      this.props.onStateChange && this.props.onStateChange(nextState);
    }
  }

  render() {
    return (
      <div
        className={CssClasses.container}
        onClick={this.onClick.bind(this)}
        title={this.props.tooltip}
        style={
          this.props.clickable !== false
            ? {
                cursor: "pointer",
              }
            : {
                pointerEvents: "none",
              }
        }
      >
        {this.state.isLive ? (
          <div className={CssClasses.liveIcon}>
            <div className={CssClasses.liveIconCircleInner} />
          </div>
        ) : null}
        <div
          className={
            this.state.isLive ? CssClasses.textLive : CssClasses.textNotLive
          }
        >
          LIVE
        </div>
      </div>
    );
  }
}
