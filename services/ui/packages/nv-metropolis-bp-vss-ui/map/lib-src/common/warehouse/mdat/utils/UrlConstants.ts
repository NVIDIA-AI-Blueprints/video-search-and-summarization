// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
// Based on Web APIs OpenAPI spec:
// https://gitlab-master.nvidia.com/metromind/mdat/web-apis/-/blob/v3.1.1/app/specification/openapi-governance.json?ref_type=heads

// web api URL constants
const ConfigUrlBaseSuffix = "/config";
export const CalibrationUrlSuffix = `${ConfigUrlBaseSuffix}/calibration`;
export const CalibrationUrlImageSuffix = `${CalibrationUrlSuffix}/image`;
export const CalibrationUrlImagesSuffix = `${CalibrationUrlSuffix}/images`;
export const CalibrationUrlImageMetadataSuffix = `${CalibrationUrlSuffix}/image-metadata`;
const ClusteringUrlBaseSuffix = "/clustering";
export const ClusteringBehaviorUrlSuffix = `${ClusteringUrlBaseSuffix}/behavior`;
export const ClusteringAddLabelUrlSuffix = `${ClusteringUrlBaseSuffix}/add-label`;
export const EventsTripwireUrlSuffix = "/events/tripwire";
export const EventsRoiUrlSuffix = "/events/roi";
export const FramesUrlSuffix = "/frames";
export const FramesUrlPtsSuffix = `${FramesUrlSuffix}/pts`;
export const FramesUrlProximityDetectionSuffix = `${FramesUrlSuffix}/proximity-detection`;
export const FramesUrlEnhancedSuffix = `${FramesUrlSuffix}/enhanced`;
const MetricsUrlBaseSuffix = "/metrics";
export const MetricsOccupancyTripwireUrlSuffix = `${MetricsUrlBaseSuffix}/occupancy/tripwire`;
export const MetricsTripwireCountsUrlSuffix = `${MetricsUrlBaseSuffix}/tripwire/counts`;
export const MetricsLastProcessedTimestampUrlSuffix = `${MetricsUrlBaseSuffix}/last-processed-timestamp`;
export const MetricsOccupancyResetUrlSuffix = `${MetricsUrlBaseSuffix}/occupancy/reset`;
export const MetricsAverageSpeedUrlSuffix = `${MetricsUrlBaseSuffix}/average-speed`;
export const MetricsFlowrateUrlSuffix = `${MetricsUrlBaseSuffix}/flowrate`;
export const MetricsAverageSpeedWithFlowrateUrlSuffix = `${MetricsUrlBaseSuffix}/average-speed-with-flowrate`;
export const MetricsAverageSpeedWithTravelTimeUrlSuffix = `${MetricsUrlBaseSuffix}/average-speed-with-travel-time`;
const MetricsRoadNetworkUrlSuffixBaseSub = `/road-network`;
export const RoadNetworkUrlSuffix = `${ConfigUrlBaseSuffix}${MetricsRoadNetworkUrlSuffixBaseSub}`;
export const MetricsRoadNetworkSegmentSpeedUrlSuffix = `${MetricsUrlBaseSuffix}${MetricsRoadNetworkUrlSuffixBaseSub}/segment-speed`;
const TrackerUrlSuffixBase = "/tracker";
export const TrackerBehaviorLocationsUrlSuffix = `${TrackerUrlSuffixBase}/behavior-locations`;
export const TrackerUniqueObjectsUrlSuffix = `${TrackerUrlSuffixBase}/unique-objects`;
export const TrackerUniqueObjectCountWithLocationsUrlSuffix = `${TrackerUrlSuffixBase}/unique-object-count-with-locations`;
export const TrackerQueryByExampleUrlSuffix = `${TrackerUrlSuffixBase}/query-by-example`;
export const UploadConfigFileUrlSuffix = `${ConfigUrlBaseSuffix}/upload-file`;
export const AlertsUrlSuffix = "/alerts";
export const AlertsSevereUrlSuffix = `${AlertsUrlSuffix}/severe`;
export const IncidentsUrlSuffix = "/incidents";
export const IncidentsSevereUrlSuffix = `${IncidentsUrlSuffix}/severe`;
