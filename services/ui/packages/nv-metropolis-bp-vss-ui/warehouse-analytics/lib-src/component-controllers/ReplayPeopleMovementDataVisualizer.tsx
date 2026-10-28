// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Circle } from "react-konva";
import { Portal } from "react-konva-utils";
import { connect } from "react-redux";

import {
  CameraView,
  LoaderDisplay,
  VizTooltip,
  Widget,
  Window,
  TripwireLine,
  RoiLine,
  VizCameraIcon,
  getCartesianMapVizCanvasThrottleMsFromAppConfig,
  shouldRefreshCanvasDisplaySnapshotFromAppConfig,
  createThrottledObjectsLocationState,
  createLayoutThrottledTask,
  createForceUpdateLayoutThrottle,
} from "common/warehouse";

import { CalibrationImageUrl, CalibrationImageMetadataUrl } from "common/warehouse";

class ReplayPeopleMovementDataVisualizer extends React.PureComponent {
  state = {
    /** From CameraView metadata: show floor-plan camera icon only for plan-view, not warped-camera-view. */
    mapCalibrationViewType: null,
  };

  /** Snapshot of converted dot positions; throttled by cartesianMapVizContentsRenderUpdateFps. */
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

  /**
   * Takes locations data from the event info and animates location successively
   * in the array over the total time inteval in the info in steps format
   */
  startReplayPeopleMovement = () => {
    this.cancelPreviousExecutions();
    this._throttledObjectsLocation.clearScheduledUpdate();
    this._throttledObjectsLocation.resetScheduleClock();

    this.animationTimeoutIds = [];
    let perStepTimeoutMs =
      (this.props.replayEventInfo.timeInterval * 1000) /
      this.props.replayEventInfo.locations.coordinates.length;
    for (
      let i = 0;
      i < this.props.replayEventInfo.locations.coordinates.length;
      i++
    ) {
      ((index, timeout) => {
        this.animationTimeoutIds.push(
          setTimeout(() => {
            this._throttledObjectsLocation.schedule([
              this.props.replayEventInfo.locations.coordinates[index],
            ]);
          }, timeout)
        );
      })(i, i * perStepTimeoutMs);
    }
  };

  hideAnimationIcon = () => {
    this.cancelPreviousExecutions();
    this._throttledObjectsLocation.clearScheduledUpdate();
    this._throttledObjectsLocation.resetScheduleClock();
    this.setState({ objectsLocationData: [] });
  };

