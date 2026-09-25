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


import React, { Component, Fragment } from "react";
import PropTypes from "prop-types";
import { Link } from "react-router-dom";
import { Form, Header, Button, Loader } from "semantic-ui-react";
import update from "immutability-helper";
import axios from "axios";
import { withAlert } from "react-alert";

import {
  setEdited,
  updateDataValue,
  updateDataValidation
} from "../common/utils";
import DeleteButton from "../common/DeleteButton";
import ImportJSONModal from "../modals/ImportJSONModal";

import {API_ENDPOINT} from "../common/axios_instance";

/**
 * Form for inputting and validating sensor data.
 */
class IntersectionDataForm extends Component {
  constructor(props) {
    super(props);

    this.state = {
      intersectionData: {
        description: "",
        name: "",
        originLat: "",
        originLng: ""
      },
      dataValidation: {
        name: false,
        description: false,
        originLat: false,
        originLng: false
      },
      isEdited: {
        name: false,
        description: false,
        originLat: false,
        originLng: false
      },
      jsonModalShow: false,
      linksAreDrawn: false,
      error: null,
      isLoaded: false
    };

    this.handleDataChange = this.handleDataChange.bind(this);
    this.updateName = this.updateName.bind(this);
    this.toggleModalShow = this.toggleModalShow.bind(this);
    this.handleJSONUpload = this.handleJSONUpload.bind(this);
    this.setAllEdited = this.setAllEdited.bind(this);
  }

  /**
   * Load the current intersection data if the intersection exists (i.e. if
   * editing).
   */
  async componentDidMount() {
    const { intersectionId } = this.props;
    if (intersectionId) {
      await axios
        .get(`${API_ENDPOINT}/intersections/${intersectionId}/`)
        .then(res => {
          const intersectionData = res.data;
          const { linksAreDrawn } = intersectionData;
          this.setState({ isLoaded: true, intersectionData, linksAreDrawn });
        })
        .catch();
    } else {
      this.setState({ isLoaded: true });
    }
  }

  /**
   * Handle data changed in any form input field. Sets that field as edited,
   * sets the value in the state based off of what was written in the field,
   * and runs data validation to see if the entered data is accurate.
   * @param {object} e Data changed event, unused
   * @param {object} data Information on the data in the form
   */
  async handleDataChange(e, data) {
    const { content, value } = data;
    const { intersectionData, dataValidation, isEdited } = this.state;
    const newEdited = setEdited(isEdited, content);
    const newData = updateDataValue(intersectionData, content, value);
    const newDataValidation = updateDataValidation(
      dataValidation,
      content,
      value
    );

    await this.setState({
      isEdited: newEdited,
      intersectionData: newData,
      dataValidation: newDataValidation
    });

    // if (content === "majorRoad" || content === "minorRoad") {
    //   this.updateName();
    // }
  }

  /**
   * Update the name of the intersection based on the majorRoad and minorRoad
   * names.
   */
  updateName() {
    const { intersectionData, dataValidation, isEdited } = this.state;

    const { majorRoad, minorRoad } = intersectionData;
    const newData = update(intersectionData, {
      name: { $set: `${majorRoad}_AND_${minorRoad}` }
    });
    this.setState({ intersectionData: newData });

    if (
      (isEdited.majorRoad && !dataValidation.majorRoad) ||
      (isEdited.minorRoad && !dataValidation.minorRoad)
    ) {
      const newValidation = update(dataValidation, {
        name: { $set: false }
      });
      this.setState({ dataValidation: newValidation });
      return;
    }

    if (!this.state.dataValidation.name) {
      const newValidation = update(dataValidation, {
        name: { $set: true }
      });
      this.setState({ dataValidation: newValidation });
    }
  }

  /**
   * Toggle if the upload data from JSON modal is shown or not.
   */
  toggleModalShow() {
    const { jsonModalShow } = this.state;
    this.setState({ jsonModalShow: !jsonModalShow });
  }

  /**
   * Handle uploading data from the JSON input modal. If a key in the JSON
   * object matches an input field in the form, that input field is set as
   * edited, the data is updated with the data from the key, and the data from
   * the key is validated.
   * @param {object} jsonObject JSON input by the user
   */
  async handleJSONUpload(jsonObject) {
    const { intersectionData, dataValidation, isEdited } = this.state;
    let newData = intersectionData;
    let newValidation = dataValidation;
    let newEdited = isEdited;
    Object.keys(jsonObject).forEach(key => {
      if (key in intersectionData) {
        newEdited = setEdited(newEdited, key);
        newData = updateDataValue(newData, key, jsonObject[key]);
        newValidation = updateDataValidation(
          newValidation,
          key,
          jsonObject[key]
        );
      }
    });

    await this.setState({
      intersectionData: newData,
      dataValidation: newValidation,
      isEdited: newEdited
    });

    if ("majorRoad" in jsonObject || "minorRoad" in jsonObject) {
      this.updateName();
    }

    this.toggleModalShow();
  }

