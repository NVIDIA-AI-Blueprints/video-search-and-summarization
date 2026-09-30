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
 * Page explaining the tools that are available within the app.
 */
export default class UsageOverview extends Component {
  render() {
    return (
      <MenuBar active="help">
        <h1>USAGE OVERVIEW</h1>
        <h2>Setup</h2>
        <h3>Projects</h3>
        <p>
          A project defines the workspace we are working in. It includes Places,
          Sensors, and/or Groups. A Project will be associated with one Place.
        </p>
        <h3>Cities (future to be change to more generically named Places)</h3>
        <p>
          A place consists of a name, a location, and a OpenStreetMap (.osm)
          link.
        </p>
        <h3>Sensors</h3>
        <p>A sensor consists of the metadata that we will be calibrating.</p>
        <h3>Groups</h3>
        <p>Allow sensors to be ground into one or more categories.</p>
        <h4>Intersections</h4>
        <p>
          An intersection of roads. Sensors can be assigned to the intersection
          they are a part of.
        </p>
        <h4>Corridors</h4>
        <p>
          A length of road with numerous sensors. Sensors can be assigned to the
          corridor they are a part of
        </p>
        <h2>Camera Calibration Tool</h2>
        <h4>Overview</h4>
        <p>
          The sensor calibration tool is used to assist the user in generating a
          homography matrix, which is used to transform points from the image
          coordinate frame to the satellite map coordinate frame.
        </p>
        <h4>Calibration</h4>
        <p>
          To use the tool, the user must draw a calibration polygon in both the
          image and the satellite map. The vertices of the two polygons must be
          corresponding features. For example, if vertex 4 on the image is the
          base of a stop sign, then vertex 4 on the satellite image should be at
          the base of the same stop sign.
        </p>
        <h4>Region of Interest (ROI)</h4>
        <p>
          The ROI is used to represent where the calibration (homography matrix)
          is expected to be valid. For example, the calibration is only expected
          to be valid on the road, so the ROI should not include buildings or
          trees next to roads. In addition, the ROI should not include areas
          that are not in the image. The ROI can also be adjusted at the
          validation step of sensor calibration.
        </p>
        <h2>Calibration Validation Tool</h2>
        <h4>Overview</h4>
        <p>
          The calibration validation tool is used to assist the user in
          validating the homography matrix calculated from the sensor
          calibration tool. This tool can only be accessed if a homogrophy
          matrix exists for the sensor of interest.
        </p>
        <h4>Validation</h4>
        <p>
          To use the tool, the user must draw a polylines on the image that
          mimic a plausible trajectory of a vehicle. The trajectory drawn in the
          image is projected to the satellite map in real time using the
          homography matrix calculated by the calibration tool. When the user is
          satisfied with how the trajectory is projected, they can click
          validate to complete the sensor calibration process.
        </p>
        <h4>Region of Interest (ROI)</h4>
        <p>
          This is the same ROI as drawn in the sensor calibration tool. The user
          is given the ability to edit the ROI here as the drawn trajectories
          reveal how well the calibration works in certain areas. The ROI must
          be drawn to validate the calibration.
        </p>
        <h2>Road Links Drawing Tool</h2>
        <h4>Overview</h4>
        <p>
          The road links drawing tool is used to assist the user in drawing the
          road links at an interesection that are to be used to generate the
          road network by map matching to the OpenStreetMap file.
        </p>
        <h4>Road Links</h4>
        <p>
          Road links are drawn at an intersection to show which direction the
          traffic can go. For example, a one way road will only have one road
          link, but a two way road will have a link for each direction.
        </p>
        <h2>Road Network Validation Tool</h2>
        <h4>Overview</h4>
        <p>
          This road network validation tool is used to assist the user in
          validation the road network generated via the road links drawing tool.
        </p>
        <p>
          The road network is what is used to aggregate data from the sensor
          system. For example, to calculate traffic one can see how many cars
          are currently on a given segment of the road network, and check how
          fast the cars in that road network are going.
        </p>
        <h2>Corridor Tool</h2>
        <h4>Overview</h4>
        <p>
          The corridor tool is used to view sensors that belong to a corridor
          and to draw the geomtry of the cooridor for automated length and
          direction calculation.
        </p>
        <h4>Sensors</h4>
        <p>
          All sensors that belong to a given corridor are shown in that corridor
          view. The sensors can be clicked for more information.
        </p>
        <h4>Corridor Length</h4>
        <p>
          The length of the corridor can be determined by drawing the corridor
          geometry via a polyline. The length will be automatically calculated
          and displayed on drawing completion or edit.
        </p>
      </MenuBar>
    );
  }
}
