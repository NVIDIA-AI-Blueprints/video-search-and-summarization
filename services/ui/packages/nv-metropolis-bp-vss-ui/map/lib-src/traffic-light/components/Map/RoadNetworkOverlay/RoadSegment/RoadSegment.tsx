// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Polyline } from "@react-google-maps/api";

/**
 * Wraps a Google Maps Polyline to display a Road Link on the Map
 */
export default class RoadSegment extends React.PureComponent {
  render() {
    return (
      <Polyline
        path={this.props.segmentLocations}
        geodesic={true}
        options={{
          strokeColor: this.props.segmentColor,
          strokeWeight: this.props.segmentStrokeWeight,
          clickable: true,
          zIndex: 2,
        }}
        onClick={(e) => this.props.onClick(e, this.props.id)}
        onMouseOver={(e) => this.props.onMouseOver(e, this.props.id)}
        onMouseOut={(e) => this.props.onMouseOut(e, this.props.id)}
      />
    );
  }
}
