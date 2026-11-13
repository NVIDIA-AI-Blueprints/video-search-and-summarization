// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Paper } from "common/map/mui";

import TrajectoryClustersGraph from "./TrajectoryClustersGraph";
import VehiclesTrajectoryClustersGeoMap from "./VehiclesTrajectoryClustersGeoMap";
import PeopleTrajectoryClustersFloorMap from "./PeopleTrajectoryClustersFloorMap";

import Styles from "./TrajectoryClustersPlot.module.css";

/**
 * Graphic view for Vehicles or People Trajectory Clusters
 */
class TrajectoryClustersPlot extends React.PureComponent {
  constructor(props) {
    super(props);

    this.state = {
      isClusterLabelsContainerOpen: true,
      keyClusterFocused: null,
      invisibleClustersSize: 0,
    };
    this.invisibleClusterIdsSet = new Set();
  }

  onClickclusterLabelsContainerBtn = () => {
    this.setState({
      isClusterLabelsContainerOpen: !this.state.isClusterLabelsContainerOpen,
    });
  };

  onMouseOverTrajectoryClusterOnMapView = (mouseEvent, trajectoryClusterId) => {
    this.setState({ keyClusterFocused: trajectoryClusterId });
  };

  onMouseOutTrajectoryClusterOnMapView = (mouseEvent) => {
    this.setState({ keyClusterFocused: null });
  };

  onMouseOverClusterBar = (data) => {
    if (!this.invisibleClusterIdsSet.has(data.clusterIndex)) {
      this.setState({ keyClusterFocused: data.clusterIndex });
    }
  };

  onMouseOutClusterBar = () => {
    this.setState({ keyClusterFocused: null });
  };

  onClusterBarClick = (data) => {
    if (this.invisibleClusterIdsSet.has(data.clusterIndex)) {
      this.invisibleClusterIdsSet.delete(data.clusterIndex);
      this.setState({
        invisibleClustersSize: this.state.invisibleClustersSize - 1,
      });
    } else {
      this.invisibleClusterIdsSet.add(data.clusterIndex);
      this.setState({
        invisibleClustersSize: this.state.invisibleClustersSize + 1,
      });
    }
  };

  UNSAFE_componentWillReceiveProps(nextProps) {
    if (nextProps.data !== this.props.data) {
      this.invisibleClusterIdsSet = new Set();
      this.setState({ invisibleClustersSize: 0 });
    }
  }

  render() {
    let graphEl = (
      <Paper
        elevation={2}
        className={Styles.graphContainer}
        // TODO: not working in css class
        style={{ backgroundColor: "#edfcd2" }}
      >
        <TrajectoryClustersGraph
          data={this.props.data}
          keyClusterFocused={this.state.keyClusterFocused}
          invisibleClusterIdsSet={this.invisibleClusterIdsSet}
          onClusterBarMouseOver={this.onMouseOverClusterBar.bind(this)}
          onClusterBarMouseOut={this.onMouseOutClusterBar.bind(this)}
          onClusterBarClick={this.onClusterBarClick.bind(this)}
        />
      </Paper>
    );

    let mapView;
    if (this.props.type === "vehicles") {
      mapView = (
        <VehiclesTrajectoryClustersGeoMap
          {...this.props}
          invisibleClusterIdsSet={this.invisibleClusterIdsSet}
          keyClusterFocused={this.state.keyClusterFocused}
          onMouseOverTrajectoryCluster={
            this.onMouseOverTrajectoryClusterOnMapView
          }
          onMouseOutTrajectoryCluster={
            this.onMouseOutTrajectoryClusterOnMapView
          }
          onClusterLabelUpdateRequest={this.props.onClusterLabelUpdateRequest}
        />
      );
    } else if (this.props.type === "people") {
      mapView = (
        <PeopleTrajectoryClustersFloorMap
          {...this.props}
          invisibleClusterIdsSet={this.invisibleClusterIdsSet}
          keyClusterFocused={this.state.keyClusterFocused}
          onMouseOverTrajectoryCluster={
            this.onMouseOverTrajectoryClusterOnMapView
          }
          onMouseOutTrajectoryCluster={
            this.onMouseOutTrajectoryClusterOnMapView
          }
          onClusterLabelUpdateRequest={this.props.onClusterLabelUpdateRequest}
        />
      );
    }

    return (
      <React.Fragment>
        {mapView}
        {graphEl}
      </React.Fragment>
    );
  }
}

export default TrajectoryClustersPlot;
