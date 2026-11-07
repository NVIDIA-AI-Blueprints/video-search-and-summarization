// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { createSlice } from "@reduxjs/toolkit";
import _ from "lodash";

let PlaceArrayToKVPairs = (array) => {
  let kvPairs = {};
  array.forEach((entry) => {
    kvPairs[entry.name] = entry.value;
  });
  return kvPairs;
};

/**
 * Process scale factor from sensor, updating the accumulated value
 */
function processSensorScaleFactor(sensor, currentScaleFactor) {
  if (currentScaleFactor === -1.0) {
    return sensor.scaleFactor;
  }
  if (currentScaleFactor !== sensor.scaleFactor) {
    return undefined;
  }
  return currentScaleFactor;
}

/**
 * Process translation coordinates from sensor, checking consistency
 */
function processSensorTranslation(sensor, currentTranslation) {
  if (!sensor.translationToGlobalCoordinates) {
    return currentTranslation;
  }
  if (currentTranslation === undefined) {
    return sensor.translationToGlobalCoordinates;
  }
  if (
    !_.isEqual(currentTranslation, sensor.translationToGlobalCoordinates)
  ) {
    return undefined;
  }
  return currentTranslation;
}

/**
 * Validate and update city name from sensor place
 */
function processCityName(state, sensorPlaceKVPairs, sensorId) {
  if (!sensorPlaceKVPairs.city) {
    return;
  }
  if (state.cityConfig.name === "") {
    state.cityConfig.name = sensorPlaceKVPairs.city;
  } else if (state.cityConfig.name !== sensorPlaceKVPairs.city) {
    throw Error(
      "new city name " +
        sensorPlaceKVPairs.city +
        " for sensor id: " +
        sensorId +
        ". Expected city name: " +
        state.cityConfig.name +
        "." +
        " Only single city supported!"
    );
  }
}

/**
 * Validate building and room exist in sensor place
 */
function validatePlaceStructure(sensorPlaceKVPairs, sensorId) {
  if (!sensorPlaceKVPairs.building) {
    throw Error(
      'No parent building found in "place" in ' +
        "metadata for sensor with id: " +
        sensorId
    );
  }
  if (!sensorPlaceKVPairs.room) {
    throw Error(
      'No parent room found in "place" in ' +
        "metadata for sensor with id: " +
        sensorId
    );
  }
}

/**
 * Initialize or update room and building maps
 */
function processRoomAndBuildingMaps(
  state,
  sensorPlaceKVPairs,
  sensorId
) {
  if (!state.roomsMap.has(sensorPlaceKVPairs.room)) {
    state.roomsMap.set(sensorPlaceKVPairs.room, {
      name: sensorPlaceKVPairs.room,
      geoLocation: {},
      sensorIds: new Set(),
    });
  }
  if (!state.buildingsMap.has(sensorPlaceKVPairs.building)) {
    state.buildingsMap.set(sensorPlaceKVPairs.building, {
      name: sensorPlaceKVPairs.building,
      geoLocation: {},
      rooms: new Set(),
    });
  }
  state.roomsMap.get(sensorPlaceKVPairs.room).sensorIds.add(sensorId);
  state.buildingsMap
    .get(sensorPlaceKVPairs.building)
    .rooms.add(sensorPlaceKVPairs.room);
}

/**
 * Process a single sensor and update accumulated metadata
 * Returns updated accumulators
 */
function processSensor(
  state,
  sensor,
  scaleFactorAccumulator,
  translationAccumulator
) {
  state.sensorsMap.set(sensor.id, sensor);

  const sensorPlaceKVPairs = PlaceArrayToKVPairs(sensor.place);
  processCityName(state, sensorPlaceKVPairs, sensor.id);
  validatePlaceStructure(sensorPlaceKVPairs, sensor.id);
  processRoomAndBuildingMaps(state, sensorPlaceKVPairs, sensor.id);

  const newScaleFactor = processSensorScaleFactor(
    sensor,
    scaleFactorAccumulator
  );
  const newTranslation = processSensorTranslation(
    sensor,
    translationAccumulator
  );

  return { scaleFactor: newScaleFactor, translation: newTranslation };
}

