// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import {
  Close as CloseIcon,
  Minimize as MinimizeIcon,
} from "common/warehouse/mui-icons";

import Styles from "./Window.module.css";

const iconStyle = { color: "white", cursor: "pointer" };

/**
 * Standard wrapping Window for Widgets.
 */
export default class Window extends React.PureComponent {
  render() {
    return (
      <div className={Styles.main}>
        <div className={Styles.headerBar}>
          <div className={Styles.headerBarMain}>
            {this.props.headerStart ? (
              <div className={Styles.headerStart}>{this.props.headerStart}</div>
            ) : null}
            <div className={Styles.headerTitle} title={this.props.titleText}>
              {this.props.titleText}
            </div>
            {(this.props.onClose || this.props.onMinimize) && (
              <div className={Styles.headerActions}>
                {this.props.onMinimize ? (
                  <MinimizeIcon
                    style={iconStyle}
                    onClick={(e) => this.props.onMinimize(e)}
                  />
                ) : null}
                {this.props.onClose ? (
                  <CloseIcon
                    style={iconStyle}
                    onClick={(e) => this.props.onClose(e)}
                  />
                ) : null}
              </div>
            )}
          </div>
          {this.props.subTitleText !== undefined &&
          this.props.subTitleText !== "" ? (
            <div
              className={Styles.headerBarSub}
              title={this.props.subTitleText}
            >
              {this.props.subTitleText}
            </div>
          ) : null}
          <div
            className={
              this.props.overflowHidden
                ? `${Styles.contents} ${Styles.contentsClip}`
                : Styles.contents
            }
          >
            {this.props.children}
          </div>
        </div>
      </div>
    );
  }
}
