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
import { Button } from "semantic-ui-react";
import Modal from "react-modal";
import { modalLayer2 } from "./utils";

const customStyles = {
  content: {
    top: "50%",
    left: "50%",
    right: "auto",
    bottom: "auto",
    marginRight: "-50%",
    transform: "translate(-50%, -50%)"
  },
  overlay: { zIndex: modalLayer2 }
};

/**
 * Button that brings up modal to confirm delete intention before deleting. This
 * button should be used in all cases of deleting information.
 */
class DeleteButton extends Component {
  constructor(props) {
    super(props);

    this.state = {
      warningModal: false
    };
  }

  componentDidMount() {
    Modal.setAppElement("body");
  }

  render() {
    const { warningModal } = this.state;
    const { onConfirmDelete, label } = this.props;
    return (
      <Fragment>
        <Button
          negative
          onClick={() => this.setState({ warningModal: !warningModal })}
          data-testid="DeleteButton"
        >
          {label}
        </Button>
        <Modal isOpen={warningModal} style={customStyles}>
          <h1>Are you sure you want to delete?</h1>
          <Button
            size="large"
            positive
            floated="left"
            onClick={() => this.setState({ warningModal: !warningModal })}
            data-testid="CancelDelete"
          >
            CANCEL
          </Button>
          <Button
            size="large"
            negative
            floated="right"
            onClick={() => onConfirmDelete()}
            data-testid="ConfirmDeleteButton"
          >
            DELETE
          </Button>
        </Modal>
      </Fragment>
    );
  }
}

export default DeleteButton;

DeleteButton.propTypes = {
  /** Label to use for the button to activate the confirm delete modal */
  label: PropTypes.string.isRequired,
  /** Handle the action of deleting when the confirm delete button is clicked */
  onConfirmDelete: PropTypes.func.isRequired
};
