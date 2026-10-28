// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { Axios } from "common/warehouse";
import React, { Fragment } from "react";
import { Line, Rect, Circle, Text, Group } from "react-konva";

import CameraView from "./../CameraView";
import VizCameraIcon from "./../VizCameraIcon";
import UserMessage from "./../../../utils/UserMessage";
import {
  createLayoutThrottledTask,
  shouldRefreshCanvasDisplaySnapshotFromFps,
} from "./../../../utils/cartesianMapVizDisplayHelpers";

import Styles from "./PeopleTrajectoryClustersFloorMap.module.css";
import VizTooltip from "../../generic/VizTooltip";

/**
 * Fingerprint for `props.data` (cluster array) in geometry cache keys.
 * Must reflect full payload: coercing elements with `String()` or putting raw objects in `.join("|")`
 * yields "[object Object]" and breaks invalidation. Heuristics like `length + data[0].clusterIndex`
 * also collide when two datasets share count / first index but differ elsewhere.
 */
function trajectoryDataFingerprintForCacheKey(data) {
  if (data == null) {
    return "";
  }
  try {
    return JSON.stringify(data);
  } catch (e) {
    console.warn(
      "PeopleTrajectoryClustersFloorMap: props.data is not JSON-serializable for cache key",
      e
    );
    return `__non_serializable__:${Array.isArray(data) ? data.length : typeof data}`;
  }
}

/**
 * Cache key for trajectory geometry + data fingerprint.
 * Excludes visibility (keyClusterFocused, invisibleClusterIdsSet) so Group.visible toggles without rebuild.
 * `coordinateConverterEpoch` bumps when world2CanvasCoordinateConverter is refreshed so cached
 * clusterGroups are not reused while props.data/scale look unchanged (convert() output may differ).
 *
 * @param {string} dataFingerprint - Memoized JSON fingerprint for `props.data` (see component).
 */
function trajectoryCacheKeyGeometry(
  props,
  scaleFactor,
  coordinateConverterEpoch = 0,
  dataFingerprint
) {
  const worldTranslationOffset =
    props.worldTranslationOffset || { x: 0, y: 0 };
  return [
    dataFingerprint,
    worldTranslationOffset.x,
    worldTranslationOffset.y,
    props.globalM2PxScaleFactor,
    scaleFactor,
    coordinateConverterEpoch,
  ].join("|");
}

class PeopleTrajectoryClustersFloorMap extends React.PureComponent {
  state = {
    /** From CameraView metadata: floor-plan camera icon only for plan-view, not warped-camera-view. */
    mapCalibrationViewType: null,
  };

  _trajectoryCache = { key: null, clusterGroups: null };

  /** Display snapshot: trajectory geometry throttled by mapVizContentsRenderUpdateFps. */
  _displayClusterGroups = null;
  _lastDisplayTrajectoryUpdateTime = 0;

  /** Konva Group per cluster (one layer per cluster); used for per-group cache() on unfocus. */
  _clusterGroupRefs = new Map();

  /** Geometry + legend visibility fingerprint; clears raster cache when it changes. */
  _allClustersCacheKey = null;
  _lastAllClustersCacheKey = null;

  /** Incremented on each CameraView onRefreshCoordinateConverters so trajectory cache keys change. */
  _coordinateConverterCacheEpoch = 0;

  /**
   * Reuses `JSON.stringify(props.data)` when `props.data` is the same reference (avoids work each render).
   * If the parent mutates the array in place, bump the reference or fingerprint can be stale.
   */
  _trajectoryDataFingerprintCache = { dataRef: null, fingerprint: "" };

  _layoutTrajectoryThrottle = createLayoutThrottledTask({
    onBeforeSchedule: () => {
      this._trajectoryCache = { key: null, clusterGroups: null };
      this._displayClusterGroups = null;
    },
    task: () => this.forceUpdate(),
  });

  _scheduleTrajectoryUpdate = () => this._layoutTrajectoryThrottle.schedule();

  _onRefreshCoordinateConverters = () => {
    this._coordinateConverterCacheEpoch += 1;
    this._scheduleTrajectoryUpdate();
  };

