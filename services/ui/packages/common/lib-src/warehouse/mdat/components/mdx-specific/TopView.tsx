// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Stage, Layer, Image, Line } from "react-konva";
import {
  IconFocusCentered,
  IconHelpCircle,
  IconZoomIn,
  IconZoomOut,
  IconZoomReset,
} from "@tabler/icons-react";

import World2CanvasCartesianCoordinateConverter from "./../../utils/World2CanvasCartesianCoordinateConverter";
import Canvas2WorldCartesianCoordinateConverter from "./../../utils/Canvas2WorldCartesianCoordinateConverter";
import { TopViewContext } from "./TopViewContext";

import Styles from "./TopView.module.css";

// TODO: Almost duplicate code with CameraView.js. Refactor

// Shows top view visualization of the real world, by animating/displaying relevant items
// on a canvas
export default class TopView extends React.Component {
  constructor(props) {
    super(props);
    // default to -1 so that initial w X h = 0
    this.scaleFactor = -1;
    this.state = {
      stageScale: 1,
      stageX: 0,
      stageY: 0,
      showInfoPopup: false,
      showZoomPopup: false,
      isDragging: false,
    };
    this.zoomPopupTimeout = null;
  }

  refreshCoordinateConverters = () => {
    if (!this.scaleFactor || !this.props.topViewImage) return;

    this.world2CanvasCoordinateConverter =
      new World2CanvasCartesianCoordinateConverter(
        {
          width: this.props.topViewImage.width,
          height: this.props.topViewImage.height,
        },
        {
          width: this.props.topViewImage.width * this.scaleFactor,
          height: this.props.topViewImage.height * this.scaleFactor,
        }
      );
    this.canvas2WorldCoordinateConverter =
      new Canvas2WorldCartesianCoordinateConverter(
        {
          width: this.props.topViewImage.width,
          height: this.props.topViewImage.height,
        },
        {
          width: this.props.topViewImage.width * this.scaleFactor,
          height: this.props.topViewImage.height * this.scaleFactor,
        }
      );

    this.props.onRefreshCoordinateConverters &&
      this.props.onRefreshCoordinateConverters();
  };

  calculateCenteredPosition = (useScaleFactor = null, useStageScale = null) => {
    const scaleFactor = useScaleFactor !== null ? useScaleFactor : this.scaleFactor;
    const stageScale = useStageScale !== null ? useStageScale : this.state.stageScale;
    
    if (!this.parentRef || !this.props.topViewImage || scaleFactor <= 0) {
      return null;
    }

    const containerWidth = this.parentRef.clientWidth;
    const containerHeight = this.parentRef.clientHeight;
    
    // Wrapper div size (fixed size of the Stage element)
    const wrapperWidth = this.props.topViewImage.width * scaleFactor;
    const wrapperHeight = this.props.topViewImage.height * scaleFactor;
    
    // Just center the wrapper itself (ignore stageScale for positioning)
    // We'll use CSS transform on the wrapper to scale from center
    const centeredX = (containerWidth - wrapperWidth) / 2;
    const centeredY = (containerHeight - wrapperHeight) / 2;
    
    return { x: centeredX, y: centeredY };
  };

  componentDidMount() {
    this.forceUpdate();
  }

  componentDidUpdate() {
    if (this.parentRef && this.props.topViewImage && !this.resizeObserver) {
      // Set up ResizeObserver for future resizes
      this.resizeObserver = new ResizeObserver(() => {
        const newScaleFactor = Math.min(
          this.parentRef.clientWidth / this.props.topViewImage.width,
          this.parentRef.clientHeight / this.props.topViewImage.height
        );

        // Calculate centered position with the new scaleFactor
        const centeredPos = this.calculateCenteredPosition(newScaleFactor);
        
        // Update scaleFactor and position together to prevent misalignment
        if (centeredPos) {
          // Set both scaleFactor and positions in sync
          this.scaleFactor = newScaleFactor;
          this.setState({
            stageX: centeredPos.x,
            stageY: centeredPos.y,
          }, () => {
            this.refreshCoordinateConverters();
            this.props.onResize && this.props.onResize();
          });
        } else {
          // Fallback: set scaleFactor without position update
          this.scaleFactor = newScaleFactor;
          this.refreshCoordinateConverters();
          this.forceUpdate();
          this.props.onResize && this.props.onResize();
        }
      });
      
      // Perform initial setup synchronously before observing
      const initialScaleFactor = Math.min(
        this.parentRef.clientWidth / this.props.topViewImage.width,
        this.parentRef.clientHeight / this.props.topViewImage.height
      );
      const initialCenteredPos = this.calculateCenteredPosition(initialScaleFactor);
      
      if (initialCenteredPos) {
        // CRITICAL: Set scaleFactor AFTER setState completes to prevent render with (0,0)
        this.setState({
          stageX: initialCenteredPos.x,
          stageY: initialCenteredPos.y,
        }, () => {
          this.scaleFactor = initialScaleFactor;
          this.refreshCoordinateConverters();
          this.forceUpdate();
          
          // Start observing AFTER initial setup is complete
          this.resizeObserver.observe(this.parentRef);
        });
      } else {
        this.scaleFactor = initialScaleFactor;
        this.refreshCoordinateConverters();
        this.forceUpdate();
        
        // Start observing AFTER initial setup is complete
        this.resizeObserver.observe(this.parentRef);
      }
    }
  }

