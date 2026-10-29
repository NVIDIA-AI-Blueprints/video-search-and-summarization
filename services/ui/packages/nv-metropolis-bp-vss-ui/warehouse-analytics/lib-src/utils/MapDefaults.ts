// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { getEnv } from "common/warehouse";

const MapDefaults = {
  get apiKey() {
    return getEnv("REACT_APP_GOOGLE_MAPS_API_KEY");
  },
  options: {
    maxZoom: 21,
    minZoom: 12,
    cameraControl: true,
    cameraControlOptions: {
      position: 8, // google.maps.ControlPosition.RIGHT_CENTER
    },
  },
};

export default MapDefaults;