/**
 * Redux Util to add processed MetaData for entities to redux store
 */
const slice = createSlice({
  name: "metadata",
  initialState: {},
  reducers: {
    updateMetaData: function (state, action) {
      // Clean old and initialize new state
      state.sensorsMap = new Map();
      state.roomsMap = new Map();
      state.buildingsMap = new Map();
      state.cityConfig = {};

      state.cityConfig.name = action.payload.city ? action.payload.city : "";
      state.calibrationType = action.payload.calibrationType;

      let m2PxScaleFactor = -1.0;
      let translationToGlobalCoordinates;

      action.payload.sensors.forEach((sensor) => {
        const result = processSensor(
          state,
          sensor,
          m2PxScaleFactor,
          translationToGlobalCoordinates
        );
        m2PxScaleFactor = result.scaleFactor;
        translationToGlobalCoordinates = result.translation;
      });

      // Compute rooms, buildings, city's Geo Location
      let cityLat = 0;
      let cityLng = 0;
      let sensorsCount = 0;
      state.roomsMap.forEach((config) => {
        let lat = 0;
        let lng = 0;
        config.sensorIds.forEach((id) => {
          lat += state.sensorsMap.get(id).geoLocation.lat;
          lng += state.sensorsMap.get(id).geoLocation.lng;
          cityLat += state.sensorsMap.get(id).geoLocation.lat;
          cityLng += state.sensorsMap.get(id).geoLocation.lng;
        });

        config.geoLocation = {
          lat: lat / config.sensorIds.size,
          lng: lng / config.sensorIds.size,
        };

        sensorsCount += config.sensorIds.size;
      });
      state.cityConfig.geoLocation = {
        lat: cityLat / sensorsCount,
        lng: cityLng / sensorsCount,
      };
      state.buildingsMap.forEach((config) => {
        let lat = 0;
        let lng = 0;
        config.rooms.forEach((roomName) => {
          lat += state.roomsMap.get(roomName).geoLocation.lat;
          lng += state.roomsMap.get(roomName).geoLocation.lng;
          cityLat += state.roomsMap.get(roomName).geoLocation.lat;
          cityLng += state.roomsMap.get(roomName).geoLocation.lng;
        });

        config.geoLocation = {
          lat: lat / config.rooms.size,
          lng: lng / config.rooms.size,
        };
      });

      state.m2PxScaleFactor =
        m2PxScaleFactor === -1.0 ? undefined : m2PxScaleFactor;

      if (!state.m2PxScaleFactor) {
        console.warn(
          "Undefined or non-matching scaleFactor for atleast 1 sensor. " +
            "Same scaleFactor for all sensors required for global map visualization"
        );
      }

      if (translationToGlobalCoordinates === undefined) {
        console.warn(
          "Undefined or non-matching translationToGlobalCoordinates for atleast 1 sensor. " +
            "Same translationToGlobalCoordinates for all sensors required for global map visualization. " +
            "Assuming it to default {0, 0}"
        );
        translationToGlobalCoordinates = { x: 0, y: 0 };
      }

      state.translationToGlobalCoordinates = translationToGlobalCoordinates;

      return state;
    },
  },
});

const MetaDataReduxUtil = {
  slice: slice,
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: {
        // https://redux-toolkit.js.org/usage/usage-guide#working-with-non-serializable-data
        ignoredPaths: [
          "metadata.sensorsMap",
          "metadata.roomsMap",
          "metadata.buildingsMap",
          "metadata.cityConfig",
          "metadata.m2PxScaleFactor",
          "metadata.translationToGlobalCoordinates",
        ],
      },
    }),
};

export default MetaDataReduxUtil;
