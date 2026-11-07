// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { joinUrl, isAbortError, readJson } from "common/warehouse";
import React from "react";
import { Circle } from "react-konva";
import { Portal } from "react-konva-utils";
import { connect } from "react-redux";

import {
  CameraView,
  Widget,
  UserMessage,
  Window,
  ColorSet,
  TripwireLine,
  RoiLine,
  VizCameraIcon,
  getCartesianMapVizCanvasThrottleMsFromAppConfig,
  shouldRefreshCanvasDisplaySnapshotFromAppConfig,
  createThrottledObjectsLocationState,
  createLayoutThrottledTask,
  createForceUpdateLayoutThrottle,
} from "common/warehouse";

import {
  AppLogsConsoleReduxUtil,
  AppConstants,
  ReduxUtils,
  CalibrationImageUrl,
  CalibrationImageMetadataUrl,
  FramesEnhancedUrl,
} from "common/warehouse";

import UiStateReduxUtil from "../utils/UiStateReduxUtil";

const DEFAULT_PROXIMITY_DETECTION_CALL_TIME_INTERVAL_MS = 100;

class LivePeopleMovementDataVisualizer extends React.PureComponent {
  state = {
    /** From CameraView metadata: show floor-plan camera icon only for plan-view, not warped-camera-view. */
    mapCalibrationViewType: null,
  };

  /** Snapshot of converted dot data for drawing; throttled by cartesianMapVizContentsRenderUpdateFps. */
  _displayDotsSnapshot = null;
  _lastDisplayDotsUpdateTime = 0;

  _throttledObjectsLocation = createThrottledObjectsLocationState({
    getThrottleMs: () =>
      getCartesianMapVizCanvasThrottleMsFromAppConfig(this.props.appConfig),
    setState: (partial) => this.setState(partial),
  });

  _layoutResizeThrottle = createForceUpdateLayoutThrottle(this);

  _layoutRefreshThrottle = createLayoutThrottledTask({
    task: () => {
      const entity = this.props.metadata.sensorsMap.get(
        this.props.uiState.selectedSensorId
      );
      if (entity) {
        this.generateTripwireCanvasElements(entity);
        this.generateRoiCanvasElements(entity);
      }
    },
  });

  _scheduleUpdate = () => this._layoutResizeThrottle.schedule();

  _scheduleRefresh = () => this._layoutRefreshThrottle.schedule();

  _onCalibrationViewTypeResolved = (viewType) => {
    this.setState({ mapCalibrationViewType: viewType });
  };

  // Persistent map to maintain consistent color assignment for object types
  objectTypeColorMap = new Map();

  getCurrentTimeMs = () => {
    return Date.now() - this.props.appConfig.uiDelaySeconds * 1000;
  };

  _collectViolationObjectIds(clusters) {
    const violationObjectIds = new Set();
    clusters.forEach((cluster) => {
      const ids =
        cluster.objectIds ??
        (cluster.points || [])
          .map((point) => point.id ?? point.objectId)
          .filter(Boolean);
      if (ids.length > 1) {
        for (const id of ids) {
          violationObjectIds.add(String(id));
        }
      }
    });
    return violationObjectIds;
  }

  _handleFrameDataResponse(data) {
    if (
      !data ||
      !data.enhancedFrames ||
      data.enhancedFrames.length === 0
    ) {
      this._throttledObjectsLocation.schedule(null);
      return;
    }

    const firstFrame = data.enhancedFrames[0];
    const apiClusters = firstFrame.socialDistancing?.clusters ?? [];
    const violationObjectIds = this._collectViolationObjectIds(apiClusters);

    const clusters = [];
    if (firstFrame.objects && firstFrame.objects.length > 0) {
      const points = firstFrame.objects
        .filter((obj) => obj.coordinate)
        .map((obj) => ({
          x: obj.coordinate.x,
          y: obj.coordinate.y,
          type: obj.label || obj.type || obj.class || "Unknown",
          id: obj.id != null ? String(obj.id) : null,
          inProximityViolation:
            obj.id != null
              ? violationObjectIds.has(String(obj.id))
              : false,
        }));
      if (points.length > 0) {
        clusters.push({ points });
      }
    }

    this._throttledObjectsLocation.schedule({
      id: firstFrame.id,
      timestamp: firstFrame.timestamp,
      clusters,
    });
  }

