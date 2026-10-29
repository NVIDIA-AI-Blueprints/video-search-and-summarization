// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { matrix, multiply, index } from "mathjs";

/**
 * Default Util class to convert points from HTML UI Canvas coordinate system to
 * source sensor's view of world coordinate system
 */
export default class Canvas2WorldCartesianCoordinateConverter {
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
    // from world global origin to camera visible area origin, and vice versa,
    // is required for any conversion of a new point given in world global
    // coordinates to HTML canvas coordinates.

    // Compute x, y axes scaling factors to map canvas units to world
    // units
    let canvasPixelScalingFactorX =
      (1.0 * sensorViewWorldFOVSize.width) / targetCanvasSize.width;
    let canvasPixelScalingFactorY =
      (1.0 * sensorViewWorldFOVSize.height) / targetCanvasSize.height;

    // compute transformation matrix for a point on UI canvas to world view coordinate
    // transformation =
    //   (translation from canvas upside down coordinate system) *
    //   (scaling from canvas to world size in pixels)

    // using homogeneous matrices in 3D to solve for 2D

    // Scale to world view size
    // Backend stores values of points in meters, while the top view FOV is in cms
    // So scaling between meters to cms is also needed.
    let matrixScaleCanvasUnit2WorldUnit = matrix([
      [0.01 * canvasPixelScalingFactorX, 0, 0],
      [0, 0.01 * canvasPixelScalingFactorY, 0],
      [0, 0, 1],
    ]);

    // Convert to canvas upside down Y axis system
    //
    // First, convert the Y coordinate to match world's upside down Y axis
    // system.
    // Then, translate scaled world Y coordinate upward to top rightownward to bottom left.
    let matrixTranslateSensorViewFOVOrigin2CanvasOrigin = matrix([
      [1, 0, 0],
      [0, 1, targetCanvasSize.height],
      [0, 0, 1],
    ]);

    let matrixTransform2WorldCoordSystem = matrix([
      [1, 0, 0],
      [0, -1, 0],
      [0, 0, 1],
    ]);

    // Net single combined transfomation while maintaining associativity/commutativity
    this.matrixTransformationCanvas2World = multiply(
      matrixScaleCanvasUnit2WorldUnit,
      multiply(
        matrixTranslateSensorViewFOVOrigin2CanvasOrigin,
        matrixTransform2WorldCoordSystem
      )
    );
  }

  /**
   * Takes point object (x, y) in UI Canvas Coordinate system and returns point object in
   * world view coordinate system
   */
  convert = (point) => {
    if (!this.matrixTransformationCanvas2World) {
      console.err("Initialize converter before converting points");
      return;
    }

    // homogeneous matrix
    let matrixPointVector = matrix([[point.x], [point.y], [1]]);

    let matrixConverted = multiply(
      this.matrixTransformationCanvas2World,
      matrixPointVector
    );

    return {
      x: matrixConverted.subset(index(0, 0)),
      y: matrixConverted.subset(index(1, 0)),
    };
  };
}
