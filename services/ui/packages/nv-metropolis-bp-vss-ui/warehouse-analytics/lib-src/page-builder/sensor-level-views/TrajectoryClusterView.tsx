// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { connect } from "react-redux";

import {
  TrajectoryClustersVisualizer,
  DEFAULT_CARTESIAN_MAP_VIZ_CONTENTS_RENDER_UPDATE_FPS,
} from "common/warehouse";

import {
  CalibrationImageUrl,
  CalibrationImageMetadataUrl,
  ClusteringAddLabelUrl,
  ClusteringBehaviorUrl,
} from "common/warehouse";

const TrajectoryClusterView = (props) => {
  let sensorConfig = props.metadata.sensorsMap.get(
    props.uiState.selectedSensorId
  );

  return (
    <TrajectoryClustersVisualizer
      type="people"
      clusteringBehaviorEndpoint={ClusteringBehaviorUrl}
      clusterLabelUpdateEndpoint={ClusteringAddLabelUrl}
      sensorConfig={sensorConfig}
      floorPlanViewConfig={{
        endpointGetImageMetadata: CalibrationImageMetadataUrl,
        endpointGetSensorImage: CalibrationImageUrl,
      }}
      globalM2PxScaleFactor={props.metadata.m2PxScaleFactor}
      worldTranslationOffset={props.metadata.translationToGlobalCoordinates}
      mapVizContentsRenderUpdateFps={
        props.appConfig?.cartesianMapVizContentsRenderUpdateFps ??
        DEFAULT_CARTESIAN_MAP_VIZ_CONTENTS_RENDER_UPDATE_FPS
      }
    />
  );
};

export default connect((state) => ({
  appConfig: state.appConfig,
  metadata: state.metadata,
  uiState: state.uiState,
}))(TrajectoryClusterView);