  showZoomPopupTemporarily = () => {
    if (this.zoomPopupTimeout) {
      clearTimeout(this.zoomPopupTimeout);
    }
    
    this.setState({ showZoomPopup: true });
    
    this.zoomPopupTimeout = setTimeout(() => {
      this.setState({ showZoomPopup: false });
    }, 1000);
  };

  handleWheel = (e) => {
    e.evt.preventDefault();

    const scaleBy = 1.1;
    const stage = e.target.getStage();
    const oldScale = this.state.stageScale;
    
    // pointer is in Stage canvas coordinates (relative to canvas top-left)
    const pointer = stage.getPointerPosition();
    
    // Get pointer position in container coordinates
    const pointerInContainer = {
      x: this.state.stageX + pointer.x * oldScale,
      y: this.state.stageY + pointer.y * oldScale,
    };

    // Find the point on the unscaled canvas that's under the mouse
    const mousePointTo = {
      x: pointer.x,
      y: pointer.y,
    };

    const newScale = e.evt.deltaY > 0 ? oldScale / scaleBy : oldScale * scaleBy;

    // Limit zoom range
    const clampedScale = Math.max(0.5, Math.min(newScale, 10));

    // Calculate new wrapper position so the same canvas point stays under the mouse
    const newStageX = pointerInContainer.x - mousePointTo.x * clampedScale;
    const newStageY = pointerInContainer.y - mousePointTo.y * clampedScale;

    this.setState({
      stageScale: clampedScale,
      stageX: newStageX,
      stageY: newStageY,
    }, () => {
      this.showZoomPopupTemporarily();
    });
  };

  handleMouseDown = () => {
    this.setState({ isDragging: true });
  };

  handleMouseUp = () => {
    this.setState({ isDragging: false });
  };

  handleDragStart = () => {
    this.setState({ isDragging: true });
  };

  handleDragMove = (e) => {
    // Update wrapper position in real-time during drag
    const dragOffsetX = e.target.x();
    const dragOffsetY = e.target.y();
    
    const newX = this.state.stageX + dragOffsetX;
    const newY = this.state.stageY + dragOffsetY;
    
    // Update state to move the wrapper div
    this.setState({
      stageX: newX,
      stageY: newY,
    });
    
    // Reset Stage position so it stays at 0,0 relative to wrapper
    e.target.position({ x: 0, y: 0 });
  };

  handleDragEnd = (e) => {
    // Position was already updated by handleDragMove
    // Just ensure Stage is at 0,0 and end dragging state
    e.target.position({ x: 0, y: 0 });
    
    this.setState({
      isDragging: false,
    });
  };

  handleContainerMouseLeave = () => {
    // Cancel drag when mouse leaves the container viewport
    if (this.state.isDragging && this.stageRef) {
      // Stop the Konva drag operation
      this.stageRef.stopDrag();
      this.setState({ isDragging: false });
    }
  };

  handleZoomIn = () => {
    const scaleBy = 1.2;
    const newScale = this.state.stageScale * scaleBy;
    const clampedScale = Math.min(newScale, 10);
    
    // Calculate centered position with the NEW scale (passed as parameter)
    const centeredPos = this.calculateCenteredPosition(null, clampedScale);
    
    // Update scale AND position in a SINGLE setState to avoid intermediate render
    if (centeredPos) {
      this.setState({
        stageScale: clampedScale,
        stageX: centeredPos.x,
        stageY: centeredPos.y,
      }, () => {
        this.showZoomPopupTemporarily();
      });
    } else {
      this.setState({
        stageScale: clampedScale,
      }, () => {
        this.showZoomPopupTemporarily();
      });
    }
  };

