/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
export type CalibrationTypeEnum = 'geo' | 'cartesian' | 'floorplan' | 'mtmc' | 'image';

export type CardinalDirectionEnum =
    | 'NW'
    | 'NNW'
    | 'N'
    | 'NNE'
    | 'NE'
    | 'ENE'
    | 'E'
    | 'ESE'
    | 'SE'
    | 'SSE'
    | 'S'
    | 'SSW'
    | 'SW'
    | 'WSW'
    | 'W'
    | 'WNW';

export interface Sensor {
    id: string;
    mapAPIKey: string;
    created: string;
    modified: string;
    sensorId: string;
    height: number;
    width: number;
    isCalibrated: boolean;
    isValidated: boolean;
    calibrationType: CalibrationTypeEnum;
    coordinates: string;
    geoLocation: string;
    sensorPolygon: string;
    edgeLengths: string;
    floorPlanPolygon: string;
    gisPolygon: string;
    roiPolygon: string;
    cropRoiPolygon: string;
    floorPlanImHeight: number;
    floorPlanImWidth: number;
    imHomography: string;
    homography: string;
    mapZoom: number;
    mapCenter: string | null;
    originLat: number;
    originLng: number;
    scaleFactor: number;
    scaleFactorPolygon: string;
    scalePolygon: string;
    sensorName: string;
    cardinalDirection: CardinalDirectionEnum;
    imageUrl: string | null;
    invertImageUrl: string | null;
    floorPlanImageUrl: string | null;
    rtspURL: string;
    invertImXPad: number;
    invertImYPad: number;
    invertImWidth: number;
    invertImHeight: number;
    majorRoad: string;
    minorRoad: string;
    tripwireLines: string;
    tripDirLines: string;
    mmsInfo_protocol: string;
    mmsInfo_host: string;
    mmsInfo_type: string;
    fps: string;
    deviceId: string;
    videoURL: string;
    depth: string;
    fieldOfView: string;
    direction: string;
    view: string;
    type: string;
    project: number;
    intersection_set: number | null;
    corridor_set: number[];
    place_set: number[];
}

export interface Intersection {
    id: number;
    sensor_set: Sensor[];
    mapAPIKey: string;
    created: string;
    modified: string;
    description: string;
    numSensors: number;
    numCalibrated: number;
    lineSegments: string;
    roadLinks: string;
    linksAreDrawn: boolean;
    linksAreValid: boolean;
    mapZoom: number;
    mapCenter: string | null;
    originLat: number;
    originLng: number;
    majorRoad: string;
    minorRoad: string;
    name: string;
    project: number;
}

export interface City {
    id: number;
    sensor_set: Sensor[];
    placeTypes_set: PlaceTypes[];
    corridor_set: Corridor[];
    intersection_set: Intersection[];
    created: string;
    modified: string;
    sensorOnly: boolean;
    calibrationType: CalibrationTypeEnum;
    name: string;
    mapAPIKey: string;
    mapFile: string;
    mapZoom: number;
    mapCenter: string | null;
    originLat: number;
    originLng: number;
    mmsURL: string;
    placeTypeHierarchy: string;
    floorPlanImageUrl: string | null;
    floorPlanImHeight: number;
    floorPlanImWidth: number;
    rtspURL: string;
    coordinates: string;
    mapCoordinates: string;
    project: number;
}

export interface PlaceTypes {
    id: number;
    place_set: Place[];
    created: string;
    modified: string;
    placeType: string;
    rank: number;
    project: number;
}

export interface Place {
    id: number;
    sensor_set: Sensor[];
    created: string;
    modified: string;
    name: string;
    originLat: number;
    originLng: number;
    placeType: number;
    project: number;
}

export interface Corridor {
    id: number;
    sensor_set: Sensor[];
    mapAPIKey: string;
    created: string;
    modified: string;
    name: string;
    mapZoom: number;
    mapCenter: string | null;
    originLat: number;
    originLng: number;
    corridorShape: string;
    directions: string;
    length: number;
    project: number;
}

export interface Project {
    id: number;
    sensor_set: Sensor[];
    intersection_set: Intersection[];
    city_set: City[];
    placeTypes_set: PlaceTypes[];
    corridor_set: Corridor[];
    created: string;
    modified: string;
    name: string;
    calibrationType: CalibrationTypeEnum;
    mapAPIKey: string;
    mapFile: string;
    webApiUrl: string;
    scaleFactor: number;
    calibrationJsonTemp: string;
    imageMetaDataJsonTemp: string;
    imageFiles: string | null;
    calibrationJson: string;
    sensorMetadataCsv: string;
    imageMetaDataJson: string;
    roadNetworkJson: string;
    mmsURL: string;
    placeTypeHierarchy: string;
    floorPlanImageUrl: string | null;
    floorPlanImHeight: number;
    floorPlanImWidth: number;
    rtspURL: string;
    coordinates: string;
    mapCoordinates: string;
    mapZoom: number;
    mapCenter: string | null;
    originLat: number;
    originLng: number;
    cityPlace: string | null;
    roomPlace: string | null;
}

export interface ProjectStats {
    totalSensors: number;
    calibratedSensors: number;
    validatedSensors: number;
    hasMmsUrl: boolean;
}
