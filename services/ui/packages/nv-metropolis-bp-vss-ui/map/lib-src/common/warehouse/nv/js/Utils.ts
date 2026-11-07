// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { GetSensorAttr } from "common/warehouse/mdat";
import { ProdBaseBackendUrl } from "./UrlConstants";
import { getEnv, isProduction, isObtainDynamicServiceUrls } from "./EnvUtils";

const Utils = {
  getMediaServiceBaseUrl: (sensorConfig) => {
    if (!(isProduction() && isObtainDynamicServiceUrls())) {
      return getEnv("REACT_APP_MEDIA_STREAMING_URL_BASE");
    }

    // Production mode with requirement to obtain dynamic service URLs. Use Ingress Path
    if (!sensorConfig) {
      console.error(
        "[getMediaServiceBaseUrl]: no sensor metadata config passed"
      );
      return undefined;
    }

    if (!getEnv("REACT_APP_MEDIA_STREAMING_API_VERSION")) {
      return undefined;
    }

    let url;
    const sensorSource = GetSensorAttr(sensorConfig, "source");

    if (sensorSource === "nvstreamer") {
      url = `${ProdBaseBackendUrl()}/nvstreamer`;
    } else if (sensorSource === "vst") {
      url = `${ProdBaseBackendUrl()}/vms`;
    } else if (sensorSource === "mms") {
      url = `${ProdBaseBackendUrl()}/mms`;
    }

    if (!url) {
      console.error(
        '[getMediaServiceBaseUrl]: Unrecognized "source" attribute value for sensor ' +
          sensorConfig.id
      );
      return undefined;
    }

    return `${url}/api/${getEnv("REACT_APP_MEDIA_STREAMING_API_VERSION")}`;
  },
};

export default Utils;
