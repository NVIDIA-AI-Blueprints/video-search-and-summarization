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
import update from "immutability-helper";
import { Button } from "semantic-ui-react";

import DrawingManager from "../common/managers/DrawingManager";
import LinksMap from "./LinksMap";
import { withMapHistory } from "../map_drawing/HOC/MapLabelingHistoryHOC";

import "./LinksStyles.css";

/**
 * App displaying the road links drawing tool. Enables the drawing of multiple
 * road links to be used for map matching to generate the road network.
 */
class LinksDrawingApp extends React.Component {
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
    this.handleGenerateRoadNetwork = this.handleGenerateRoadNetwork.bind(this);
  }

  /**
   * Checks if the map shapes have changed, and if so pushes the map shapes
   * data to the backend.
   * @param {object} prevProps Previous props before update
   */
  componentDidUpdate(prevProps) {
    const { onLabelChange, mapShapes } = this.props;

    if (prevProps.mapShapes !== mapShapes) {
      onLabelChange({
        mapLabelData: mapShapes,
        linksAreDrawn: false,
        linksAreValid: false
      });
    }
  }

  /**
   * Handle button click of generate road network. Runs the getRoadNetworkJSON
   * function passed from the loader.
   */
  async handleGenerateRoadNetwork() {
    const { onLabelChange, mapShapes } = this.props;
    onLabelChange({ mapLabelData: mapShapes });
    await this.props.getRoadNetworkJSON();
  }

  /**
   * Handle response to clicking a category in the drawing manager.
   * @param {string} selected ID of selected category
   */
  handleSelected(selected) {
    if (selected === this.state.selected) return;

    if (!selected) {
      this.setState({ selected });
      this.setState({ mapDrawingMode: null });
      return;
    }

    this.setState({ selected, mapDrawingMode: "polyline" });
  }

  render() {
    const {
      name,
      id,
      type,
      API_KEY,
      mapCenter,
      mapShapes,
      labels,
      updateMapView,
      mapZoom,
      reloadPage,
      showMapMarkers
    } = this.props;
    const { selected, toggles, mapDrawingMode } = this.state;

    const managerProps = {
      title: `Metropolis Road Links Drawing Tool - ${name}`,
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
        <div className="linksManager">
          <DrawingManager labels={labels} {...managerProps} />
        </div>
        <div className="linksMap">
          <LinksMap
            id={id}
            type={type}
            toggles={toggles}
            showMapMarkers={showMapMarkers}
            apiKey={API_KEY}
            center={mapCenter}
            mapZoom={mapZoom}
            shapes={mapShapes}
            updateMapView={updateMapView}
            drawingMode={mapDrawingMode}
            selected={selected}
            editable={true}
            reloadPage={reloadPage}
            unselectDrawing={this.handleSelected}
            handleDrawingChange={this.props.handleDrawingChange}
          />
        </div>
        <Button
          onClick={() => {
            this.handleGenerateRoadNetwork();
          }}
          color="green"
        >
          Generate Road Network
        </Button>
        <Button onClick={this.props.goBack} color="purple" floated="right">
          Close
        </Button>
      </div>
    );
  }
}

export default withMapHistory(LinksDrawingApp);

LinksDrawingApp.propTypes = {
  /** name of the intersection */
  name: PropTypes.string,
  /** Intersection ID */
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Intersection string. This is used when saving the moved map location */
  type: PropTypes.string.isRequired,
  /** Google map API key */
  API_KEY: PropTypes.string.isRequired,
  /** Lat/Lng coordinate to load as google map center */
  mapCenter: PropTypes.object.isRequired,
  /** Zoom level to use when loading the google map */
  mapZoom: PropTypes.number.isRequired,
  /** Existing map shapes to load into google map */
  mapLabels: PropTypes.oneOfType([
    PropTypes.arrayOf(PropTypes.object),
    PropTypes.object
  ]).isRequired,
  /** Label categories available for drawing in the google map */
  labels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Function to push drawing updates to the backend */
  onLabelChange: PropTypes.func.isRequired,
  /** Handle the event that the user clicks the generate road network button */
  getRoadNetworkJSON: PropTypes.func.isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired
};
