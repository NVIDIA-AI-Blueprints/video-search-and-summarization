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


import update from "immutability-helper";

export const calibId = "0001";
export const roiId = "0002";
export const calValId = "0003";
export const linksId = "0004";
export const corId = "0005";

export const cartCalibId = "0006";
export const cartValId = "0007";
export const cartTripwireId = "0008";
export const cartRoiId = "0009";
export const cartTripDirId = "0010";

export const tripwireId = "0011";
export const tripDirId = "0012";

export const floorPlanCalibId = "0013";
export const floorPlanCalibMapId = "0014";
export const floorPlanValId = "0015";
export const floorPlanRoiId = "0016";
export const floorPlanTripwireId = "0017";
export const floorPlanTripDirId = "0018";

export const camPlacementId = "0019";
export const camPlacementMapId = "0020";

export const MAX_PLACE_SELECTION = 2;


export const colorMapping = {
  red: "#FF0000",
  orange: "#FE9A76",
  yellow: "#FFD700",
  olive: "#32CD32",
  green: "#90EE90",
  teal: "#008080",
  blue: "#0E6EB8",
  violet: "#EE82EE",
  purple: "#B413EC",
  pink: "#FF1493",
  brown: "#A52A2A",
  gray: "#A0A0A0",
  black: "#000000"
}


export const modalLayer1 = 10;
export const modalLayer2 = 500;
export const alertLayer = modalLayer2 + 1;

export const shortcuts = "1234567890qwe";


export const directions = {
  N: 0,
  NNE: 22.5,
  NE: 45,
  ENE: 67.5,
  E: 90,
  ESE: 112.5,
  SE: 135,
  SSE: 157.5,
  S: 180,
  SSW: 202.5,
  SW: 225,
  WSW: 247.5,
  W: 270,
  WNW: 292.5,
  NW: 315,
  NNW: 337.5
};

export const directionsOptions = Object.keys(directions).map(
  (direction, index) => {
    return { key: index, value: direction, text: direction };
  }
);

export const mmsHost = {
  nvMms: "nvMms",
  wowza: "wowza"
};

export const mmsHostOptions = Object.keys(mmsHost).map(
  (mmsHost, index) => {
    return { key: index, value: mmsHost, text: mmsHost };
  }
);

export const mmsProtocol = {
  webrtc: "webrtc",
  hls: "hls"
};

export const mmsProtocolOptions = Object.keys(mmsProtocol).map(
  (mmsProtocol, index) => {
    return { key: index, value: mmsProtocol, text: mmsProtocol };
  }
);


/**
 * Converts a point drawn on the image canvas to an object
 * @param {unkown} p e.latlng
 * @return {object} {lat: val, lng: val}
 */
export function convertPoint(p) {
  return {
    lat: p.lat,
    lng: p.lng
  };
}

/**
 * Lighten a color
 * @param {string} col Color
 * @param {number} amt to lighten
 */
export function lighten(col, amt) {
  let usePound = false;
  if (col[0] === "#") {
    col = col.slice(1);
    usePound = true;
  }
  const num = parseInt(col, 16);
  let r = (num >> 16) + amt;
  if (r > 255) r = 255;
  else if (r < 0) r = 0;
  let b = ((num >> 8) & 0x00ff) + amt;
  if (b > 255) b = 255;
  else if (b < 0) b = 0;
  let g = (num & 0x0000ff) + amt;
  if (g > 255) g = 255;
  else if (g < 0) g = 0;
  return (usePound ? "#" : "") + (g | (b << 8) | (r << 16)).toString(16);
}

/**
 * Generate an ID
 * @return {string} ID string
 */
export function genId() {
  return (
    Math.random()
      .toString(36)
      .substring(2, 15) +
    Math.random()
      .toString(36)
      .substring(2, 15)
  );
}

/**
 * Data validation for data input forms
 * @param {string} content Content type (i.e. form input label)
 * @param {string} value Data value (i.e. data input into form)
 * @return {object} {re: regex string, value: updated input value}
 */
