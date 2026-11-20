// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { OverlayView } from "@react-google-maps/api";

import CssClasses from "./SpeedInfoBox.module.css";

/**
 * Wraps a Google Maps OverlayView to display a Speed Info Box on the Map
 */
export default class SpeedInfoBox extends React.PureComponent {
  render() {
    return (
      <OverlayView
        mapPaneName={OverlayView.OVERLAY_MOUSE_TARGET}
        position={this.props.position}
      >
        <div
          className={CssClasses.speedInfoBox}
          title="Click to Close"
          onClick={() => this.props.onClick(this.props.id)}
          onMouseEnter={() => this.props.onMouseEnter()}
          onMouseLeave={() => this.props.onMouseLeave()}
        >
          <div
            className={CssClasses.speedInfoBoxFooterTextContainer}
          >
            mph
          </div>
          <div
            className={CssClasses.speedInfoBoxMainTextContainer}
          >
            {this.props.speed}
          </div>
        </div>
      </OverlayView>
    );
  }
}