  startRequestCyclePeopleDataForSensor = (startTimeMs = -1) => {
    // stop previous
    this.stopRequestCyclePeopleDataForSensor();

    if (startTimeMs !== -1 && !/^\d+$/.test(startTimeMs)) {
      console.error(
        `LivePeopleMovementDataVisualizer: start time Ms value not a +ve int: ${startTimeMs}`
      );

      return;
    }

    this.abortControllerPeopleLocationDataFromSensor = new AbortController();

    let entityInfo = this.props.metadata.sensorsMap.get(
      this.props.uiState.selectedSensorId
    );
    // current time with backend processing delay
    if (startTimeMs === -1) {
      this.lastRequestTimeMsPeopleDataForSensor = this.getCurrentTimeMs();
    } else {
      this.lastRequestTimeMsPeopleDataForSensor = startTimeMs - 1;
    }

    let lastIntervalCallbackStartMs;

    // Use config value if provided, otherwise use default
    const pollingIntervalMs = this.props.appConfig.mapVizPollingIntervalMs || DEFAULT_PROXIMITY_DETECTION_CALL_TIME_INTERVAL_MS;

    // Wait-for-completion pattern: only schedule next request after current one completes
    const pollFrameData = () => {
      let newQueryTimeMs;
      if (startTimeMs !== -1) {
        let newIntervalCallbackStartMs = Date.now();
        if (!lastIntervalCallbackStartMs) {
          lastIntervalCallbackStartMs = newIntervalCallbackStartMs;
        }
        startTimeMs += newIntervalCallbackStartMs - lastIntervalCallbackStartMs;
        newQueryTimeMs = startTimeMs;
        lastIntervalCallbackStartMs = newIntervalCallbackStartMs;
      } else {
        newQueryTimeMs = this.getCurrentTimeMs();
      }

      if (newQueryTimeMs <= this.lastRequestTimeMsPeopleDataForSensor) {
        newQueryTimeMs = this.lastRequestTimeMsPeopleDataForSensor + 1;
      }

      fetch(joinUrl(FramesEnhancedUrl, {
          fromTimestamp: new Date(
            this.lastRequestTimeMsPeopleDataForSensor
          ).toISOString(),
          toTimestamp: new Date(newQueryTimeMs).toISOString(),
          sensorId: entityInfo.id,
        }), {
        signal: this.abortControllerPeopleLocationDataFromSensor?.signal,
      })
        .then((response) => readJson(response))
        .then((data) => {
          this.lastRequestTimeMsPeopleDataForSensor = newQueryTimeMs;
          this._handleFrameDataResponse(data);
        })
        .then(() => {
          // Schedule next poll only AFTER current request completes successfully
          // This prevents request stacking and allows other API calls to proceed
          this.requestPeopleDataForSensorCycleTimeoutId = setTimeout(
            pollFrameData,
            pollingIntervalMs
          );
        })
        .catch((thrown) => {
          let userMessage = new UserMessage(
            UserMessage.SRC_TYPE.HTTP_ERROR,
            UserMessage.MESSAGE_LEVEL.ERROR,
            "Fetch People Locations for sensor: " + entityInfo.id,
            thrown,
            "Retry",
            () => {
              this.startRequestCyclePeopleDataForSensor(entityInfo);
            }
          );
          this.props["addLogTo" + AppConstants.APP_LOGS_CONSOLE_NAME](
            userMessage
          );
          if (isAbortError(thrown)) {
            // Request was intentionally cancelled - don't restart the loop
            console.log(userMessage.getLongString());
          } else {
            // Request failed due to network/server error - restart the loop
            console.error(userMessage.getLongString());
            this.requestPeopleDataForSensorCycleTimeoutId = setTimeout(
              pollFrameData,
              pollingIntervalMs
            );
          }
        });
    };

    // Start the first poll
    pollFrameData();
  };