  _onCalibrationViewTypeResolved = (viewType) => {
    this.setState({ mapCalibrationViewType: viewType });
  };

  /**
   * Floor-plan camera glyph + FOV for the clustering sensor when the widget loads plan-view.
   */
  _renderSensorCameraIconForFloorPlan() {
    const sensorConfig = this.props.sensorConfig;
    if (
      !sensorConfig ||
      sensorConfig.type !== "camera" ||
      !sensorConfig.coordinates ||
      this.state.mapCalibrationViewType !== "plan-view" ||
      !this.props.globalM2PxScaleFactor ||
      !this.cameraViewRef?.topViewRef?.world2CanvasCoordinateConverter ||
      !this.cameraViewRef?.topViewRef?.scaleFactor
    ) {
      return null;
    }
    const worldTranslationOffset = this.props.worldTranslationOffset || {
      x: 0,
      y: 0,
    };
    return (
      <VizCameraIcon
        key={sensorConfig.id}
        cameraConfig={sensorConfig}
        scaleFactor={this.cameraViewRef.topViewRef.scaleFactor}
        iconScaleMultiplier={2.25}
        m2PxScaleFactor={this.props.globalM2PxScaleFactor}
        worldTranslationOffset={worldTranslationOffset}
        coordinateConverter={
          this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter
        }
      />
    );
  }

  _getMemoizedTrajectoryDataFingerprint() {
    const data = this.props.data;
    if (data == null) {
      return "";
    }
    const cache = this._trajectoryDataFingerprintCache;
    if (cache.dataRef === data) {
      return cache.fingerprint;
    }
    const fingerprint = trajectoryDataFingerprintForCacheKey(data);
    this._trajectoryDataFingerprintCache = { dataRef: data, fingerprint };
    return fingerprint;
  }

  _clientPositionFromDomEvent(evt) {
    if (!evt) return null;
    if (typeof evt.clientX === "number" && typeof evt.clientY === "number") {
      return { x: evt.clientX, y: evt.clientY };
    }
    const touch = evt.touches?.[0] ?? evt.changedTouches?.[0];
    if (touch && typeof touch.clientX === "number") {
      return { x: touch.clientX, y: touch.clientY };
    }
    return null;
  }

  _clientPositionFromStagePointer(stage, containerEl) {
    const pointerPos = stage.getPointerPosition();
    if (!pointerPos || (pointerPos.x === 0 && pointerPos.y === 0)) return null;
    const rect = containerEl.getBoundingClientRect();
    const stageW = stage.width();
    const stageH = stage.height();
    if (stageW <= 0 || stageH <= 0) return null;
    return {
      x: rect.left + (pointerPos.x / stageW) * rect.width,
      y: rect.top + (pointerPos.y / stageH) * rect.height,
    };
  }

  onMouseOverTrajectoryCluster = (event, cluster) => {
    const stage = event.currentTarget.getStage();
    if (!stage) {
      return;
    }

    const containerEl = stage.container();
    const clientPosition =
      this._clientPositionFromDomEvent(event.evt) ||
      (containerEl && this._clientPositionFromStagePointer(stage, containerEl));

    if (!clientPosition) {
      return;
    }

    this.setState({
      clusterToolTipEl: (
        <VizTooltip
          clientPosition={clientPosition}
          text={
            "(Double Click to update label for the cluster)\n" +
            "\nCluster Index: " +
            cluster.clusterIndex +
            "\nCluster Label: " +
            (cluster.label || "") +
            "\nCluster Model Version: " +
            cluster.modelVersion +
            "\nTrajectories Count: " +
            cluster.count
          }
          containerRef={containerEl}
        />
      ),
    });

    this.props.onMouseOverTrajectoryCluster &&
      this.props.onMouseOverTrajectoryCluster(event, cluster.clusterIndex);
  };

  onMouseOutTrajectoryCluster = (e) => {
    this.state.clusterToolTipEl &&
      this.setState({
        clusterToolTipEl: undefined,
      });

    this.props.onMouseOutTrajectoryCluster &&
      this.props.onMouseOutTrajectoryCluster(e);
  };

