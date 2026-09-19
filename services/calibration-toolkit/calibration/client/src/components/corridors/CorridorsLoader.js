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
import { Loader } from "semantic-ui-react";
import DOMPurify from "dompurify";
import CorridorsApp from "./CorridorsApp";
import { corId } from "../common/utils";
import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Loader to load the data of a given corridor to be displayed on the page. A
 * loader takes no props, except the corridorId passed through the URL.
 */
export default class CorridorsLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      name: null,
      mapCenter: null,
      mapZoom: null,
      corridorShape: [],
      sensors: [],
      projectId: null,
      length: 0
    };
    this.goBack = this.goBack.bind(this);
    this.loadPage = this.loadPage.bind(this);
    this.handleUpdateLength = this.handleUpdateLength.bind(this);
  }

  /**
   * Run loadPage function when the component mounts.
   */
  componentDidMount() {
    this.loadPage();
  }

  /**
   * Load the data of a specific corridor from the backend.
   */
  async loadPage() {
    const { corridorId } = this.props.match.params;
    await axios
      .get(`${API_ENDPOINT}/corridors/${corridorId}/`)
      .then(res => {
        const corridor = res.data;
        const {  project, name, mapAPIKey } = corridor;
        const corridorShape = JSON.parse(corridor.corridorShape);
        const sensors = corridor.sensor_set;
        const { mapZoom } = corridor;
        let mapCenter;
        if (corridor.mapCenter) {
          mapCenter = JSON.parse(corridor.mapCenter);
        } else {
          mapCenter = { lat: corridor.originLat, lng: corridor.originLng };
        }
        this.setState({
          mapAPIKey,
          name,
          isLoaded: true,
          mapCenter,
          sensors,
          mapZoom,
          corridorShape,
          projectId: project,
          length: corridor.length
        });
      })
      .catch(error => {
        this.setState({
          isLoaded: true,
          error
        });
      });
  }

  /**
   * Push updates to the backend.
   * @param {object} labelData Contains information to be sent to the backend
   */
  async pushUpdate(labelData) {
    const { corridorId } = this.props.match.params;
    const corridorShape = JSON.stringify(labelData.corridorShape);
    const { length, directions } = labelData;

    await axios
      .patch(`${API_ENDPOINT}/corridors/${corridorId}/`, {
        length,
        directions,
        corridorShape
      })
      .then()
      .catch(error => console.error("err", error));
  }

  /**
   * Handle update when length is changed.
   * @param {number} length Length of corridor
   */
  handleUpdateLength(length) {
    this.setState({ length });
  }

  /**
   * Go back to the Corridors tab of the project page.
   */
  goBack = () => {
    const {  projectId } = this.state;
    let path = `/projects/geo/generateRoadLinks/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      state: { prevPath: "Corridors" }
    });
  };

  render() {
    const  corridorId  = DOMPurify.sanitize(this.props.match.params.corridorId);
    const {
      mapAPIKey,
      name,
      mapCenter,
      mapZoom,
      corridorShape,
      sensors,
      error,
      isLoaded,
      length
    } = this.state;
    const labels = [{ type: "polyline", name: "Corridor", id: corridorId, draw:true }];

    if (error) {
      return <div data-testid="CalibLoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return (
        <Loader data-testid="CalibLoaderLoading" active inline="centered" />
      );
    }
    return (
      <CorridorsApp
        name={name}
        id={corridorId}
        type="corridors"
        API_KEY={mapAPIKey}
        mapCenter={mapCenter}
        mapZoom={mapZoom}
        mapLabels={corridorShape}
        length={length}
        onUpdateLength={this.handleUpdateLength}
        sensors={sensors}
        onLabelChange={this.pushUpdate.bind(this)}
        labels={labels}
        reloadPage={this.loadPage}
        goBack={this.goBack}
      />
    );
  }
}