  stopRequestCyclePeopleDataForSensor = () => {
    this.abortControllerPeopleLocationDataFromSensor &&
      this.abortControllerPeopleLocationDataFromSensor.abort();
    clearTimeout(this.requestPeopleDataForSensorCycleTimeoutId);
  };

  // Generates tripwires lines components for the canvas
  generateTripwireCanvasElements = (entityInfo) => {
    if (
      !this.props.metadata.m2PxScaleFactor ||
      !this.cameraViewRef ||
      !this.cameraViewRef.topViewRef ||
      !this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter ||
      !this.cameraViewRef.topViewRef.scaleFactor
    )
      return;

    let tripwireCanvasElements = [];

    if (entityInfo.tripwires === undefined) {
      console.warn("No tripwires found for sensorId: " + entityInfo.id);
    } else {
      entityInfo.tripwires.forEach((wire) => {
        let tripwireLinesCoordinatesArray = [];
        // convert points to canvas coordinates
        let canvasPoint1 =
          this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter.convert(
            {
              x:
                (wire.wire.p1.x +
                  this.props.metadata.translationToGlobalCoordinates.x) *
                this.props.metadata.m2PxScaleFactor,
              y:
                (wire.wire.p1.y +
                  this.props.metadata.translationToGlobalCoordinates.y) *
                this.props.metadata.m2PxScaleFactor,
            }
          );
        let canvasPoint2 =
          this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter.convert(
            {
              x:
                (wire.wire.p2.x +
                  this.props.metadata.translationToGlobalCoordinates.x) *
                this.props.metadata.m2PxScaleFactor,
              y:
                (wire.wire.p2.y +
                  this.props.metadata.translationToGlobalCoordinates.y) *
                this.props.metadata.m2PxScaleFactor,
            }
          );

        tripwireLinesCoordinatesArray.push(canvasPoint1.x);
        tripwireLinesCoordinatesArray.push(canvasPoint1.y);
        tripwireLinesCoordinatesArray.push(canvasPoint2.x);
        tripwireLinesCoordinatesArray.push(canvasPoint2.y);

        tripwireCanvasElements.push(
          <TripwireLine
            key={wire.id}
            id={wire.id}
            points={tripwireLinesCoordinatesArray}
            scaleFactor={this.cameraViewRef.topViewRef.scaleFactor}
          />
        );
      });
    }

    this.setState({ tripwireCanvasElements: tripwireCanvasElements });
  };

  // Generates Roi rectangle React konva component for canvas
  generateRoiCanvasElements = (entityInfo) => {
    if (
      !this.props.metadata.m2PxScaleFactor ||
      !this.cameraViewRef ||
      !this.cameraViewRef.topViewRef ||
      !this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter ||
      !this.cameraViewRef.topViewRef.scaleFactor
    )
      return;

    let roiCanvasElements = [];

    entityInfo.rois.forEach((roi) => {
      let roiLinesCoodinatesArray = [];
      roi.roiCoordinates.forEach((coord) => {
        // convert to canvas coordinates
        let canvasCoord =
          this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter.convert(
            {
              x:
                (coord.x +
                  this.props.metadata.translationToGlobalCoordinates.x) *
                this.props.metadata.m2PxScaleFactor,
              y:
                (coord.y +
                  this.props.metadata.translationToGlobalCoordinates.y) *
                this.props.metadata.m2PxScaleFactor,
            }
          );
        roiLinesCoodinatesArray.push(canvasCoord.x);
        roiLinesCoodinatesArray.push(canvasCoord.y);
      });
      // To draw the last line of the roi rectangle from the given data,
      // re-add the first roi coordinate
      let canvasCoord =
        this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter.convert({
          x:
            (roi.roiCoordinates[0].x +
              this.props.metadata.translationToGlobalCoordinates.x) *
            this.props.metadata.m2PxScaleFactor,
          y:
            (roi.roiCoordinates[0].y +
              this.props.metadata.translationToGlobalCoordinates.y) *
            this.props.metadata.m2PxScaleFactor,
        });
      roiLinesCoodinatesArray.push(canvasCoord.x);
      roiLinesCoodinatesArray.push(canvasCoord.y);

      roiCanvasElements.push(
        <RoiLine
          key={roi.id}
          id={roi.id}
          points={roiLinesCoodinatesArray}
          scaleFactor={this.cameraViewRef.topViewRef.scaleFactor}
        />
      );
    });

    this.setState({ roiCanvasElements: roiCanvasElements });
  };

