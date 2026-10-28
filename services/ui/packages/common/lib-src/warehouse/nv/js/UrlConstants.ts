// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import {
  IncidentsUrlSuffix,
  IncidentsSevereUrlSuffix,
  AlertsUrlSuffix,
  AlertsSevereUrlSuffix,
  CalibrationUrlImageMetadataSuffix,
  CalibrationUrlImageSuffix,
  CalibrationUrlImagesSuffix,
  CalibrationUrlSuffix,
  ClusteringAddLabelUrlSuffix,
  ClusteringBehaviorUrlSuffix,
  EventsTripwireUrlSuffix,
  EventsRoiUrlSuffix,
  FramesUrlProximityDetectionSuffix,
  FramesUrlEnhancedSuffix,
  FramesUrlPtsSuffix,
  FramesUrlSuffix,
  MetricsLastProcessedTimestampUrlSuffix,
  MetricsOccupancyResetUrlSuffix,
  MetricsOccupancyTripwireUrlSuffix,
  MetricsTripwireCountsUrlSuffix,
  MetricsAverageSpeedUrlSuffix,
  MetricsAverageSpeedWithFlowrateUrlSuffix,
  MetricsAverageSpeedWithTravelTimeUrlSuffix,
  MetricsFlowrateUrlSuffix,
  MetricsRoadNetworkSegmentSpeedUrlSuffix,
  RoadNetworkUrlSuffix,
  TrackerBehaviorLocationsUrlSuffix,
  TrackerQueryByExampleUrlSuffix,
  TrackerUniqueObjectsUrlSuffix,
  TrackerUniqueObjectCountWithLocationsUrlSuffix,
  UploadConfigFileUrlSuffix,
} from "common/warehouse/mdat";

import { getEnv, isDevelopment, isProduction, isObtainDynamicServiceUrls, stripTrailingSlash } from "./EnvUtils";

if (isProduction() && isObtainDynamicServiceUrls()) {
  if (!getEnv("REACT_APP_ANALYTICS_API_BASE_URL_VERSION"))
    console.error(
      "No REACT_APP_ANALYTICS_API_BASE_URL_VERSION in .env.production or runtime env"
    );
  if (!getEnv("REACT_APP_MEDIA_STREAMING_API_VERSION"))
    console.error(
      "No REACT_APP_MEDIA_STREAMING_API_VERSION in .env.production or runtime env"
    );
} else {
  // Support REACT_APP_API_URL as fallback for backward compatibility with older Docker configurations
  if (!getEnv("REACT_APP_API_URL_BASE") && !getEnv("REACT_APP_API_URL"))
    console.warn("No REACT_APP_API_URL_BASE in .env.development");
  if (!getEnv("REACT_APP_MEDIA_STREAMING_URL_BASE"))
    console.warn("No REACT_APP_MEDIA_STREAMING_URL_BASE in .env.development");
}

// MDX deployment on NGC prod may happen for per user basis,
// thus changing the url paths to the different services.
// Endpoints URLs for Web API/Media Streaming etc. used by the
// UI need to built from the UI URL context. Additionally, the
// UI static files path changes as those may not be served at '/'.
// These functions help generate the required endpoints URLs.
export const ProdBaseBackendUrl = () => {
  const pattern = /\/ui.*$/;
  return `${window.location.origin}${window.location.pathname.replace(
    pattern,
    "/"
  )}`;
};

export const ProdBaseUiServerUrl = () => {
  return `${window.location.origin}${window.location.pathname}`;
};

export const AppConstants = {
  APP_LOGS_CONSOLE_NAME: "AppLogs",
  get API_URL_BASE() {
    if (!(isProduction() && isObtainDynamicServiceUrls())) {
      return stripTrailingSlash(
        getEnv("REACT_APP_API_URL_BASE") || getEnv("REACT_APP_API_URL") || "",
      );
    }
    return stripTrailingSlash(
      `${ProdBaseBackendUrl()}/api/${getEnv("REACT_APP_ANALYTICS_API_BASE_URL_VERSION")}/analytics`,
    );
  },
};