  cancelPreviousExecutions = () => {
    // Abort all the location animation timeout calls
    this.animationTimeoutIds &&
      this.animationTimeoutIds.forEach((id) => clearTimeout(id));
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

  onMouseOverObjectMarker = (event) => {
    const stage = event.currentTarget.getStage();
    if (!stage) return;
    const evt = event.evt;
    const clientPosition =
      evt && typeof evt.clientX === "number"
        ? { x: evt.clientX, y: evt.clientY }
        : null;
    if (!clientPosition) return;
    this.setState({
      objectMarkerTooltipEl: (
        <VizTooltip
          clientPosition={clientPosition}
          text={`id: ${this.props.replayEventInfo.object.id}`}
        />
      ),
    });
  };

  onMouseOutObjectMarker = () => {
    this.state.objectMarkerTooltipEl &&
      this.setState({
        objectMarkerTooltipEl: undefined,
      });
  };

  render() {
    const outlineStrokeWidth = 2;
    const outlineStrokeColor = "rgba(255, 255, 255, 0.85)";
    const now = Date.now();
    const data = this.state.objectsLocationData;
    const canUpdateDots =
      data &&
      data.length > 0 &&
      this.props.metadata.m2PxScaleFactor &&
      this.cameraViewRef?.topViewRef?.world2CanvasCoordinateConverter &&
      shouldRefreshCanvasDisplaySnapshotFromAppConfig(
        this.props.appConfig,
        this._displayDotsSnapshot != null,
        now,
        this._lastDisplayDotsUpdateTime
      );
    if (!data || data.length === 0) {
      this._displayDotsSnapshot = null;
    } else if (canUpdateDots) {
      const scaleFactor = this.cameraViewRef.topViewRef.scaleFactor;
      const converter = this.cameraViewRef.topViewRef.world2CanvasCoordinateConverter;
      const objectsLocationArray = data.map((loc) => {
        const locXY = {
          x:
            (loc[0] + this.props.metadata.translationToGlobalCoordinates.x) *
            this.props.metadata.m2PxScaleFactor,
          y:
            (loc[1] + this.props.metadata.translationToGlobalCoordinates.y) *
            this.props.metadata.m2PxScaleFactor,
        };
        const convertedPoint = converter.convert(locXY);
        const objectColor =
          loc ===
          this.props.replayEventInfo.locations.coordinates[
            this.props.replayEventInfo.locations.coordinates.length - 1
          ]
            ? "grey"
            : "green";
        return {
          x: convertedPoint.x,
          y: convertedPoint.y,
          color: objectColor,
          radius: 20,
          strokeColor: outlineStrokeColor,
          strokeWidth: outlineStrokeWidth,
        };
      });
      this._displayDotsSnapshot = { objectsLocationArray, scaleFactor };
      this._lastDisplayDotsUpdateTime = now;
    }

    let objects = [];
    if (this._displayDotsSnapshot?.objectsLocationArray) {
      const scale = this._displayDotsSnapshot.scaleFactor ?? this.cameraViewRef?.topViewRef?.scaleFactor ?? 1;
      this._displayDotsSnapshot.objectsLocationArray.forEach((loc, key) => {
        objects.push(
          <Circle
            key={key}
            x={loc.x}
            y={loc.y}
            radius={loc.radius}
            fill={loc.color}
            stroke={loc.strokeColor}
            strokeWidth={loc.strokeWidth}
            shadowColor="white"
            shadowBlur={10}
            perfectDrawEnabled={false}
            onMouseOver={this.onMouseOverObjectMarker.bind(this)}
            onMouseOut={this.onMouseOutObjectMarker.bind(this)}
            scale={{ x: scale, y: scale }}
          />
        );
      });
    }

    return (
      <>
        <Widget
          left="21.5%"
          top="6%"
          width="48%"
          height="94%"
          zIndex="1"
          showShadow={true}
        >
          <Window titleText="Map Viz">
            <div
              style={{
                position: "absolute",
                inset: 0,
                overflow: "hidden",
              }}
            >
              {this.props.metadata.m2PxScaleFactor ? (
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
                  overlayLayerNames={["top-layer-roi-tripwire", "top-layer"]}
                >
                  <Portal selector=".top-layer">{objects}</Portal>
                  <Portal selector=".top-layer-roi-tripwire">
                    {this.state.roiCanvasElements}
                    {this.state.tripwireCanvasElements}
                  </Portal>
                  {this._renderSelectedSensorCameraIcon()}
                </CameraView>
              ) : (
                "Missing Global Meters:Pixels scale factor"
              )}
              {this.props.replayEventInfo?.locations?.coordinates?.length >
                0 &&
                (!this.state.objectsLocationData ||
                  this.state.objectsLocationData.length === 0) && (
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      backgroundColor: "rgba(221, 221, 221, 0.8)",
                      zIndex: 1100,
                    }}
                  >
                    <LoaderDisplay
                      type={LoaderDisplay.TYPE.LOADING}
                      size={LoaderDisplay.SIZE.SMALL}
                      userMessage="Starting event replay"
                      backgroundOpacity={0}
                    />
                  </div>
                )}
            </div>
          </Window>
        </Widget>
        {this.state.objectMarkerTooltipEl}
      </>
    );
  }

  componentWillUnmount() {
    this.cancelPreviousExecutions();
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
  null,
  null,
  { forwardRef: true }
)(ReplayPeopleMovementDataVisualizer);