  handleZoomOut = () => {
    const scaleBy = 1.2;
    const newScale = this.state.stageScale / scaleBy;
    const clampedScale = Math.max(newScale, 0.5);
    
    // Calculate centered position with the NEW scale (passed as parameter)
    const centeredPos = this.calculateCenteredPosition(null, clampedScale);
    
    // Update scale AND position in a SINGLE setState to avoid intermediate render
    if (centeredPos) {
      this.setState({
        stageScale: clampedScale,
        stageX: centeredPos.x,
        stageY: centeredPos.y,
      }, () => {
        this.showZoomPopupTemporarily();
      });
    } else {
      this.setState({
        stageScale: clampedScale,
      }, () => {
        this.showZoomPopupTemporarily();
      });
    }
  };

  handleResetZoom = () => {
    // Calculate centered position with scale = 1 (passed as parameter)
    const centeredPos = this.calculateCenteredPosition(null, 1);
    
    // Update scale AND position in a SINGLE setState
    if (centeredPos) {
      this.setState({
        stageScale: 1,
        stageX: centeredPos.x,
        stageY: centeredPos.y,
      }, () => {
        this.showZoomPopupTemporarily();
      });
    } else {
      this.setState({
        stageScale: 1,
      }, () => {
        this.showZoomPopupTemporarily();
      });
    }
  };

  handleResetPosition = () => {
    const centeredPos = this.calculateCenteredPosition();
    if (centeredPos) {
      this.setState({
        stageX: centeredPos.x,
        stageY: centeredPos.y,
      });
    }
  };

  toggleInfoPopup = () => {
    this.setState((prevState) => ({
      showInfoPopup: !prevState.showInfoPopup,
    }));
  };

