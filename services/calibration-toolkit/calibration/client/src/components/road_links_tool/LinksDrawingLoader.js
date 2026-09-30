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
import { withAlert } from "react-alert";

import LinksDrawingApp from "./LinksDrawingApp";
import { linksId } from "../common/utils";
import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Loader to load all map data required for drawing road links. A loader takes
 * no props, except the intersectionId passed through the URL.
 */
class LinksDrawingLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      name: null,
      mapCenter: {},
      mapZoom: null,
      lineSegments: [],
      projectId: null,
      isLoaded: false,
      loadingMapMatching: false
    };
    this.goBack = this.goBack.bind(this);
    this.loadPage = this.loadPage.bind(this);
    this.toggleMatchingLoader = this.toggleMatchingLoader.bind(this);
  }

  /**
   * Run the loadPage function on component mount.
   */
  componentDidMount() {
    this.loadPage();
  }

  /**
   * Load the intersection data from the backend.
   */
  async loadPage() {
    const { intersectionId } = this.props.match.params;
    await axios
      .get(`${API_ENDPOINT}/intersections/${intersectionId}/`)
      .then(res => {
        const intersection = res.data;
        const { project, name, mapAPIKey } = intersection;
        const { mapZoom } = intersection;
        const newLinkData = JSON.parse(intersection.lineSegments);
        let lineSegments = {};
        lineSegments[linksId] = newLinkData;
        let mapCenter;
        if (intersection.mapCenter) {
          mapCenter = JSON.parse(intersection.mapCenter);
        } else {
          mapCenter = {
            lat: intersection.originLat,
            lng: intersection.originLng
          };
        }

        this.setState({
          mapAPIKey,
          name,
          isLoaded: true,
          mapCenter,
          mapZoom,
          lineSegments,
          projectId: project
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
   * @param {object} labelData Contains information to be sent to the backend:
   */
  async pushUpdate(labelData) {
    const { intersectionId } = this.props.match.params;
    const lineSegments = JSON.stringify(labelData.mapLabelData[linksId]);
    const { linksAreDrawn, linksAreValid } = labelData;

    await axios
      .patch(`${API_ENDPOINT}/intersections/${intersectionId}/`, {
        lineSegments,
        linksAreDrawn,
        linksAreValid
      })
      .then()
      .catch(error => console.error("err", error));
  }

  /**
   * Requests that the backend generates the roadNetwork JSON and saves the road
   * network shapes into the backend of the intersection. Sets a loader to
   * prevent user from making changes which the map matching is being performed.
   */
  async getRoadNetworkJSON() {
    const { intersectionId } = this.props.match.params;
    const { alert } = this.props;
    this.toggleMatchingLoader();
    await axios
      .get(`${API_ENDPOINT}/roadSegment/${intersectionId}/`)
      .then(() => {
        alert.success("Road Links Generated");
        axios.patch(`${API_ENDPOINT}/intersections/${intersectionId}/`, {
          linksAreDrawn: true
        });
        this.goBack();
      })
      .catch(error => {
        axios
          .patch(`${API_ENDPOINT}/intersections/${intersectionId}/`, {
            linksAreDrawn: false
          })
          .then(() => {
            alert.error("Error Generating Road Links");
            this.setState({ loadingMapMatching: false });
          })
          .catch(() => {
            alert.error("Error Generating Road Links");
            this.setState({ loadingMapMatching: false });
          });
      });
  }

  /**
   * Toggle wether or not the loader is being displayed.
   */
  toggleMatchingLoader() {
    const { loadingMapMatching } = this.state;
    this.setState({ loadingMapMatching: !loadingMapMatching });
  }

  /**
   * Go back to the project page under the Intersections tab.
   */
  goBack = () => {
    const { projectId } = this.state;
    let path = `/projects/geo/generateRoadLinks/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      state: { prevPath: "Intersections" }
    });
  };

  render() {
    const { intersectionId } = DOMPurify.sanitize(this.props.match.params);
    const {
      mapAPIKey,
      name,
      lineSegments,
      mapCenter,
      error,
      isLoaded,
      loadingMapMatching,
      mapZoom
    } = this.state;
    const labels = [
      {
        type: "polyline",
        name: "Road Links",
        id: linksId,
        draw: true,
      }];

    if (error) {
      return <div data-testid="CalibLoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return (
        <Loader data-testid="CalibLoaderLoading" active inline="centered" />
      );
    } else if (loadingMapMatching) {
      return (
        <div style={{ textAlign: "center" }}>
          <h1>Matching Road Links</h1>
          <p>
            Please wait. If running for the first time in this city, this can
            take a few minutes.
          </p>
          <p>You will be automatically redirected on completion.</p>
          <Loader data-testid="CalibLoaderLoading" active inline="centered" />
        </div>
      );
    }

    return (
      <LinksDrawingApp
        name={name}
        id={intersectionId}
        type="intersections"
        API_KEY={mapAPIKey}
        mapCenter={mapCenter}
        mapZoom={mapZoom}
        mapLabels={lineSegments}
        labels={labels}
        onLabelChange={this.pushUpdate.bind(this)}
        getRoadNetworkJSON={this.getRoadNetworkJSON.bind(this)}
        reloadPage={this.loadPage}
        goBack={this.goBack}
      />
    );
  }
}

export default withAlert()(LinksDrawingLoader);
