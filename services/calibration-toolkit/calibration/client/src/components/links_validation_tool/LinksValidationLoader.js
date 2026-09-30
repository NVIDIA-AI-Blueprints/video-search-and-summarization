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

import LinksValidationApp from "./LinksValidationApp";
import { linksId } from "../common/utils";
import DOMPurify from "dompurify";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Loader to load all map and image data required for validating a road network.
 * A loader takes no props, except the type and id passed through the URL.
 */
class LinksValidationLoader extends Component {
  constructor(props) {
    super(props);

    this.state = {
      mapAPIKey: "",
      name: null,
      mapCenter: {},
      mapZoom: null,
      mapShapes: [],
      projectId: null
    };
    this.goBack = this.goBack.bind(this);
    this.loadPage = this.loadPage.bind(this);
    this.loadIntersection = this.loadIntersection.bind(this);
  }

  /**
   * Run loadPage function when the component mounts.
   */
  componentDidMount() {
    this.loadPage();
  }

  /**
   * Determine whether to load single intersection network or all networks in
   * the project.
   */
  loadPage() {
    const { type, id } = this.props.match.params;
    if (type === "intersections") {
      this.loadIntersection(type);
    } else if (type === "projects") {
      this.loadProject();
    }
  }

  /**
   * Load intersection data from the backend.
   */
  async loadIntersection() {
    const { type, id } = this.props.match.params;
    await axios
      .get(`${API_ENDPOINT}/${type}/${id}/`)
      .then(res => {
        const intersection = res.data;
        const {  project, name, mapAPIKey } = intersection;
        const roadLinks = JSON.parse(intersection.roadLinks);
        const { mapZoom } = intersection;
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
          mapShapes: roadLinks,
          mapCenter,
          mapZoom,
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
   * Load project data from the backend.
   */
  async loadProject() {
    const { type, id } = this.props.match.params;
    //check this
    await axios
      .get(`${API_ENDPOINT}/${type}/${id}/`)
      .then(res => {
        const project = res.data;
        const { id, name, mapAPIKey } = project;
        const { intersection_set } = project;
        const roadLinks = intersection_set.map(intersection => {
          if (intersection.linksAreDrawn) {
            return JSON.parse(intersection.roadLinks);
          } else {
            return [];
          }
        });
        const { mapZoom } = project;
        let mapCenter;
        if (project.mapCenter) {
          mapCenter = JSON.parse(project.mapCenter);
        } else {
          mapCenter = {
            lat: project.originLat,
            lng: project.originLng
          };
        }

        this.setState({
          mapAPIKey,
          name,
          isLoaded: true,
          mapShapes: roadLinks,
          mapCenter,
          mapZoom,
          projectId: project.id
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
   * Set the the road network as valid at the intersection level.
   */
  async validateLinks() {
    const { type, id } = this.props.match.params;
    if (type === "intersections") {
      await axios
        .patch(`${API_ENDPOINT}/${type}/${id}/`, { linksAreValid: true })
        .then(() => this.props.alert.success("Road Links Validated"))
        .catch(error => {
          console.error(error);
        });
    }
  }

  /**
   * Go back to the Intersections tab in the project page.
   */
  goBack = () => {
    const { projectId } = this.state;
    console.log("goback", projectId)
    let path = `/projects/geo/generateRoadLinks/${projectId}`;
    let { history } = this.props;
    history.push({
      pathname: path,
      state: { prevPath: "Intersections" }
    });
  };


  render() {
    const { id, type } = this.props.match.params;
    const sanitizeType = DOMPurify.sanitize(type)
    const sanitizeId = DOMPurify.sanitize(id)
    const {
      mapAPIKey,
      name,
      mapShapes,
      mapCenter,
      mapZoom,
      error,
      isLoaded
    } = this.state;
    const labels = [{ type: "polyline", name: "Road Links", id: linksId }];

    if (error) {
      return <div data-testid="CalibLoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return (
        <Loader data-testid="CalibLoaderLoading" active inline="centered" />
      );
    }

    return (
      <LinksValidationApp
        name={name}
        id={sanitizeId}
        type={sanitizeType}
        API_KEY={mapAPIKey}
        mapCenter={mapCenter}
        mapZoom={mapZoom}
        mapLabels={mapShapes}
        labels={labels}
        validateLinks={this.validateLinks.bind(this)}
        reloadPage={this.loadPage}
        goBack={this.goBack}
      />
    );
  }
}

export default withAlert()(LinksValidationLoader);