  /**
   * Handle double click on cluster for label update
   */
  _handleClusterLabelUpdateRequest = (cluster) => {
    this.props.onClusterLabelUpdateRequest &&
      this.props.onClusterLabelUpdateRequest(cluster);
  };

  /**
   * Process a single coordinate pair and add to path
   */
  _processCoordinate(
    pair,
    coordinateIndex,
    coordinatesLength,
    worldTranslationOffset,
    globalM2PxScaleFactor,
    path
  ) {
    const canvasCoord =
      this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter.convert({
        x: (pair[0] + worldTranslationOffset.x) * globalM2PxScaleFactor,
        y: (pair[1] + worldTranslationOffset.y) * globalM2PxScaleFactor,
      });
    path.push(canvasCoord.x);
    path.push(canvasCoord.y);
    return canvasCoord;
  }

  /**
   * Halo width in pixels (scale-independent) for outline visibility on dark backgrounds
   */
  _haloExtra = 0.05;

  /**
   * Create circle element for trajectory coordinate (white halo behind for visibility)
   */
  _createTrajectoryCircle(
    cluster,
    trajectoryBehavior,
    coordinateIndex,
    canvasCoord,
    coordinatesLength
  ) {
    const scaleFactor = this.cameraViewRef.topViewRef.scaleFactor;
    const isLast = coordinateIndex === coordinatesLength - 1;
    const isFirst = coordinateIndex === 0;
    // Radii and strokes are in stage pixels (one factor of scaleFactor). Do not also set Circle scale={{ scaleFactor }}
    // or the main dot renders at base * scaleFactor² and misaligns the halo.
    const radius = (isLast ? 40 : isFirst ? 20 : 1) * scaleFactor;
    const trajectoryRingWidth = 8 * scaleFactor;
    const whiteStrokeWidth = 1;
    const whiteOutlineRadius = radius + trajectoryRingWidth / 2 + whiteStrokeWidth / 2;
    const haloRadius = whiteOutlineRadius + whiteStrokeWidth / 2 + this._haloExtra;
    const haloStrokeWidth = whiteStrokeWidth;

    const handleMouseOver = (e) => this.onMouseOverTrajectoryCluster(e, cluster);
    const handleMouseOut = (e) => this.onMouseOutTrajectoryCluster(e);
    const handleDblClick = () => this._handleClusterLabelUpdateRequest(cluster);

    return (
      <Fragment key={`${cluster.clusterIndex}-${trajectoryBehavior.Id}-${coordinateIndex}-dot`}>
        <Circle
          x={canvasCoord.x}
          y={canvasCoord.y}
          radius={haloRadius}
          stroke="white"
          strokeWidth={haloStrokeWidth}
          listening={false}
          perfectDrawEnabled={false}
        />
        <Circle
          x={canvasCoord.x}
          y={canvasCoord.y}
          radius={radius}
          fill="transparent"
          stroke={cluster.color}
          strokeWidth={trajectoryRingWidth}
          perfectDrawEnabled={false}
          onMouseOver={handleMouseOver}
          onMouseOut={handleMouseOut}
          onDblClick={handleDblClick}
        />
        <Circle
          x={canvasCoord.x}
          y={canvasCoord.y}
          radius={whiteOutlineRadius}
          fill="transparent"
          stroke="white"
          strokeWidth={whiteStrokeWidth}
          perfectDrawEnabled={false}
          listening={false}
        />
      </Fragment>
    );
  }

