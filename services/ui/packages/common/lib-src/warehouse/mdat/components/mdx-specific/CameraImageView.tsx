// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import { Stage, Layer, Image } from "react-konva";

import Image2CanvasCartesianCoordinateConverter from "./../../utils/Image2CanvasCartesianCoordinateConverter";
import Canvas2WorldCartesianCoordinateConverter from "./../../utils/Canvas2WorldCartesianCoordinateConverter";

import Styles from "./CameraImageView.module.css";

// TODO: Almost duplicate code with CameraView.js. Refactor

export default class CameraImageView extends React.Component {
  constructor(props) {
    super(props);
    // default to -1 so that initial w X h = 0
    this.scaleFactor = -1;
  }

  refreshCoordinateConverters = () => {
    if (!this.scaleFactor || !this.props.viewImage) return;

    this.image2CanvasCoordinateConverter =
      new Image2CanvasCartesianCoordinateConverter(
        {
          width: this.props.viewImage.width,
          height: this.props.viewImage.height,
        },
        {
          width: this.props.viewImage.width * this.scaleFactor,
          height: this.props.viewImage.height * this.scaleFactor,
        }
      );
    this.canvas2WorldCoordinateConverter =
      new Canvas2WorldCartesianCoordinateConverter(
        {
          width: this.props.viewImage.width,
          height: this.props.viewImage.height,
        },
        {
          width: this.props.viewImage.width * this.scaleFactor,
          height: this.props.viewImage.height * this.scaleFactor,
        }
      );

    this.props.onRefreshCoordinateConverters &&
      this.props.onRefreshCoordinateConverters();
  };

  componentDidMount() {
    this.forceUpdate();
  }

  componentDidUpdate() {
    if (this.parentRef && this.props.viewImage && !this.resizeObserver) {
      this.resizeObserver = new ResizeObserver(() => {
        this.scaleFactor = Math.min(
          this.parentRef.clientWidth / this.props.viewImage.width,
          this.parentRef.clientHeight / this.props.viewImage.height
        );

        this.refreshCoordinateConverters();
        this.forceUpdate();
        this.props.onResize && this.props.onResize();
      });
      this.resizeObserver.observe(this.parentRef);
    }
  }

  render() {
    if (!this.props.viewImage) return "No Top View Image";

    return (
      <div className={Styles.container} ref={(ref) => (this.parentRef = ref)}>
        {this.parentRef && this.scaleFactor !== -1 && (
          <Stage
            ref={(ref) => {
              this.props.onLoad && this.props.onLoad();
            }}
            width={this.props.viewImage.width * this.scaleFactor}
            height={this.props.viewImage.height * this.scaleFactor}
          >
            <Layer>
              <Image
                image={this.props.viewImage}
                x={0}
                y={0}
                width={this.props.viewImage.width * this.scaleFactor}
                height={this.props.viewImage.height * this.scaleFactor}
              />
              {this.props.children}
            </Layer>
            <Layer name="top-layer" />
          </Stage>
        )}
      </div>
    );
  }

  componentWillUnmount() {
    this.resizeObserver && this.resizeObserver.disconnect();
  }
}

/*
 * Prop types
 *
 * viewImage: background image for topview
 * onResize: Callback
 * onLoad: Callback
 * onRefreshCoordinateConverters: Callback
 */
