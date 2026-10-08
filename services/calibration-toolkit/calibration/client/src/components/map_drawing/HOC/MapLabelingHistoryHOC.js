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
import axios from "axios";
import update from "immutability-helper";

import { API_ENDPOINT } from "../../common/axios_instance";
/**
 * Higher order component for handling the google map history.
 * @param {component} Comp Component being wrapped by the higher order function.
 */
export function withMapHistory(Comp) {
  return class mapHistoryLayer extends Component {
    constructor(props) {
      super(props);

      const { mapLabels, labels } = props;
      let mapShapes = {};

      labels.map(label => (mapShapes[label.id] = []));
      Object.keys(mapLabels).forEach(key => {
        mapShapes[key] = (mapShapes[key] || []).concat(mapLabels[key]);
      });

      this.state = { mapShapes, showMapMarkers: true };
      this.clearMapData = this.clearMapData.bind(this);
      // console.log("maphistory, do i gethere")
    }

    /**
     * Clear all map shapes under a specific category.
     * @param {string} category Category of map shapes to clear
     */
    clearMapData(category) {
      let { mapShapes } = this.state;
      mapShapes = update(mapShapes, { [category]: { $set: [] } });
      this.setState({ mapShapes });
    }

    /**
     * Toggle if the vertex markers are shown on the map shape.
     */
    toggleMapMarkers() {
      const { showMapMarkers } = this.state;
      this.setState({ showMapMarkers: !showMapMarkers });
    }

    /**
     * Save the map zoom and map center to the backend.
     * @param {number} mapZoom Map zoom level
     * @param {object} mapCenter Map center
     */
    async updateMapView(mapZoom, mapCenter) {
      const { type, id } = this.props;
      mapCenter = JSON.stringify(mapCenter);
      let data = {};
      data.mapCenter = mapCenter;
      if (mapZoom) {
        data.mapZoom = mapZoom;
      }
      await axios
        .patch(`${API_ENDPOINT}/${type}/${id}/`, data)
        .then()
        .catch(error => console.error("err", error));
    }

    render() {
      const { props } = this;
      const { mapShapes, showMapMarkers } = this.state;

      return (
        <Comp
          handleDrawingChange={mapShapes => this.setState({ mapShapes })}
          clearMapData={this.clearMapData}
          updateMapView={this.updateMapView.bind(this)}
          toggleMapMarkers={this.toggleMapMarkers.bind(this)}
          {...props}
          mapShapes={mapShapes}
          showMapMarkers={showMapMarkers}
        />
      );
    }
  };
}
