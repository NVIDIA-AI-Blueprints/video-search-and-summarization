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


import React, { Component } from "react";
import MenuBar from "../common/Menubar";

/**
 * Page outlining the JSON schema for uploading data via JSON input string.
 */
export default class JSONSchema extends Component {
  render() {
    return (
      <MenuBar active="help">
        <h1>JSON SCHEMA (IN PROGRESS)</h1>
        <p>
          The current JSON schema list contains all data that is currently able
          to be upload via JSON. However, this list is not final and more inputs
          may be added in the future
        </p>
        <p>
          The main purpose of this schema is to help testers know what what JSON
          inputs are expected to be working when using the upload via JSON
          functionality.
        </p>
        <hr />
        <h2>CAMERA</h2>
        <h3>sensorId</h3>
        <p>String input representing the sensor sensor ID.</p>
        <p>Example: "HWY20_AND_BRYANT"</p>
        <h3>sensorName</h3>
        <p>String input representing the sensor name.</p>
        <p>Example: "HWY20_AND_BRYANT"</p>
        <h3>majorRoad</h3>
        <p>String input representing major road in the intersection view.</p>
        <p>Example: "HWY20"</p>
        <h3>minorRoad</h3>
        <p>String input representing minor road in the intersection view.</p>
        <p>Example: "BRYANT"</p>
        <h3>originLat</h3>
        <p>
          Float input (between -85.0 and 85.0) representing the latitude
          coordinate of the sensor location.
        </p>
        <p>Example: 42.4897</p>
        <h3>originLng</h3>
        <p>
          Float input (between -180 and 180) representing the longitude
          coordinate of the sensor location.
        </p>
        <p>Example: -90.6770</p>
        <h3>cardinalDirection</h3>
        <p>
          String input consisting of two or three letter code representing the
          cardinal direction that the sensor faces.
        </p>
        <p>Example: "NW"</p>
        <h3>intersection</h3>
        <p>
          Integer input public key of the intersection this sensor is located
          at.
        </p>
        <p>Example: 5</p>
        <h3>corridor</h3>
        <p>Integer input public key of the corridor this sensor belongs to.</p>
        <p>Example: 5</p>
        <hr />
        <h2>INTERSECTION</h2>
        <h3>description</h3>
        <p>String input describing the intersection.</p>
        <p>Example: "Highway 20 and Bryant in Dubuque, Iowa."</p>
        <h3>majorRoad</h3>
        <p>String input representing major road of the intersection.</p>
        <p>Example: "HWY20"</p>
        <h3>minorRoad</h3>
        <p>String input representing minor road of the intersection.</p>
        <p>Example: "BRYANT"</p>
        <h3>name</h3>
        <p>Automatically generated based on majorRoad and minorRoad inputs.</p>
        <p>Example: "HWY20_AND_BRYANT"</p>
        <h3>originLat</h3>
        <p>
          Float input (between -85.0 and 85.0) representing the latitude
          coordinate of the intersection location.
        </p>
        <p>Example: 42.4897</p>
        <h3>originLng</h3>
        <p>
          Float input (between -180 and 180) representing the longitude
          coordinate of the intersection location.
        </p>
        <p>Example: -90.6770</p>
        <hr />
        <h2>CORRIDOR</h2>
        <h3>name</h3>
        <p>String input representing the name of the corridor</p>
        <p>Example: "HWY20"</p>
        <h3>originLat</h3>
        <p>
          Float input (between -85.0 and 85.0) estimating the latitude
          coordinate of the center of the corridor.
        </p>
        <p>Example: 42.4897</p>
        <h3>originLng</h3>
        <p>
          Float input (between -180 and 180) estimating the longitude coordinate
          center of the corridor.
        </p>
        <p>Example: -90.6770</p>
      </MenuBar>
    );
  }
}