  /**
   * Process a single trajectory behavior and add elements
   */
  _processTrajectoryBehavior(
    cluster,
    trajectoryBehavior,
    trajectoryArrayIndex,
    worldTranslationOffset,
    globalM2PxScaleFactor,
    trajectoryElements
  ) {
    const path = [];
    const coordinates = trajectoryBehavior.locations.coordinates;
    const coordinatesLength = coordinates.length;

    coordinates.forEach((pair, coordinateIndex) => {
      const canvasCoord = this._processCoordinate(
        pair,
        coordinateIndex,
        coordinatesLength,
        worldTranslationOffset,
        globalM2PxScaleFactor,
        path
      );

      if (
        coordinateIndex === coordinatesLength - 1 ||
        coordinateIndex === 0
      ) {
        trajectoryElements.push(
          this._createTrajectoryCircle(
            cluster,
            trajectoryBehavior,
            coordinateIndex,
            canvasCoord,
            coordinatesLength
          )
        );
      }
    });

    const handleLineMouseOver = (e) =>
      this.onMouseOverTrajectoryCluster(e, cluster);
    const handleLineMouseOut = (e) => this.onMouseOutTrajectoryCluster(e);
    const handleLineDblClick = () => this._handleClusterLabelUpdateRequest(cluster);

    const scaleFactor = this.cameraViewRef.topViewRef.scaleFactor;
    const lineStrokeWidth = 5 * scaleFactor;
    const haloStrokeWidth = lineStrokeWidth + Math.max(1, this._haloExtra * 2);

    trajectoryElements.push(
      <Line
        key={"" + cluster.clusterIndex + trajectoryBehavior.Id + "-halo"}
        points={path}
        closed={false}
        tension={2}
        stroke="white"
        strokeWidth={haloStrokeWidth}
        listening={false}
        perfectDrawEnabled={false}
      />
    );
    trajectoryElements.push(
      <Line
        key={"" + cluster.clusterIndex + trajectoryBehavior.Id}
        points={path}
        closed={false}
        tension={2}
        stroke={cluster.color}
        strokeWidth={lineStrokeWidth}
        perfectDrawEnabled={false}
        onMouseOver={handleLineMouseOver}
        onMouseOut={handleLineMouseOut}
        onDblClick={handleLineDblClick}
      />
    );
  }

  /**
   * Add cluster trajectories to trajectory elements array
   */
  _addClusterToTrajectoryElements(
    cluster,
    worldTranslationOffset,
    globalM2PxScaleFactor,
    trajectoryElements
  ) {
    if (!cluster.sampledBehaviors) {
      return;
    }

    cluster.sampledBehaviors.forEach((trajectoryBehavior, trajectoryArrayIndex) => {
      this._processTrajectoryBehavior(
        cluster,
        trajectoryBehavior,
        trajectoryArrayIndex,
        worldTranslationOffset,
        globalM2PxScaleFactor,
        trajectoryElements
      );
    });
  }

  _buildClusterGroups(scaleFactor, worldTranslationOffset) {
    const dataFingerprint = this._getMemoizedTrajectoryDataFingerprint();
    const key = trajectoryCacheKeyGeometry(
      this.props,
      scaleFactor,
      this._coordinateConverterCacheEpoch,
      dataFingerprint
    );
    let clusterGroups =
      this._trajectoryCache.key === key && this._trajectoryCache.clusterGroups
        ? this._trajectoryCache.clusterGroups
        : null;

    if (!clusterGroups && this.props.data) {
      clusterGroups = this.props.data.map((cluster) => {
        const elements = [];
        this._addClusterToTrajectoryElements(
          cluster,
          worldTranslationOffset,
          this.props.globalM2PxScaleFactor,
          elements
        );
        return { clusterIndex: cluster.clusterIndex, elements };
      });
      this._trajectoryCache = { key, clusterGroups };
    }

    if (!this.props.data) {
      this._displayClusterGroups = null;
    }
    if (!clusterGroups) {
      this._allClustersCacheKey = null;
    }
    return { key, clusterGroups };
  }

