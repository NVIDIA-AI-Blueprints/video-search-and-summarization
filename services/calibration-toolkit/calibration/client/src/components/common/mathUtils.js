/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
 */


import { matrix, subset, index } from "mathjs";

/**
 * Flips the y (or lattidude) coordinate of figures. This is done because the
 * drawing origin (0,0) is bottom left, but it should be top left for proper
 * sensor coordinate convention.
 * @param {object} figures
 * @param {number} height The height of the sensor resolution
 * @return {object}
 */
export function flipY(figures, height) {
  // flip the y-coordinate to make origin top left of image
  // current drawing tool defaults to bottom left as origin
  const f = {};
  Object.keys(figures).forEach(label => {
    f[label] = figures[label].map(figure => {
      if (figure.type !== "polygon" && figure.type !== "polyline")
        return figure;

      let tracingOptions;
      if (figure.tracingOptions && figure.tracingOptions.enabled) {
        tracingOptions = {
          ...figure.tracingOptions,
          trace: flipPointY(figure.tracingOptions.trace)
        };
      } else {
        tracingOptions = figure.tracingOptions;
      }

      return {
        ...figure,
        points: flipPointY(figure.points, height),
        tracingOptions
      };
    });
  });
  return f;
}


/**
 * Flips the y (or lattidude) coordinate of figures. This is done because the
 * drawing origin (0,0) is bottom left, but it should be top left for proper
 * sensor coordinate convention.
 * @param {object} figures
 * @param {number} labels The height of the sensor resolution
 * @return {object}
 */
 export function flipYLabels(figures, labels) {
  // flip the y-coordinate to make origin top left of image
  // current drawing tool defaults to bottom left as origin
  const f = {};

  // const height = 1080
  Object.keys(figures).forEach(label => {
    f[label] = figures[label].map(figure => {
      console.log ("fpy", labels[0], label)
      const height = labels.filter(item => item.id === label)[0].height
      // console.log ("fpy1", height)
      // console.log("fpy2", chosenLabel[0].h eight)
      if (figure.type !== "polygon" && figure.type !== "polyline")
        return figure;

      let tracingOptions;
      if (figure.tracingOptions && figure.tracingOptions.enabled) {
        tracingOptions = {
          ...figure.tracingOptions,
          trace: flipPointY(figure.tracingOptions.trace)
        };
      } else {
        tracingOptions = figure.tracingOptions;
      }

      return {
        ...figure,
        points: flipPointY(figure.points, height),
        tracingOptions
      };
    });
  });
  return f;
}




/**
 * Flips the y (or lattidude) coordinate of for all points in the input array.
 * This is done because the drawing origin (0,0) is bottom left, but it should
 * be top left for proper sensor coordinate convention.
 * @param {array} points Array of points in the form of {lat: val, lng: val}
 * @param {number} height The height of the sensor resolution
 * @return {array} Array of flipped points in the form of {lat: val, lng: val}
 */
export function flipPointY(points, height) {
  if (points.constructor !== Array) {
    return { lat: height - points.lat, lng: points.lng };
  }
  return points.map(({ lat, lng }) => ({
    lat: height - lat,
    lng
  }));
}

/**
 * Flips the y (or lattidude) coordinate of for all points in the input array.
 * This is done because the drawing origin (0,0) is bottom left, but it should
 * be top left for proper sensor coordinate convention.
 * @param {array} points Array of points in the form of {lat: val, lng: val}
 * @param {number} height The height of the sensor resolution
 * @return {array} Array of flipped points in the form of {lat: val, lng: val}
 */
 export function addPointY(points, height) {
  if (points.constructor !== Array) {
    return { lat:  points.lat - height, lng: points.lng };
  }
  return points.map(({ lat, lng }) => ({
    lat: lat - height,
    lng
  }));
}


/**
 * Flips the y (or lattidude) coordinate of for all points in the input array.
 * This is done because the drawing origin (0,0) is bottom left, but it should
 * be top left for proper sensor coordinate convention.
 * @param {array} points Array of points in the form of {lat: val, lng: val}
 * @param {number} imXPad The pad width for the img Crop
 * @param {number} imYPad The pad height of img Crop
 * @return {array} Array of flipped points in the form of {lat: val, lng: val}
 */
 export function  padPointY(points, imXPad, imYPad, height) {
  if (points.constructor !== Array) {
    // console.log ("i get here")
    // console.log()
    return { lat: points.lat + imYPad, lng: points.lng + imXPad };
  }
  // console.log("nt")
  return points.map(({ lat, lng }) => ({
    lat:  lat + imYPad,
    lng:  lng + imXPad
  }));
}



