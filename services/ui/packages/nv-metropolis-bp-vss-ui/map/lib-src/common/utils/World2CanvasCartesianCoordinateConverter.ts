// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { matrix, multiply, index } from "mathjs";

/**
 * Default Util class to convert points from source sensor's view of world
 * in pixels coordinate system to HTML UI Canvas coordinates
 */
export default class World2CanvasCartesianCoordinateConverter {
  /**
   * @param {object} config input data for the entity under context
   * @param {object} targetCanvasSize (width, height) of target canvas,
   *   width, height are assumed in in px (pixel) values
   */
  constructor(sensorViewWorldFOVSize, targetCanvasSize) {
    // MDX assumes that camera view on UI canvas should show
    // visible camera region. The size of this real world view FOV is passed
    // as input here and differs by some scaling factor w.r.t. the size
    // of the target canvas.
    // Hence, need for translation
    // from world global origin to camera visible area origin is
    // required for any conversion of a new point given in world global
    // coordinates to HTML canvas coordinates.

    // Compute x, y axes scaling factors to map world units to given target
    // canvas size
    let canvasPixelScalingFactorX =
      (1.0 * targetCanvasSize.width) / sensorViewWorldFOVSize.width;
    let canvasPixelScalingFactorY =
      (1.0 * targetCanvasSize.height) / sensorViewWorldFOVSize.height;

    // compute transformation matrix for drawing a new point (given based off
    // global coordinates) on canvas
    // transformation =
    //   (scaling to fit given canvas size in pixels) *
    //   (translation to canvas upside down coordinate system)
    //
    // Then Scaling, because, original width/height units may be too small for the screen/target
    // canvas size.
    // Then second translation, because, canvas Y axis is positive downward unlike global world
    // Y axis, which is positive upward.

    // using homogeneous matrices in 3D to solve for 2D

    // Scale to fit target canvas size
    // Backend stores values of points in meters, while the top view FOV is in cms
    // So scaling between meters to cms is also needed.
    let matrixScaleWorldUnit2CanvasUnit = matrix([
      [canvasPixelScalingFactorX, 0, 0],
      [0, canvasPixelScalingFactorY, 0],
      [0, 0, 1],
    ]);

    // Convert to canvas upside down Y axis system
    //
    // For this, first translate scaled world Y coordinate downward to bottom left.
    // Then, convert the tansformed Y coordinate to match canvas's upside down Y axis
    // system.
    let matrixTranslateCanvasOrigin2CameraFovCanvasOrigin = matrix([
      [1, 0, 0],
      [0, 1, -1 * targetCanvasSize.height],
      [0, 0, 1],
    ]);

    let matrixTransform2CanvasCoordSystem = matrix([
      [1, 0, 0],
      [0, -1, 0],
      [0, 0, 1],
    ]);

    // Net single combined transfomation while maintaining associativity/commutativity
    this.matrixTransformationGlobal2Canvas = multiply(
      matrixTransform2CanvasCoordSystem,
      multiply(
        matrixTranslateCanvasOrigin2CameraFovCanvasOrigin,
        matrixScaleWorldUnit2CanvasUnit
      )
    );
  }

  /**
   * Takes point object (x, y) in global coordinate system and returns point object with
   * coordinates on canvas
   */
  convert = (point) => {
    if (!this.matrixTransformationGlobal2Canvas) {
      console.err("Initialize converter before converting points");
      return;
    }

    // homogeneous matrix
    let matrixPointVector = matrix([[point.x], [point.y], [1]]);

    let matrixConverted = multiply(
      this.matrixTransformationGlobal2Canvas,
      matrixPointVector
    );

    return {
      x: matrixConverted.subset(index(0, 0)),
      y: matrixConverted.subset(index(1, 0)),
    };
  };
}
