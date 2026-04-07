<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and limitations under the License.

-->

# Smart City Incident Report

## Basic Information

| Field | Value |
|-------|-------|
| **Report Identifier** | {A unique ID for this report (e.g., numeric or alphanumeric).} |
| **Date of Incident** | {Specify the date (DD/MM/YYYY).} |
| **Start Time of Incident** | {Specify the local time (HH:MM:SS). If unknown, leave blank.} |
| **End Time of Incident** | {Specify the local time (HH:MM:SS). If unknown, leave blank.} |
| **Reporting AI Agent** | {AI Agent version} |
| **Camera IDs Involved** | {Sensor IDs/Camera IDs Involved} |

## Location Details

| Field | Value |
|-------|-------|
| **Location Description** | {Describe the incident location (street name, intersection, or landmark).} |
| **Location Coordinates** | {Include the geographic coordinates of the location (latitude, longitude).} |
| **City/County/Region/Country** | {Name the city, county, region, and country where the incident occurred.} |
| **Road Type** | {Indicate the type of road (e.g., residential street, highway, rural road, intersection).} |
| **Speed Limit at Location** | {Provide the posted speed limit (in km/h or mph) or visible via cameras.} |

## Vehicles Involved

### Vehicle {vehicle_number}

| Field | Value |
|-------|-------|
| **Vehicle ID** | {The object ID of the vehicle} |
| **Vehicle Type** | {The type of the vehicle (e.g., Car / Bus / Truck / Motorcycle / Bicycle / Other)} |
| **Vehicle Color** | {The color of the vehicle} |

## Road and Environmental Conditions

| Field | Value |
|-------|-------|
| **Road Condition** | {Describe the condition of the road (e.g., Dry / Wet / Ice / Snow / Sand / Unknown)} |
| **Light Condition** | {Describe the light condition (e.g., Daylight / Dusk / Dawn / Darkness with street lighting / Darkness without lighting / Unknown)} |
| **Weather** | {Describe the weather (e.g., Clear / Rain / Fog / Snow / Storm / Windy / Unknown)} |
| **Traffic Control Devices** | {Indicate any traffic control devices (e.g., Traffic lights / Stop signs / Yield signs / None / Unknown)} |
| **Road Geometry** | {Describe the road geometry (e.g., Straight / Curve / Hillcrest / Intersection / Roundabout / Bridge / Unknown)} |

## Crash Circumstances

| Field | Value |
|-------|-------|
| **Police Attendance** | {Did the police attend the scene? (Yes / No)} |

