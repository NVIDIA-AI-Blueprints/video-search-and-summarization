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
import PropTypes from "prop-types";
import { withAlert } from "react-alert";
import { Form, Button } from "semantic-ui-react";
import Modal from "react-modal";
import { modalLayer2 } from "../common/utils";

const customStyles = {
  content: {
    top: "50%",
    left: "50%",
    right: "auto",
    bottom: "auto",
    marginRight: "-50%",
    transform: "translate(-50%, -50%)"
  },
  overlay: {
    zIndex: modalLayer2
  }
};

/**
 * Modal for displaying the window to import copy+paste in data from a JSON
 * string.
 */
class ImportJSONModal extends Component {
  constructor(props) {
    super(props);

    this.state = {
      jsonString: ""
    };

    this.handleJSONInput = this.handleJSONInput.bind(this);
    this.uploadJSONObject = this.uploadJSONObject.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  handleJSONInput(e) {
    this.setState({ jsonString: e.target.value });
  }

  /**
   * Handle clicking upload json. Alerts error if no JSON is provided, if JSON
   * object is invalid, or if the JSON.parse returns an error.
   */
  uploadJSONObject() {
    const { jsonString } = this.state;
    if (jsonString === "") {
      this.props.alert.error("No JSON data provided");
      return;
    }
    let jsonObject;
    try {
      jsonObject = JSON.parse(jsonString);
      if (!jsonObject) {
        this.props.alert.error("Invalid JSON Object");
        return;
      }
    } catch (error) {
      this.props.alert.error(error.message);
      return;
    }
    this.props.onUpload(jsonObject);
  }

  render() {
    const { onClose, modalShow } = this.props;
    return (
      <Modal isOpen={modalShow} style={customStyles}>
        <h2>Copy+Paste JSON Metadata:</h2>
        <Form>
          <Form.TextArea
            label="JSON Metadata"
            placeholder="Copy and past a JSON object here"
            onChange={e => this.handleJSONInput(e)}
          />
          <Button
            floated="left"
            color="green"
            onClick={() => this.uploadJSONObject()}
            data-testid="JSONImportUpload"
          >
            Upload
          </Button>
          <Button
            floated="right"
            color="red"
            onClick={() => onClose()}
            data-testid="JSONImportClose"
          >
            Cancel
          </Button>
        </Form>
      </Modal>
    );
  }
}

export default withAlert()(ImportJSONModal);

ImportJSONModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** Handle the action of uploading the inputted JSON data */
  onUpload: PropTypes.func.isRequired,
  /** Handle the action of closing the JSON upload modal */
  onClose: PropTypes.func.isRequired
};
