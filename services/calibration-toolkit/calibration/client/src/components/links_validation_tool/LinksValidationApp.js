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

import LinksValidationmap from "./LinksValidationMap";
import { withMapHistory } from "../map_drawing/HOC/MapLabelingHistoryHOC";

import "./LinksValStyles.css";

/**
 * App displaying the loaded road network and map information for validating the
 * generated road network.
 */
class LinksValidationApp extends React.Component {
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
   *  Handle response to clicking a category in the drawing manager.
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
      mapZoom,
      reloadPage,
      mapDrawingMode,
      updateMapView
    } = this.props;
    const { selected } = this.state;

    return (
      <div>
        <div className="linksValMap">
          <h2>{`Metropolis Road Network Validation - ${name}`}</h2>
          <LinksValidationmap
            id={id}
            type={type}
            apiKey={API_KEY}
            center={mapCenter}
            shapes={mapShapes}
            editable={false}
            mapZoom={mapZoom}
            drawingMode={mapDrawingMode}
            selected={selected}
            updateMapView={updateMapView}
            reloadPage={reloadPage}
            unselectDrawing={this.handleSelected}
            handleDrawingChange={this.props.handleDrawingChange}
          />
        </div>
        {type === "intersections" && (
          <Button
            onClick={() => {
              this.props.validateLinks();
              this.props.goBack();
            }}
            color="green"
            floated="left"
          >
            Validate Road Network
          </Button>
        )}
        <Button onClick={this.props.goBack} color="purple" floated="right">
          Close
        </Button>
      </div>
    );
  }
}

export default withMapHistory(LinksValidationApp);

LinksValidationApp.propTypes = {
  /** Name of the intersection or city */
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
  mapLabels: PropTypes.array.isRequired,
  /** Label categories available for drawing in the google map */
  labels: PropTypes.arrayOf(PropTypes.object).isRequired,
  /** Handle the action of clicking the validate road network button */
  validateLinks: PropTypes.func.isRequired,
  /** Reload the data from the backend and reload the app */
  reloadPage: PropTypes.func.isRequired,
  /** Function to handle the closing of the app */
  goBack: PropTypes.func.isRequired
};