  /**
   * Set all fields as edited. Used to show which fields are empty when creating
   * a new intersection.
   */
  setAllEdited() {
    const { isEdited } = this.state;
    const { intersectionId } = this.props;
    if (!intersectionId) {
      let updatedEdited = {};
      Object.keys(isEdited).forEach(key => {
        updatedEdited[key] = true;
      });
      this.setState({ isEdited: updatedEdited });
    }
  }

  render() {
    const { onClose, onSaveData, onDelete, intersectionId, alert } = this.props;
    const {
      intersectionData,
      dataValidation,
      isEdited,
      jsonModalShow,
      linksAreDrawn,
      error,
      isLoaded
    } = this.state;
    const {
      name,
      description,
      // majorRoad,
      // minorRoad,
      originLat,
      originLng
    } = intersectionData;

    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }

    return (
      <Fragment>
        <ImportJSONModal
          modalShow={jsonModalShow}
          onClose={this.toggleModalShow}
          onUpload={this.handleJSONUpload}
        />
        <Form>
          {intersectionId && (
            <div>
              <Form.Group>
                <Link to={`/links/intersection/${intersectionId}`}>
                  <Button
                    color="green"
                    icon="pencil"
                    label="Draw Intersection Road Links"
                  />
                </Link>
                <Link
                  to={
                    linksAreDrawn
                      ? `/links/intersections/validation/${intersectionId}`
                      : "#"
                  }
                >
                  <Button
                    color="green"
                    icon="pencil"
                    label="Validate Intersection Road Network"
                    secondary={!linksAreDrawn}
                    onClick={() =>
                      !linksAreDrawn
                        ? alert.show("Please draw road links first.")
                        : null
                    }
                  />
                </Link>
              </Form.Group>
              <hr />
              <h3>Edit Data:</h3>
            </div>
          )}
          <Form.Input
            label="Description"
            content="description"
            value={description}
            error={
              isEdited.description && !dataValidation.description
                ? "Invalid Description. Maximum Length: 400 Characters."
                : false
            }
            onChange={(e, data) => this.handleDataChange(e, data)}
            data-testid="IntersecDescInput"
          />
          <Form.Group widths="equal">
            {/* <Form.Input
              label="Major Road"
              content="majorRoad"
              value={majorRoad}
              error={
                isEdited.majorRoad && !dataValidation.majorRoad
                  ? "Invalid Road Name. Maximum Length: 200 Characters."
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="IntersecMajRdInput"
            />
            <Form.Input
              label="Minor Road"
              content="minorRoad"
              value={minorRoad}
              error={
                isEdited.minorRoad && !dataValidation.minorRoad
                  ? "Invalid Road Name. Maximum Length: 200 Characters."
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="IntersecMinRdInput"
            /> */}
            <Form.Input
              label="Name"
              content="name"
              value={name}
              error={
                isEdited.name  && !dataValidation.name
                  ? "Invalid Intersection Name. Maximum Length: 200 Characters."
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="IntersecNameInput"
            />
          </Form.Group>
          <Form.Group>
            <Form.Input
              label="Intersection Latitude"
              content="originLat"
              value={originLat}
              error={
                isEdited.originLat && !dataValidation.originLat
                  ? "Invalid Latitude"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="IntersecLatInput"
            />
            <Form.Input
              label="Intersection Longitude"
              content="originLng"
              value={originLng}
              error={
                isEdited.originLng && !dataValidation.originLng
                  ? "Invalid Longitude"
                  : false
              }
              onChange={(e, data) => this.handleDataChange(e, data)}
              data-testid="IntersecLngInput"
            />
          </Form.Group>
          <Form.Group>
            <Form.Button
              color="green"
              onClick={() => {
                this.setAllEdited();
                onSaveData(intersectionData, dataValidation);
              }}
              data-testid="IntersecFormApply"
            >
              Apply
            </Form.Button>
            <Form.Button
              color="purple"
              onClick={() => onClose()}
              data-testid="IntersecFormClose"
            >
              Close
            </Form.Button>
            <Form.Button
              floated="right"
              color="green"
              onClick={this.toggleModalShow}
            >
              Upload from JSON
            </Form.Button>
          </Form.Group>
          {onDelete && (
            <div>
              <hr />
              <Header>DELETE INTERSECTION</Header>
              <p>
                The button bellow will delete the intersection and all data
                within the intersection (sensors).
              </p>
              <DeleteButton
                onConfirmDelete={() => onDelete()}
                label={"DELETE INTERSECTION"}
              />
            </div>
          )}
        </Form>
      </Fragment>
    );
  }
}

export default withAlert()(IntersectionDataForm);

IntersectionDataForm.propTypes = {
  /** ID of intersection if form being used to edit existing intersection */
  intersectionId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle the action of deleting if editing an existing intersection */
  onDelete: PropTypes.func,
  /** Handle action of saving the data in the form */
  onSaveData: PropTypes.func.isRequired,
  /** Handle the action of closing the form */
  onClose: PropTypes.func.isRequired
};
