// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import "@testing-library/jest-dom/extend-expect";
import { matrix } from "mathjs";

import {
  flipY,
  flipPointY,
  convertLatLngToXYMatrix,
  convertProjectedPoint,
  haversineDistance,
  calculateHeading,
  bearingToDirections
} from "../mathUtils";

it("Test flipY correctly flips all values in figures object", () => {
  const height = Math.random() * 1000;
  const width = Math.random() * 1000;
  const figures = {
    "0001": [
      {
        type: "polygon",
        points: [
          { lat: 0.5 * height, lng: 0.5 * width },
          { lat: 0.24 * height, lng: 0.5432 * width },
          { lat: 0.75 * height, lng: 0.1 * width }
        ]
      },
      {
        type: "polyline",
        points: [
          { lat: 14, lng: 0 },
          { lat: 26, lng: 25 },
          { lat: 68, lng: 75 },
          { lat: 82, lng: 100 }
        ]
      }
    ],
    "0002": [
      {
        type: "polyline",
        points: [
          { lat: height, lng: 50 },
          { lat: 0, lng: 50 }
        ]
      }
    ]
  };
  const flippedFigures = flipY(figures, height);
  Object.keys(flippedFigures).forEach(key =>
    flippedFigures[key].forEach((figure, figIndex) =>
      figure.points.forEach((point, ptIndex) => {
        expect(point.lat).toEqual(
          height - figures[key][figIndex].points[ptIndex].lat
        );
        expect(point.lng).toEqual(figures[key][figIndex].points[ptIndex].lng);
      })
    )
  );
});

it("Test flipPointY correctly flips all values in point array", () => {
  const height = 200;
  const point = { lat: 78, lng: 91 };
  const pointsA = [point, flipPointY(point, height)];
  expect(pointsA[0].lat).toEqual(height - pointsA[1].lat);
  const pointsB = flipPointY(pointsA, height);
  pointsA.forEach((point, index) => {
    expect(point).toEqual(pointsB[pointsB.length - 1 - index]);
  });
});

it("Test convertLatLngToXYMatrix correctly converts to matrix object", () => {
  const point = { lat: 10, lng: 15 };
  const height = Math.random() * 100;
  const projectedPoint = convertLatLngToXYMatrix(point, height);
  expect(projectedPoint).toEqual(matrix([15, height - 10, 1]));
});

it("Test convertProjectedPoint correctly converts to lat/lng object", () => {
  const point = matrix([10, 5, 2]);
  const projectedPoint = convertProjectedPoint(point);
  expect(projectedPoint.lat).toEqual(5 / 2);
  expect(projectedPoint.lng).toEqual(10 / 2);
});

it("Test haversineDistance returns correct distance", () => {
  let coord1 = { lat: 42.4897, lng: -90.677 };
  let coord2 = { lat: 42.489, lng: -90.67 };
  let distance = haversineDistance(coord1, coord2);
  expect(distance).toEqual(579.2219282409903);
  coord2 = { lat: 42.4897, lng: -90.677 };
  distance = haversineDistance(coord1, coord2);
  expect(distance).toEqual(0);
});

it("Test calculateHeading returns correct heading", () => {
  let coord1 = { lat: 42.4897, lng: -90.677 };
  let coord2 = { lat: 42.489, lng: -90.67 };
  let heading = calculateHeading(coord1, coord2);
  expect(heading).toEqual(97.72046622011203);
  coord2 = { lat: 42.4897, lng: -90.677 };
  heading = haversineDistance(coord1, coord2);
  expect(heading).toEqual(0);
});

it("Test bearingToDirections returns correct directions", () => {
  const NS = ["N", "S"];
  const EW = ["E", "W"];
  const deviation = 360 * Math.floor(Math.random() * Math.floor(10));
  // Test on values between 0 and 360
  expect(bearingToDirections(0)).toEqual(NS);
  expect(bearingToDirections(45)).toEqual(NS);
  expect(bearingToDirections(45.1)).toEqual(EW);
  expect(bearingToDirections(90)).toEqual(EW);
  expect(bearingToDirections(134.9)).toEqual(EW);
  expect(bearingToDirections(135)).toEqual(NS);
  expect(bearingToDirections(180)).toEqual(NS);
  expect(bearingToDirections(225)).toEqual(NS);
  expect(bearingToDirections(225.1)).toEqual(EW);
  expect(bearingToDirections(275)).toEqual(EW);
  expect(bearingToDirections(314.9)).toEqual(EW);
  expect(bearingToDirections(315)).toEqual(NS);
  expect(bearingToDirections(360)).toEqual(NS);
  // Test with deviation on values > 360
  expect(bearingToDirections(0 + deviation)).toEqual(NS);
  expect(bearingToDirections(45 + deviation)).toEqual(NS);
  expect(bearingToDirections(45.1 + deviation)).toEqual(EW);
  expect(bearingToDirections(90 + deviation)).toEqual(EW);
  expect(bearingToDirections(134.9 + deviation)).toEqual(EW);
  expect(bearingToDirections(135 + deviation)).toEqual(NS);
  expect(bearingToDirections(180 + deviation)).toEqual(NS);
  expect(bearingToDirections(225 + deviation)).toEqual(NS);
  expect(bearingToDirections(225.1 + deviation)).toEqual(EW);
  expect(bearingToDirections(275 + deviation)).toEqual(EW);
  expect(bearingToDirections(314.9 + deviation)).toEqual(EW);
  expect(bearingToDirections(315 + deviation)).toEqual(NS);
  expect(bearingToDirections(360 + deviation)).toEqual(NS);
});