// Based on Web APIs OpenAPI spec:
// https://gitlab-master.nvidia.com/metromind/mdat/web-apis/-/blob/v3.1/app/specification/openapi-governance.json?ref_type=heads

const lazyUrl = (suffix) => ({
  toString() {
    return `${AppConstants.API_URL_BASE}${suffix}`;
  },
  valueOf() {
    return `${AppConstants.API_URL_BASE}${suffix}`;
  },
  [Symbol.toPrimitive]() {
    return `${AppConstants.API_URL_BASE}${suffix}`;
  },
});

// API Endpoints Urls — resolved on use so Next runtime env is available.
export const CalibrationUrl = lazyUrl(CalibrationUrlSuffix);
export const RoadNetworkUrl = lazyUrl(RoadNetworkUrlSuffix);
export const CalibrationImageUrl = lazyUrl(CalibrationUrlImageSuffix);
export const CalibrationImagesUrl = lazyUrl(CalibrationUrlImagesSuffix);
export const CalibrationImageMetadataUrl = lazyUrl(CalibrationUrlImageMetadataSuffix);
export const ClusteringBehaviorUrl = lazyUrl(ClusteringBehaviorUrlSuffix);
export const ClusteringAddLabelUrl = lazyUrl(ClusteringAddLabelUrlSuffix);
export const EventsTripwireUrl = lazyUrl(EventsTripwireUrlSuffix);
export const EventsRoiUrl = lazyUrl(EventsRoiUrlSuffix);
export const FramesUrl = lazyUrl(FramesUrlSuffix);
export const FramesProximityDetectionUrl = lazyUrl(FramesUrlProximityDetectionSuffix);
export const FramesEnhancedUrl = lazyUrl(FramesUrlEnhancedSuffix);
export const FramesPtsUrl = lazyUrl(FramesUrlPtsSuffix);
export const MetricsOccupancyTripwireUrl = lazyUrl(MetricsOccupancyTripwireUrlSuffix);
export const MetricsTripwireCountsUrl = lazyUrl(MetricsTripwireCountsUrlSuffix);
export const MetricsLastProcessedTimestampUrl = lazyUrl(MetricsLastProcessedTimestampUrlSuffix);
export const MetricsOccupancyResetUrl = lazyUrl(MetricsOccupancyResetUrlSuffix);
export const MetricsAverageSpeedUrl = lazyUrl(MetricsAverageSpeedUrlSuffix);
export const MetricsFlowrateUrl = lazyUrl(MetricsFlowrateUrlSuffix);
export const MetricsAverageSpeedWithFlowrateUrl = lazyUrl(MetricsAverageSpeedWithFlowrateUrlSuffix);
export const MetricsAverageSpeedWithTravelTimeUrl = lazyUrl(MetricsAverageSpeedWithTravelTimeUrlSuffix);
export const MetricsRoadNetworkSegmentSpeedUrl = lazyUrl(MetricsRoadNetworkSegmentSpeedUrlSuffix);
export const TrackerBehaviorLocationsUrl = lazyUrl(TrackerBehaviorLocationsUrlSuffix);
export const TrackerUniqueObjectsUrl = lazyUrl(TrackerUniqueObjectsUrlSuffix);
export const TrackerUniqueObjectCountWithLocationsUrl = lazyUrl(TrackerUniqueObjectCountWithLocationsUrlSuffix);
export const TrackerQueryByExampleUrl = lazyUrl(TrackerQueryByExampleUrlSuffix);
export const UploadConfigFileUrl = lazyUrl(UploadConfigFileUrlSuffix);
export const AlertsUrl = lazyUrl(AlertsUrlSuffix);
export const AlertsSevereUrl = lazyUrl(AlertsSevereUrlSuffix);
export const IncidentsUrl = lazyUrl(IncidentsUrlSuffix);
export const IncidentsSevereUrl = lazyUrl(IncidentsSevereUrlSuffix);