/**
 * Convert a {lat: val, lng: val} point to a homographic coordinate vector of
 * type matrix.
 * @param {object} point Point in the form of {lat: val, lng: val}
 * @param {number} height The height of the sensor resolution
 * @return {matrix} Homographic coordinate vector
 */
export function convertLatLngToXYMatrix(point, height) {
  const newPoint = flipPointY(point, height);

  // recall lng is x and lat is y
  return matrix([newPoint.lng, newPoint.lat, 1]);
}

/**
 * Converts a point projected from image coordinates to map coordinates from a
 * matrix to a {lat: val, lng: val} object
 * @param {matrix} point Vector of projected point
 * @return {object} Projected point in the form {lat: val, lng: val}
 */
export function convertProjectedPoint(point) {
  const scale = subset(point, index(2));
  let projectedPoint = { lat: 0, lng: 0 };
  point.forEach(function(value, index) {
    if (index[0] === 1) {
      // second value is the latitude, as latitude = y
      projectedPoint.lat = value / scale;
    } else if (index[0] === 0) {
      // first value is the longitude, as longitude = x
      projectedPoint.lng = value / scale;
    }
  });
  return projectedPoint;
}

/**
 * Calculate the haversine distance between two lat/lng coordinates
 * @param {object} coords1 Coordinate in the form {lat: val, lng: val}
 * @param {object} coords2 Coordinate in the form {lat: val, lng: val}
 * @return {number} Distance between coordinates in meters
 */
export function haversineDistance(coords1, coords2) {
  function toRad(x) {
    return (x * Math.PI) / 180;
  }

  var lng1 = coords1.lng;
  var lat1 = coords1.lat;



  var lng2 = coords2.lng;
  var lat2 = coords2.lat;

  var R = 6371 * 1000; // approximate radius of earth in meters

  var x1 = lat2 - lat1;
  var dLat = toRad(x1);
  var x2 = lng2 - lng1;
  var dLon = toRad(x2);
  var a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) *
      Math.cos(toRad(lat2)) *
      Math.sin(dLon / 2) *
      Math.sin(dLon / 2);
  var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  var d = R * c;

  return d;
}

/**
 * Calculate the euclidean distance between two lat/lng coordinates
 * @param {object} coords1 Coordinate in the form {lat: val, lng: val}
 * @param {object} coords2 Coordinate in the form {lat: val, lng: val}
 * @return {number} Distance between coordinates in meters
 */
 export function euclideanDistance(coords1, coords2) {


  var lng1 = coords1.lng;
  var lat1 = coords1.lat;



  var lng2 = coords2.lng;
  var lat2 = coords2.lat;

  const dist = Math.sqrt((lng1 - lng2) ** 2 + (lat1 - lat2) ** 2) * 0.01;




  return dist;
}



/**
 * Calculate the bearing direction in degrees of a vector from coordinate 1 to
 * coordinate 2
 * @param {object} coords1 Coordinate in the form {lat: val, lng: val}
 * @param {object} coords2 Coordinate in the form {lat: val, lng: val}
 * @return {number} Bearing direction in degress (North = 0 degrees)
 */
export function calculateHeading(coords1, coords2) {
  const pi = Math.PI;
  var lng1 = (coords1.lng * pi) / 180;
  var lat1 = (coords1.lat * pi) / 180;

  var lng2 = (coords2.lng * pi) / 180;
  var lat2 = (coords2.lat * pi) / 180;

  var deltaLng = lng2 - lng1;

  var y = Math.sin(deltaLng) * Math.cos(lat2);
  var x =
    Math.cos(lat1) * Math.sin(lat2) -
    Math.sin(lat1) * Math.cos(lat2) * Math.cos(deltaLng);

  var bearing = Math.atan2(y, x);
  bearing = ((bearing * 180) / pi + 360) % 360;

  return bearing;
}

/**
 * Determine if bearing is closer to east-west or north-south
 * @param {number} bearing degree directional bearing
 * @return {array} Either "N","S" or "E","W"
 */
export function bearingToDirections(bearing) {
  bearing = bearing % 360;
  const eastWestA = [45, 135];
  const eastWestB = [225, 315];
  if (
    (bearing > eastWestA[0] && bearing < eastWestA[1]) ||
    (bearing > eastWestB[0] && bearing < eastWestB[1])
  ) {
    return ["E", "W"];
  } else {
    return ["N", "S"];
  }
}