  // Get color for object type from ColorSet
  getColorForObjectType = (type, objectTypeColorMap) => {
    if (!objectTypeColorMap.has(type)) {
      // Assign a new color to this type
      const colorIndex = objectTypeColorMap.size % ColorSet.length;
      objectTypeColorMap.set(type, ColorSet[colorIndex]);
    }
    return objectTypeColorMap.get(type);
  };

  /** Sanitize object type for Konva layer name (selector-safe). */
  _layerNameForType = (type) =>
    "top-layer-" +
    String(type)
      .replace(/\s+/g, "-")
      .replace(/[^a-zA-Z0-9-]/g, "")
      .toLowerCase();

  /** Build dot snapshot from current state and converter; only call when throttle allows (1 FPS). */
  _buildDisplayDotsSnapshot = () => {
    const outlineStrokeWidth = 2;
    const outlineStrokeColor = "rgba(255, 255, 255, 0.85)";
    const whiteRingStrokeWidth = 2;
    const whiteRingColor = "rgba(255, 255, 255, 0.9)";
    const defaultRadius = 20;
    const violationRadius = 25;
    const violationStrokeWidth = 8;
    const scaleFactor = this.cameraViewRef?.topViewRef?.scaleFactor ?? 1;
    const converter = this.cameraViewRef?.topViewRef?.world2CanvasCoordinateConverter;
    const data = this.state.objectsLocationData;
    if (!data?.clusters || !converter) return null;
    const objectsByType = {};
    data.clusters.forEach((cluster, clusterIdx) => {
      cluster.points.forEach((point, pointIdx) => {
        const type = point.type;
        if (!objectsByType[type]) objectsByType[type] = [];

        const inViolation = point.inProximityViolation === true;
        const strokeColor = inViolation ? "red" : outlineStrokeColor;
        const strokeWidth = inViolation ? violationStrokeWidth : outlineStrokeWidth;
        const objectRadius = inViolation ? violationRadius : defaultRadius;
        const objectColor = this.getColorForObjectType(point.type, this.objectTypeColorMap);
        const convertedPoint = converter.convert({
          x:
            (point.x + this.props.metadata.translationToGlobalCoordinates.x) *
            this.props.metadata.m2PxScaleFactor,
          y:
            (point.y + this.props.metadata.translationToGlobalCoordinates.y) *
            this.props.metadata.m2PxScaleFactor,
        });

        if (inViolation) {
          const whiteRingRadius = objectRadius + strokeWidth / 2 + whiteRingStrokeWidth / 2;
          objectsByType[type].push({
            key: `${clusterIdx}-${pointIdx}-ring`,
            ring: true,
            x: convertedPoint.x,
            y: convertedPoint.y,
            radius: whiteRingRadius,
            fill: "transparent",
            stroke: whiteRingColor,
            strokeWidth: whiteRingStrokeWidth,
            scaleFactor,
          });
        }
        objectsByType[type].push({
          key: `${clusterIdx}-${pointIdx}`,
          ring: false,
          x: convertedPoint.x,
          y: convertedPoint.y,
          radius: objectRadius,
          fill: objectColor,
          stroke: strokeColor,
          strokeWidth,
          inViolation,
          scaleFactor,
        });
      });
    });
    return { objectsByType, scaleFactor };
  };