  render() {
    if (!this.props.topViewImage) return "No Top View Image";

    const zoomPercentage = Math.round(this.state.stageScale * 100);

    return (
      <div className={Styles.container} ref={(ref) => (this.parentRef = ref)} onMouseLeave={this.handleContainerMouseLeave}>
        {this.parentRef && this.scaleFactor !== -1 && (
          <TopViewContext.Provider value={{ stageScale: this.state.stageScale }}>
            <div
              style={{
                position: 'absolute',
                left: this.state.stageX,
                top: this.state.stageY,
                transform: `scale(${this.state.stageScale})`,
                transformOrigin: 'center center',
              }}
            >
              <Stage
                ref={(ref) => {
                  this.stageRef = ref;
                  this.props.onLoad && this.props.onLoad();
                }}
                width={this.props.topViewImage.width * this.scaleFactor}
                height={this.props.topViewImage.height * this.scaleFactor}
                onWheel={this.handleWheel}
                draggable
                x={0}
                y={0}
                scaleX={1}
                scaleY={1}
                onMouseDown={this.handleMouseDown}
                onMouseUp={this.handleMouseUp}
                onDragStart={this.handleDragStart}
                onDragMove={this.handleDragMove}
                onDragEnd={this.handleDragEnd}
              >
              {this.props.trajectoryLayers &&
              this.props.trajectoryLayers.length > 0 ? (
                <React.Fragment>
                  <Layer>
                    <Image
                      image={this.props.topViewImage}
                      x={0}
                      y={0}
                      width={
                        this.props.topViewImage.width * this.scaleFactor
                      }
                      height={
                        this.props.topViewImage.height * this.scaleFactor
                      }
                    />
                  </Layer>
                  {this.props.trajectoryLayers.map(({ name, content }) => (
                    <Layer key={name} name={name}>
                      {content}
                    </Layer>
                  ))}
                  {/*
                    CameraView children (e.g. VizCameraIcon Portals) must live inside a Layer.
                    Direct Stage children can only be Layers — raw Portal Groups caused
                    "You may only add layers to the stage."
                  */}
                  <Layer listening={false} name="trajectory-topview-children">
                    {this.props.children}
                  </Layer>
                </React.Fragment>
              ) : (
                <Layer listening={false}>
                  <Image
                    image={this.props.topViewImage}
                    x={0}
                    y={0}
                    width={
                      this.props.topViewImage.width * this.scaleFactor
                    }
                    height={
                      this.props.topViewImage.height * this.scaleFactor
                    }
                  />
                  {this.props.children}
                </Layer>
              )}
              {this.state.isDragging && (
                <Layer>
                  {/* Red lines - content center */}
                  <Line
                    points={[
                      0,
                      (this.props.topViewImage.height * this.scaleFactor) / 2,
                      this.props.topViewImage.width * this.scaleFactor * 10,
                      (this.props.topViewImage.height * this.scaleFactor) / 2,
                    ]}
                    stroke="rgba(255, 0, 0, 0.8)"
                    strokeWidth={3}
                    dash={[10, 10]}
                    shadowColor="rgba(255, 0, 0, 1)"
                    shadowBlur={6}
                  />
                  <Line
                    points={[
                      (this.props.topViewImage.width * this.scaleFactor) / 2,
                      0,
                      (this.props.topViewImage.width * this.scaleFactor) / 2,
                      this.props.topViewImage.height * this.scaleFactor * 10,
                    ]}
                    stroke="rgba(255, 0, 0, 0.8)"
                    strokeWidth={3}
                    dash={[10, 10]}
                    shadowColor="rgba(255, 0, 0, 1)"
                    shadowBlur={6}
                  />
                </Layer>
              )}
              {this.props.overlayLayerNames && this.props.overlayLayerNames.length > 0
                ? this.props.overlayLayerNames.map((name) => (
                    <Layer key={name} name={name} />
                  ))
                : (
                    <Layer name="top-layer" />
                  )}
              {/*
                Camera icons + FOV (VizCameraIcon) portal here so they sit above
                dots/lines on top-layer without sharing the same canvas layer.
              */}
              <Layer name="camera-viz-layer" />
            </Stage>
            </div>
            {this.state.showInfoPopup && (
              <div className={Styles.infoPopup} role="tooltip">
                <div className={Styles.infoPopupTitle}>Map controls</div>
                <ul>
                  <li>Drag with the left mouse button to pan the floor plan.</li>
                  <li>Scroll the mouse wheel, or use + / −, to zoom.</li>
                  <li>Reset zoom returns to 100%. Recenter puts the plan in the middle of the view.</li>
                </ul>
              </div>
            )}
            {this.state.showZoomPopup && (
              <div className={Styles.zoomPopupCenter}>
                {zoomPercentage}%
              </div>
            )}
            {this.state.isDragging && this.parentRef && (
              <svg className={Styles.blueGuideLines}>
                <line
                  x1="0"
                  y1={this.parentRef.clientHeight / 2}
                  x2={this.parentRef.clientWidth}
                  y2={this.parentRef.clientHeight / 2}
                  stroke="rgba(0, 128, 255, 0.5)"
                  strokeWidth="2"
                  style={{
                    filter: 'drop-shadow(0 0 4px rgba(0, 128, 255, 0.8))'
                  }}
                />
                <line
                  x1={this.parentRef.clientWidth / 2}
                  y1="0"
                  x2={this.parentRef.clientWidth / 2}
                  y2={this.parentRef.clientHeight}
                  stroke="rgba(0, 128, 255, 0.5)"
                  strokeWidth="2"
                  style={{
                    filter: 'drop-shadow(0 0 4px rgba(0, 128, 255, 0.8))'
                  }}
                />
              </svg>
            )}
            <div className={Styles.zoomControls} role="toolbar" aria-label="Map controls">
              <button
                type="button"
                className={`${Styles.zoomButton} ${this.state.showInfoPopup ? Styles.zoomButtonActive : ""}`}
                onClick={this.toggleInfoPopup}
                data-tooltip="Help: drag to pan, scroll or use +/− to zoom, reset to restore the view"
                aria-label="Help: drag to pan, scroll or use +/− to zoom, reset to restore the view"
                aria-expanded={this.state.showInfoPopup}
              >
                <IconHelpCircle size={18} stroke={1.75} />
              </button>
              <span className={Styles.controlDivider} aria-hidden="true" />
              <button
                type="button"
                className={Styles.zoomButton}
                onClick={this.handleZoomIn}
                disabled={this.state.stageScale >= 10}
                data-tooltip="Zoom in"
                aria-label="Zoom in"
              >
                <IconZoomIn size={18} stroke={1.75} />
              </button>
              <button
                type="button"
                className={Styles.zoomButton}
                onClick={this.handleZoomOut}
                disabled={this.state.stageScale <= 0.5}
                data-tooltip="Zoom out"
                aria-label="Zoom out"
              >
                <IconZoomOut size={18} stroke={1.75} />
              </button>
              <button
                type="button"
                className={Styles.zoomButton}
                onClick={this.handleResetZoom}
                data-tooltip="Reset zoom to 100%"
                aria-label="Reset zoom to 100%"
              >
                <IconZoomReset size={18} stroke={1.75} />
              </button>
              <button
                type="button"
                className={Styles.zoomButton}
                onClick={this.handleResetPosition}
                data-tooltip="Recenter the floor plan"
                aria-label="Recenter the floor plan"
              >
                <IconFocusCentered size={18} stroke={1.75} />
              </button>
            </div>
          </TopViewContext.Provider>
        )}
      </div>
    );
  }

  componentWillUnmount() {
    this.resizeObserver && this.resizeObserver.disconnect();
    if (this.zoomPopupTimeout) {
      clearTimeout(this.zoomPopupTimeout);
    }
  }
}

/*
 * Prop types
 *
 * topViewImage: background image for topview
 * onResize: Callback
 * onLoad: Callback
 * onRefreshCoordinateConverters: Callback
 */