  _buildTrajectoryLayers(key, clusterGroups) {
    const now = Date.now();
    if (
      shouldRefreshCanvasDisplaySnapshotFromFps(
        this.props.mapVizContentsRenderUpdateFps,
        this._displayClusterGroups != null,
        now,
        this._lastDisplayTrajectoryUpdateTime
      )
    ) {
      this._displayClusterGroups = clusterGroups;
      this._lastDisplayTrajectoryUpdateTime = now;
    }
    const keyClusterFocused = this.props.keyClusterFocused;
    const invisibleSet = this.props.invisibleClusterIdsSet || new Set();
    this._allClustersCacheKey =
      key +
      "|invisible:" +
      [...invisibleSet].sort((a, b) => a - b).join(",");
    const isVisible = (clusterIndex) =>
      !invisibleSet.has(clusterIndex) &&
      (keyClusterFocused === null || keyClusterFocused === clusterIndex);
    const ordered =
      keyClusterFocused === null
        ? this._displayClusterGroups
        : [
            ...this._displayClusterGroups.filter(
              (g) => g.clusterIndex !== keyClusterFocused
            ),
            ...this._displayClusterGroups.filter(
              (g) => g.clusterIndex === keyClusterFocused
            ),
          ];
    return ordered.map(({ clusterIndex, elements }) => ({
      name: `cluster-${clusterIndex}`,
      content: (
        <Group
          key={clusterIndex}
          ref={(node) => {
            if (node) {
              this._clusterGroupRefs.set(clusterIndex, node);
            } else {
              this._clusterGroupRefs.delete(clusterIndex);
            }
          }}
          visible={isVisible(clusterIndex)}
          listening={isVisible(clusterIndex)}
        >
          {elements}
        </Group>
      ),
    }));
  }

  render() {
    let trajectoryLayers = null;
    const worldTranslationOffset =
      this.props.worldTranslationOffset || { x: 0, y: 0 };

    const scaleFactor = this.cameraViewRef?.topViewRef?.scaleFactor;
    const canBuild =
      this.props.globalM2PxScaleFactor &&
      this.cameraViewRef?.topViewRef?.world2CanvasCoordinateConverter &&
      scaleFactor;

    if (canBuild) {
      const { key, clusterGroups } = this._buildClusterGroups(
        scaleFactor,
        worldTranslationOffset
      );
      if (clusterGroups) {
        trajectoryLayers = this._buildTrajectoryLayers(key, clusterGroups);
      }
    }

    return (
      <>
        <div
          ref={(ref) => (this.cameraViewContainerRef = ref)}
          className={Styles.cameraViewContainer}
        >
          {this.props.globalM2PxScaleFactor ? (
            <CameraView
              ref={(ref) => (this.cameraViewRef = ref)}
              endpointGetCameraImagesMetadata={
                this.props.floorPlanViewConfig.endpointGetImageMetadata
              }
              endpointGetCameraViewImage={
                this.props.floorPlanViewConfig.endpointGetSensorImage
              }
              cameraConfig={this.props.sensorConfig}
              onResize={this._scheduleTrajectoryUpdate}
              onRefreshCoordinateConverters={this._onRefreshCoordinateConverters}
              onCalibrationViewTypeResolved={
                this._onCalibrationViewTypeResolved
              }
              trajectoryLayers={trajectoryLayers}
            >
              {this._renderSensorCameraIconForFloorPlan()}
            </CameraView>
          ) : (
            "Missing Global Meters:Pixels scale factor"
          )}
        </div>
        {this.state.clusterToolTipEl}
      </>
    );
  }

  componentDidUpdate(prevProps) {
    const cacheKey = this._allClustersCacheKey;
    if (cacheKey != null && cacheKey !== this._lastAllClustersCacheKey) {
      this._clusterGroupRefs.forEach((node) => {
        node.clearCache();
      });
      this._lastAllClustersCacheKey = cacheKey;
    }

    const justUnfocused =
      prevProps.keyClusterFocused !== null &&
      this.props.keyClusterFocused === null;
    if (justUnfocused && this._clusterGroupRefs.size > 0) {
      const firstNode = this._clusterGroupRefs.values().next().value;
      const stage = firstNode && firstNode.getStage && firstNode.getStage();
      if (stage) {
        stage.batchDraw();
      }
      requestAnimationFrame(() => {
        this._clusterGroupRefs.forEach((node) => {
          if (node.visible()) {
            node.cache();
          }
        });
      });
    }
  }

  componentWillUnmount() {
    this._layoutTrajectoryThrottle.dispose();
  }
}

export default PeopleTrajectoryClustersFloorMap;