  /**
   * Floor-plan camera glyph + FOV (when config supports it) for the selected sensor.
   */
  _renderSelectedSensorCameraIcon() {
    const sensorConfig = this.props.metadata.sensorsMap.get(
      this.props.uiState.selectedSensorId
    );
    if (
      !sensorConfig ||
      sensorConfig.type !== "camera" ||
      !sensorConfig.coordinates ||
      this.state.mapCalibrationViewType !== "plan-view" ||
      !this.props.metadata.m2PxScaleFactor ||
      !this.cameraViewRef?.topViewRef?.world2CanvasCoordinateConverter ||
      !this.cameraViewRef?.topViewRef?.scaleFactor
    ) {
      return null;
    }
    return (
      <VizCameraIcon
        key={sensorConfig.id}
        cameraConfig={sensorConfig}
        scaleFactor={this.cameraViewRef.topViewRef.scaleFactor}
        iconScaleMultiplier={2.25}
        m2PxScaleFactor={this.props.metadata.m2PxScaleFactor}
        worldTranslationOffset={
          this.props.metadata.translationToGlobalCoordinates
        }
        coordinateConverter={
          this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter
        }
      />
    );
  }

  render() {
    const outlineStrokeWidth = 2;
    const outlineStrokeColor = "rgba(255, 255, 255, 0.85)";
    const whiteRingStrokeWidth = 2;
    const whiteRingColor = "rgba(255, 255, 255, 0.9)";
    const now = Date.now();
    if (!this.state.objectsLocationData) {
      this._displayDotsSnapshot = null;
    } else {
      const canUpdateDots =
        this.props.metadata.m2PxScaleFactor &&
        this.cameraViewRef?.topViewRef?.world2CanvasCoordinateConverter &&
        shouldRefreshCanvasDisplaySnapshotFromAppConfig(
          this.props.appConfig,
          this._displayDotsSnapshot != null,
          now,
          this._lastDisplayDotsUpdateTime
        );
      if (canUpdateDots) {
        this._displayDotsSnapshot = this._buildDisplayDotsSnapshot();
        this._lastDisplayDotsUpdateTime = now;
      }
    }

    const objectsByType = {};
    if (this._displayDotsSnapshot?.objectsByType) {
      const scale = this._displayDotsSnapshot.scaleFactor;
      Object.entries(this._displayDotsSnapshot.objectsByType).forEach(([type, items]) => {
        objectsByType[type] = items.map((item) => {
          if (item.ring) {
            return (
              <Circle
                key={item.key}
                x={item.x}
                y={item.y}
                radius={item.radius}
                fill={item.fill}
                stroke={item.stroke}
                strokeWidth={item.strokeWidth}
                perfectDrawEnabled={false}
                scale={{ x: scale, y: scale }}
              />
            );
          }
          return (
            <Circle
              key={item.key}
              x={item.x}
              y={item.y}
              radius={item.radius}
              fill={item.fill}
              stroke={item.stroke}
              strokeWidth={item.strokeWidth}
              shadowColor={item.inViolation ? undefined : "white"}
              shadowBlur={item.inViolation ? 0 : 10}
              perfectDrawEnabled={false}
              scale={{ x: scale, y: scale }}
            />
          );
        });
      });
    }

    const sortedTypes = Object.keys(objectsByType).sort((a, b) => a.localeCompare(b));
    const ROI_TRIPWIRE_LAYER = "top-layer-roi-tripwire";
    const overlayLayerNames = [
      ROI_TRIPWIRE_LAYER,
      ...sortedTypes.map((t) => this._layerNameForType(t)),
    ];

    // Create legend items from objectTypeColorMap
    const legendItems = Array.from(this.objectTypeColorMap.entries()).map(([type, color]) => (
      <div
        key={type}
        style={{
          display: "inline-flex",
          alignItems: "center",
          marginRight: "15px",
          marginBottom: "5px",
        }}
      >
        <div
          style={{
            width: "16px",
            height: "16px",
            borderRadius: "50%",
            backgroundColor: color,
            marginRight: "6px",
            border: "1px solid #ccc",
          }}
        />
        <span style={{ fontSize: "12px", color: "#333" }}>{type}</span>
      </div>
    ));

    return (
      <>
        <Widget
          left="21.5%"
          top="6%"
          width="48%"
          height="calc(94% - 3.25rem)"
          zIndex="1"
          showShadow={true}
        >
          <Window titleText="Map Viz">
            {this.props.metadata.m2PxScaleFactor ? (
              <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
                <div 
                  style={{ 
                    flex: 1, 
                    minHeight: 0,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    overflow: "hidden",
                  }}
                >
                  <div style={{ width: "100%", height: "100%", maxHeight: "80%", overflow: "hidden" }}>
                    <CameraView
                      ref={(ref) => (this.cameraViewRef = ref)}
                      endpointGetCameraImagesMetadata={CalibrationImageMetadataUrl}
                      endpointGetCameraViewImage={CalibrationImageUrl}
                      cameraConfig={this.props.metadata.sensorsMap.get(
                        this.props.uiState.selectedSensorId
                      )}
                      onResize={this._scheduleUpdate}
                      onRefreshCoordinateConverters={this._scheduleRefresh}
                      onCalibrationViewTypeResolved={
                        this._onCalibrationViewTypeResolved
                      }
                      overlayLayerNames={overlayLayerNames}
                    >
                      <Portal selector={`.${ROI_TRIPWIRE_LAYER}`}>
                        {this.state.roiCanvasElements}
                        {this.state.tripwireCanvasElements}
                      </Portal>
                      {sortedTypes.map((type) => (
                        <Portal
                          key={type}
                          selector={`.${this._layerNameForType(type)}`}
                        >
                          {objectsByType[type]}
                        </Portal>
                      ))}
                      {this._renderSelectedSensorCameraIcon()}
                    </CameraView>
                  </div>
                </div>
                <div
                  style={{
                    padding: "10px",
                    backgroundColor: "#f5f5f5",
                    borderTop: "1px solid #ddd",
                    minHeight: "60px",
                    maxHeight: "120px",
                    overflowY: "auto",
                    overflowX: "hidden",
                    display: "flex",
                    flexWrap: "wrap",
                    justifyContent: "center",
                    alignItems: "center",
                    flexShrink: 0,
                  }}
                >
                  {legendItems.length > 0 ? (
                    legendItems
                  ) : (
                    <span style={{ fontSize: "12px", color: "#999", fontStyle: "italic" }}>
                      No object types found
                    </span>
                  )}
                </div>
              </div>
            ) : (
              "Missing Global Meters:Pixels scale factor"
            )}
          </Window>
        </Widget>
      </>
    );
  }

  componentWillUnmount() {
    this.stopRequestCyclePeopleDataForSensor();
    this._layoutResizeThrottle.dispose();
    this._layoutRefreshThrottle.dispose();
    this._throttledObjectsLocation.clearScheduledUpdate();
  }
}

export default connect(
  (state) => ({
    appConfig: state.appConfig,
    metadata: state.metadata,
    uiState: state.uiState,
  }),
  ReduxUtils.combineDispatchToProps(
    AppLogsConsoleReduxUtil.slice.actions,
    UiStateReduxUtil.slice.actions
  ),
  null,
  { forwardRef: true }
)(LivePeopleMovementDataVisualizer);
