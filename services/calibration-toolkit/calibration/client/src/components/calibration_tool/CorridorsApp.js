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


import React from "react";
import PropTypes from "prop-types";
import { Button } from "semantic-ui-react";
import update from "immutability-helper";

import CorridorsMap from "./CorridorsMap";
import DrawingManager from "../common/managers/DrawingManager";
import {
  haversineDistance,
  calculateHeading,
  bearingToDirections
} from "../common/mathUtils";
import { corId } from "../common/utils";
import { withMapHistory } from "../map_drawing/HOC/MapLabelingHistoryHOC";

import "./CorridorsStyles.css";

/**
 * Corridor page for drawing cooridor geometry and viewing sensors in the
 * corridor.
 */
class CorridorsApp extends React.Component {
  constructor(props) {
    super(props);

    const { labels } = this.props;

    const toggles = {};
    labels.map(label => (toggles[label.id] = true));

    this.state = {
      selected: null,
      mapDrawingMode: null,
      toggles
    };
    this.handleSelected = this.handleSelected.bind(this);
  }

  /**
   * Calculate the length and direction of the corridor when the component
   * updates
   * @param {object} prevProps Previous props before update
   */
  componentDidUpdate(prevProps) {
    const { onLabelChange, mapShapes } = this.props;

    if (prevProps.mapShapes !== mapShapes) {
      let length = 0;
      let directions = "[]";
      if (mapShapes[corId].length) {
        const { points } = mapShapes[corId][0];
        length = getCorridorLength(points);
        const bearing = calculateHeading(points[0], points[points.length - 1]);
        directions = JSON.stringify(bearingToDirections(bearing));
      }
      this.props.onUpdateLength(length);
      onLabelChange({
        length,
        directions,
        corridorShape: mapShapes
      });
    }
  }

  /**
   * Handle response to clicking a category in the drawing manager.
   * @param {string} selected ID of selected category
   */
  handleSelected(selected) {
    const { mapShapes } = this.props;
    if (selected === this.state.selected) return;

    if (!selected) {
      this.setState({ selected });
      this.setState({ mapDrawingMode: null });
      return;
    }

    if (selected === corId && !mapShapes[corId].length) {
      this.setState({ mapDrawingMode: "polyline" });
    }

    this.setState({ selected });
  }

  render() {
    const {
      name,
      id,
      type,
      API_KEY,
      showMapMarkers,
      mapCenter,
      mapShapes,
      updateMapView,
      mapZoom,
      labels,
      reloadPage
    } = this.props;
    const { selected, mapDrawingMode, toggles } = this.state;

    const managerProps = {
      title: `Metropolis Corridor View - ${name}`,
      selected,
      onSelect: this.handleSelected,
      toggles,
      onToggle: label => {
        this.setState({
          toggles: update(toggles, {
            [label.id]: { $set: !toggles[label.id] }
          })
        });
      },
      labelData: mapShapes,
      clearMapData: this.props.clearMapData,
      toggleMapMarkers: this.props.toggleMapMarkers,
      showMapMarkers: this.props.showMapMarkers
    };

    return (
      <div>
        <div className="corridorManager">
          <DrawingManager labels={labels} {...managerProps} />
        </div>
        <div className="corridorsMap">
          <CorridorsMap
            id={id}
            type={type}
            toggles={toggles}
            showMapMarkers={showMapMarkers}
            apiKey={API_KEY}
            center={mapCenter}
            mapZoom={mapZoom}
            shapes={mapShapes}
            sensors={this.props.sensors}
            drawingMode={mapDrawingMode}
            selected={selected}
            editable={true}
            reloadPage={reloadPage}
            unselectDrawing={this.handleSelected}
            handleDrawingChange={this.props.handleDrawingChange}
            updateMapView={updateMapView}
          />
        </div>
        <Button onClick={this.props.goBack} color="green" floated="left">
          Validate Length
        </Button>
        <h2
          style={{ display: "inline" }}
        >{`Current Length: ${this.props.length.toFixed(2)}m`}</h2>
        <Button onClick={this.props.goBack} color="purple" floated="right">
          Close
        </Button>
      </div>
    );
  }
}

export default withMapHistory(CorridorsApp);

CorridorsApp.propTypes = {
  /** Name of the corridor */
  name: PropTypes.string,
  /** Corridor ID */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Type corridor. This is used when saving moved map location */
  type: PropTypes.string.isRequired,
  /** Google map API key */
  API_KEY: PropTypes.string.isRequired,
  /** Lat/Lng coordinate to load as google map center */
  mapCenter: PropTypes.object.isRequired,
  /** Zoom level to use when loading the google map */
  mapZoom: PropTypes.number.isRequired,
  /** Existing map shapes to load into google map */
  mapLabels: PropTypes.object.isRequired,
  /** Length of the corridor */
  length: PropTypes.number.isRequired,
  /** Handles the change when the length is updated */
  onUpdateLength: PropTypes.func.isRequired,
  /** Sensors that are to be displayed as part of the cooridor */
  sensors: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Function to push drawing updates to the backend */
  onLabelChange: PropTypes.func.isRequired,
  /** Label categories available for drawing in the google map */
  labels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired
};

function getCorridorLength(points) {
  let prevPoint = null;
  let length = 0;
  points.forEach(point => {
    if (prevPoint) {
      const segmentLength = haversineDistance(point, prevPoint);
      length = length + segmentLength;
    }
    prevPoint = point;
  });
  return length;
}
