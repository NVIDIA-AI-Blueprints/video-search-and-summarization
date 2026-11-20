// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { OverlayView } from "@react-google-maps/api";

import CssClasses from "./GoogleMapsToolTip.module.css";

/**
 * Wraps a Google Maps OverlayView to display a Tool tip on the Map
 * Note: PureComponent makes it more efficient to render - if props
 *       don't change, render isn't called.
 */
const GoogleMapsToolTip = (props) => {
  return (
    <OverlayView mapPaneName={OverlayView.FLOAT_PANE} position={props.position}>
      <div className={CssClasses.toolTip}>{props.text}</div>
    </OverlayView>
  );
};

export default GoogleMapsToolTip;
