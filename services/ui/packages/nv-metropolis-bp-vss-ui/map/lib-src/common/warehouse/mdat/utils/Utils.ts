// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import React from "react";
import Moment from "moment";
/**
 * Converts ITS specific camera timestamp UTC String to epoch milliseconds
 *
 * @param {string} timestamp UTC string time
 * @return {number} time epoch time in milliseconds
 */
export function CameraTsToMs(ts) {
  const unformattedTs = ts.split("_UTC")[0];
  const unformattedTimeZoneOffset = ts.split("_UTC")[1];
  const formattedTimeZoneOffset = unformattedTimeZoneOffset
    .split("_")
    .join(":");
  const formattedTs = Moment.utc(
    unformattedTs + " " + formattedTimeZoneOffset,
    ["MM_DD_YYYY_hh_mm_ss_SSS_A ZZ"]
  ).toISOString();
  return new Date(formattedTs).getTime();
}

/**
 * Parses App specific place titles with their types from more generic
 * Metropolis SDK placeName string and returns a place info object with
 * names and types and Object property value pairs
 *
 * @param {string} placeName string to parse from
 * @return {object} placeNamesInfo object with property value pairs presenting 'type': 'name'
 */
export function ParseMdxPlaceString(str) {
  if (typeof str === "string" || str instanceof String) {
    let arrayTitleTypePairStrings = str.split("/");
    let arrayTitleTypePairs = [];
    arrayTitleTypePairStrings.forEach((s) => {
      let titleTypePair = s.split("=");
      arrayTitleTypePairs.push([titleTypePair[0], titleTypePair[1]]);
    });
    return Object.fromEntries(arrayTitleTypePairs);
  } else {
    throw new TypeError("Expected URL string");
  }
}

// MDX Place can also be presented as an array of {name, value} pairs.
// This function constructs a string out of it. If a cut off level is
// provided then the resulting string contains levels only upto the
// requested level
export function MdxPlaceArray2String(arr, cutOffPlaceLevel) {
  let str = "";

  if (!(arr && Array.isArray(arr))) {
    return undefined;
  }

  let foundCutOffPlaceLevel = false;
  for (let i = 0; i < arr.length; i++) {
    str += `${arr[i].name}=${arr[i].value}`;

    if (cutOffPlaceLevel && arr[i].name === cutOffPlaceLevel) {
      foundCutOffPlaceLevel = true;
      break;
    }

    if (i < arr.length - 1) {
      str += "/";
    }
  }

  if (cutOffPlaceLevel && !foundCutOffPlaceLevel) {
    console.error(
      `No ${cutOffPlaceLevel} found in place array ${JSON.stringify(arr)}`
    );
    return undefined;
  }

  return str;
}

export const DeriveMdxSensorRegion = (sensor) => {
  let globalRegionName;
  let absoluteRegionName;

  if (sensor.region) {
    absoluteRegionName = MdxPlaceArray2String(
      sensor.place,
      sensor.region.placeLevel
    );

    // assumption: if region present explictly, then the global map region has
    // to be one level above the region.
    let globalRegionPlaceLevelIndex;

    sensor.place.forEach((pair, levelIndex) => {
      if (sensor.region.placeLevel === pair.name) {
        globalRegionPlaceLevelIndex = levelIndex - 1;
      }
    });

    if (globalRegionPlaceLevelIndex >= 0) {
      globalRegionName = MdxPlaceArray2String(
        sensor.place,
        sensor.place[globalRegionPlaceLevelIndex].name
      );
    }
  } else {
    // if no region present explicitly, then the whole place heirarchy is a region
    absoluteRegionName = MdxPlaceArray2String(sensor.place);
    globalRegionName = absoluteRegionName;
  }

  return [absoluteRegionName, globalRegionName];
};

/**
 * Beautifies string by removing underscore
 *
 * @param {string} name string
 * @return {string} beautified string
 */
export function BeautifyNameString(str) {
  return str && str.replace(/_/g, " ");
}

// /**
//  * Checks if Camera Video Source is Wowza + HLS
//  *
//  * @param {object} MDX Schema Camera Metadata JSON Object
//  * @return {boolean}
//  */
// export function IsCameraMediaValidWowzaHls(camera) {
//   if (!camera.mmsInfo) return false;
//   if (camera.mmsInfo.type !== 'wowza' || camera.mmsInfo.protocol !== 'hls') return false;
//   // videoURL param contains the specific camera media path on Wowza server. Hence, mandatory.
//   if (!camera.videoURL || camera.videoURL === '') return false;
//   return true;
// }

// /**
//  * Checks if Camera Video Source is NvMMS + WebRTC
//  *
//  * @param {object} MDX Schema Camera Metadata JSON Object
//  * @return {boolean}
//  */
// export function IsCameraMediaValidNvmmsWebrtc(camera) {
//   if (!camera.mmsInfo) return false;
//   if (camera.mmsInfo.type !== 'nvMms' || camera.mmsInfo.protocol !== 'webrtc') return false;
//   // NvMMS Server IP required
//   if (!camera.mmsInfo.host || camera.mmsInfo.host === '') return false;
//   // deviceId is required by NvMMS to locate WebRTC stream for the camera
//   if (!camera.mmsInfo.deviceId || camera.mmsInfo.deviceId === '') return false;
//   return true;
// }

export const MsToHMSsssString = (ms) => {
  ms = Math.floor(ms);
  let h = Math.floor(ms / (1000 * 60 * 60));
  let m = Math.floor(ms / (1000 * 60) - h * 60);
  let sec = Math.floor(ms / 1000 - h * 60 * 60 - m * 60);
  let milli = ms - h * 60 * 60 * 1000 - m * 60 * 1000 - sec * 1000;

  return (
    "" +
    (h + "").padStart(2, "0") +
    ":" +
    (m + "").padStart(2, "0") +
    ":" +
    (sec + "").padStart(2, "0") +
    "." +
    (milli + "").padStart(3, "0")
  );
};

/**
 * VideoElement Shared Default Common Properties
 */
// TODO: duplicate in other apps/pkgs utils
export const VideoElementDefaultConfig = {
  htmlTagOptions: {
    height: "16.875vw",
    width: "30vw",
  },
};

/*
 * Get requested attribute value from a sensor config object
 */
export function GetSensorAttr(sensorConfig, attrName) {
  if (!sensorConfig) {
    return undefined;
  }

  if (!sensorConfig.attributes || !Array.isArray(sensorConfig.attributes)) {
    console.warn(
      `No 'attributes' array in sensor config for id: ${sensorConfig.id}`
    );
    return undefined;
  }

  for (const attr of sensorConfig.attributes) {
    if (attr.name === attrName) {
      return attr.value;
    }
  }

  return undefined;
}
