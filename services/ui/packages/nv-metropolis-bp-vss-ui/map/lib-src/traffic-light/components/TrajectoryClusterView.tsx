// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";

import {
  ClusteringAddLabelUrl,
  ClusteringBehaviorUrl,
  getEnv,
  connect,
  TrajectoryClustersVisualizer,
} from "common/map";

import Styles from "./TrajectoryClusterView.module.css";

const TrajectoryClusterView = (props) => {
  return (
    <div className={Styles.container}>
      <TrajectoryClustersVisualizer
        type="vehicles"
        clusteringBehaviorEndpoint={ClusteringBehaviorUrl}
        clusterLabelUpdateEndpoint={ClusteringAddLabelUrl}
        sensorConfig={props.metadata.sensorsMap.get(
          props.uiState.selectedSensorId
        )}
        mapsApiKey={getEnv("REACT_APP_GOOGLE_MAPS_API_KEY")}
        mapOptions={{
          defaultZoom: props.appConfig.mapOptions.intersection.zoom.default,
          minZoom: props.appConfig.mapOptions.intersection.zoom.min,
          maxZoom: props.appConfig.mapOptions.intersection.zoom.max,
        }}
        top="0"
        left="0.6%"
        width="98.8%"
        height="100%"
      />
    </div>
  );
};

export default connect((state) => ({
  appConfig: state.appConfig,
  metadata: state.metadata,
  uiState: state.uiState,
}))(TrajectoryClusterView);
