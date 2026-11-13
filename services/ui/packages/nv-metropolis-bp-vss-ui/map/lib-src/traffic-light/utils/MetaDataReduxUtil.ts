// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { createSlice } from "../../common/warehouse/shell/js/WarehouseStore";

/**
 * Store util to add processed metadata for ITS entities.
 */

const slice = createSlice({
  name: "metadata",
  initialState: {},
  reducers: {
    updateMetaData: function (_state, action) {
      const next = {};
      next.sensorsMap = new Map();
      next.intersectionsMap = new Map();
      next.corridor2IntersectionsMap = new Map();
      next.cityConfig = {};

      action.payload.sensors.forEach((sensor) => {
        next.sensorsMap.set(sensor.id, sensor);

        if (sensor.place && sensor.place[0].name === "city") {
          if (!next.cityConfig.name) {
            next.cityConfig["name"] = sensor.place[0].value;
          } else if (next.cityConfig.name !== sensor.place[0].value) {
            throw Error(`All sensors don't have same city`);
          }

          if (!sensor.place[1] || sensor.place[1].name !== "intersection") {
            throw Error(`No intersection defined for sensor=${sensor.id}`);
          }

          const intersectionName = sensor.place[1].value;

          if (!next.intersectionsMap.has(intersectionName)) {
            next.intersectionsMap.set(intersectionName, {
              name: intersectionName,
              geoLocation: {},
              corridorsSet: new Set(),
              sensorIds: [],
            });
          }
          next.intersectionsMap
            .get(intersectionName)
            .sensorIds.push(sensor.id);
        } else {
          throw Error(`sensor=${sensor.id} has no city defined`);
        }
      });

      let cityLat = 0;
      let cityLng = 0;
      let sensorsCount = 0;
      next.intersectionsMap.forEach((config) => {
        let lat = 0;
        let lng = 0;

        config.sensorIds.forEach((id) => {
          lat += next.sensorsMap.get(id).geoLocation.lat;
          lng += next.sensorsMap.get(id).geoLocation.lng;
          cityLat += next.sensorsMap.get(id).geoLocation.lat;
          cityLng += next.sensorsMap.get(id).geoLocation.lng;
        });

        config.geoLocation = {
          lat: lat / config.sensorIds.length,
          lng: lng / config.sensorIds.length,
        };

        sensorsCount += config.sensorIds.length;
      });

      next.cityConfig.geoLocation = {
        lat: cityLat / sensorsCount,
        lng: cityLng / sensorsCount,
      };

      if (!action.payload.corridors) {
        console.warn("No corridors info in metadata");
      } else {
        action.payload.corridors.forEach((corridor) => {
          if (!corridor.name) {
            console.error("No name in one of the corridor metadata");
            return;
          }

          let corridorName;
          const regex = /corridor=([^/]+)/;
          const match = corridor.name.match(regex);

          if (match) {
            corridorName = match[1];
          } else {
            console.error(
              `No corridor name set in the corridor metadata for ${corridor.name}`
            );
            return;
          }

          let intersectionsSet = new Set();

          if (!corridor.sensors) {
            console.warn(`No sensors listed under corridor=${corridorName}`);
          } else {
            corridor.sensors.forEach((sensorId) => {
              const sensorConfig = next.sensorsMap.get(sensorId);
              if (!sensorConfig) {
                console.warn(
                  `No metadata found for sensor=${sensorId} listed under corridor=${corridorName}`
                );
              } else if (
                sensorConfig &&
                sensorConfig.place &&
                sensorConfig.place.length > 1
              ) {
                intersectionsSet.add(
                  next.sensorsMap.get(sensorId).place[1].value
                );
              }
            });
          }

          let intersectionNames = [];
          intersectionsSet.forEach((intersectionName) => {
            intersectionNames.push(intersectionName);
            let intersectionConfig =
              next.intersectionsMap.get(intersectionName);
            if (!intersectionConfig) {
              console.warn(
                `No metadata of any sensors found for /intersection=${intersectionName} under corridor=${corridorName}`
              );
            } else {
              intersectionConfig.corridorsSet.add(corridorName);
            }
          });
          next.corridor2IntersectionsMap.set(corridorName, intersectionNames);
        });
      }

      return next;
    },
  },
});

const MetaDataReduxUtil = {
  slice: slice,
};

export default MetaDataReduxUtil;