export function formDataValidation(content, value) {
  let re;
  switch (content) {
    case "majorRoad":
    case "minorRoad":
    case "cardinalDirection":
      if (value.length >= 400) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /^[a-zA-z0-9_ ]+$/;
      break;
    case "calibrationType":
      if (value.length >= 400) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /^[a-zA-z0-9_ ]+$/;
      break;
    case "name":
    case "sensorName":
    case "cityPlace":
      if (value.length >= 200) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /^[a-zA-z0-9_ \\-]+$/;
      break;
    case "webApiUrl":
      re = /^(https?):\/\/(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?::\d{1,5})?\/?$|^(https?):\/\/\d{1,3}(\.\d{1,3}){3}(:\d{1,5})?\/?$/;      break;
    case "mmsUrl":
      if (value.length >= 1000) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /.*/;
      break;
    case "rtspURL":
      re = /rtsp:(.*)/;
      break;
    case "mapFile":
      re = /(.*)(bz2|pbf)/;
      break;
    case "originLat":
    case "latitude":
      re = /^-?[0-9]\d*(\.\d+)?$/;
      value = Number(value);
      if (value > 85 || value < -85 || value === 0) value = "false";
      break;
    case "longitude":
    case "originLng":
      re = /^-?[0-9]\d*(\.\d+)?$/;
      value = Number(value);
      if (value > 180 || value < -180 || value === 0) value = "false";
      break;
    case "description":
      if (value.length >= 1000) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /.*/;
      break;
    case "sensorId":
    case "deviceId":
    case "mapAPIKey":
    case "videoURL":
    case "placeType":
    case "roomPlace":  
    // case "place"
      if (value.length >= 1000) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /.*/;
      break;
    case "fps":
      re = /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$/;
      value = Number(value);
      break;
    case "fieldOfView":
      re = /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$/;
      value = Number(value);
      break;
    case "direction":
        re = /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$/;
        value = Number(value);
        if (value > 360 || value < 0) value = "false";

        break;
    case "depth":

        re = /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$/;
        value = Number(value);
        break;
    case "mmsInfo_protocol":
      if (value.length >= 200) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /^[a-zA-z0-9_ ]+$/;
      break;
    case "mmsInfo_type":
      if (value.length >= 200) {
        // re will match nothing, which is intended since string is too long
        re = /a^/;
        break;
      }
      re = /^[a-zA-z0-9_ ]+$/;
      break;
    case "mmsInfo_host":
      re = /http:(.*)/;
      break;
    case "invertImXpad":
    case "invertImYpad":
    case "invertImWidth":
    case "invertImHeight":
    // case "xcoord":
    // case "ycoord":
      re = /^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)$/;
      value = Number(value);
      break;
    default:
      re = /.*/;
  }

  return { re: re, value: value };
}

/**
 * Set that an input field in a form has been edited.
 * @param {object} isEdited Each key represents an input field in the form and
 * has value true or false
 * @param {string} content Key representing the input field that was edited
 * @return {object} The same as the input isEdited, but with the content key
 * set to false
 */
export function setEdited(isEdited, content) {
  if (!isEdited[content]) {
    isEdited = update(isEdited, {
      [content]: { $set: true }
    });
  }
  return isEdited;
}

/**
 * Update the value entered in the input field within a form
 * @param {object} dataValues Each key represents an input field in the form and
 * stores the value for that key
 * @param {string} content Key representing the input field to change the value of
 * @param {varies} value Value input into content input field
 * @return {object} The same as the dataValues input except the value of key
 * content is set to the passed value
 */
export function updateDataValue(dataValues, content, value) {
  dataValues = update(dataValues, {
    [content]: { $set: value }
  });
  return dataValues;
}

/**
 * Check if the value entered in the input field meets certain restrictions
 * @param {object} dataValidation Each key represents an input field in the form and
 * has value either true or false depending on if it satisfies constraints
 * @param {string} content Key repesenting the input field to check the value of
 * @param {varies} rawValue Value input into content input field
 * @return {object} The same as dataValidation except the value of the key
 * content has been changed to either true or false depending on the constraints
 * check
 */
export function updateDataValidation(dataValidation, content, rawValue) {
  const { re, value } = formDataValidation(content, rawValue);

  if (re.test(value)) {
    if (!dataValidation[content]) {
      dataValidation = update(dataValidation, {
        [content]: { $set: true }
      });
    }
    return dataValidation;
  }
  dataValidation = update(dataValidation, {
    [content]: { $set: false }
  });
  return dataValidation;
}

export const colors = [
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",
  "red",
  "blue",
  "green",
  "violet",
  "orange",
  "brown",
  "yellow",
  "olive",
  "teal",
  "purple",
  "pink",
  "black",

];
