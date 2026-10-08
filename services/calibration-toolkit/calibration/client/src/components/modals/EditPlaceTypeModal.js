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
import Modal from "react-modal";
import update from "immutability-helper";
import axios from "axios";

import PlaceTypeForm from "../forms/PlaceTypeForm";
import { modalLayer1 } from "../common/utils";
import DOMPurify from "dompurify";
import Joi from 'joi';
import {API_ENDPOINT} from "../common/axios_instance";
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
    zIndex: modalLayer1
  }
};

/**
 * Modal to use as popup when editing the data of an existing placeType.
 */
class EditPlaceTypeModal extends Component {
  constructor(props) {
    super(props);
    this.state = {
      sortedPlaceTypeHierarchy : [],
      validatedPlaceTypes :[],
      error:false
    }


    this.handleSaveData = this.handleSaveData.bind(this);
    this.handleDelete = this.handleDelete.bind(this);
    this.getPlaceData = this.getPlaceData.bind(this);
    this.rerankPlaceTypes = this.rerankPlaceTypes.bind(this);
  }

  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    this._isMounted = true;
    Modal.setAppElement("body");
    this.getPlaceData()
    this.forceUpdate()
    console.log("this.state,", this.state)

  }

  /**
   * Handle event that user saves the data input into the form. The place ID
   * is used to patch the place in the backend. Only data that is changed and
   * is valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleSaveData(changedData, dataValidation) {
    const { placeTypeId } = this.props;
    let updatedData = {};
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        updatedData = update(updatedData, {
          [key]: { $set: changedData[key] }
        });
      }
    });

    if (Object.entries(updatedData).length === 0) {
      this.props.alert.show("No valid changes made");
      return;
    }

    await axios
      .patch(`${API_ENDPOINT}/placeTypes/${placeTypeId}/`, updatedData)
      .then(() => {
        this.props.reloadPlaceTypes();
        this.props.alert.success("Updated Data");
      })
      .catch(error => {
        const { alert } = this.props;
        if (error.response.data) {
          const { data } = error.response;
          Object.keys(data).forEach(key => {
            alert.error(`Error in ${key}. ${data[key]}`);
          });
        } else if (error.request) {
          console.error("No response from server.");
        }
      });
  }



  async getPlaceData(){
    const { projectId } = this.props;

    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {

        if (!res ||!res.data ||typeof res.data!=="object"){
          throw new Error("Invalid data recieved from the server.")
        }
        const responseData = {}
        responseData = JSON.parse(DOMPurify.sanitize(JSON.stringify(res.data)));

        const placeTypeSchema = Joi.object({
          placeType: Joi.string().required(),
          rank: Joi.number().integer().required(),
        });

        const validatedPlaceTypes = responseData.map(placeType => {
          const { error, value } = placeTypeSchema.validate(placeType);
          if (error) {
            console.error('Invalid placeType:', error);
            return null;
          }
          return value;
        }).filter(Boolean);

        let placeTypeHierarchy = new Map();

        //const placeTypeHierarchy = Object.create(null);

        validatedPlaceTypes.forEach(placeType => {
          const { placeType: name, rank: num } = placeType;
          placeTypeHierarchy.set(name, num);
        });
        var sortedPlaceTypeHierarchy = []

        for (var key in placeTypeHierarchy) {
          sortedPlaceTypeHierarchy.push([ key, placeTypeHierarchy[key] ])
        }
        sortedPlaceTypeHierarchy.sort(function compare(kv1, kv2) {
          return kv1[1] - kv2[1]
        })

        sortedPlaceTypeHierarchy.map((obj,i) =>{
        })
        this._isMounted &&
        this.setState({
          sortedPlaceTypeHierarchy,
          validatedPlaceTypes
        } );
      })
      .catch(error => {
        this._isMounted &&
          this.setState({
            error
          });
      });

    }

  rerankPlaceTypes(placeTypeId){
      const {cityId} = this.props
      const {sortedPlaceTypeHierarchy, placeTypes_set} = this.state
      // console.log ("asfa", sortedPlaceTypeHierarchy)
      // console.log("placetp", placeTypes_set)

      //get placetypid rank
      const placeType = placeTypes_set.find(({id}) => id == placeTypeId );
      // console.log("placetype", placeType)
      const name = placeType.placeType
      // console.log("n", name)
      const specified = sortedPlaceTypeHierarchy.find( sorted => sorted[0] === name);

      const rank = specified[1]
      // console.log(rank)
      for (var index = rank ; index <= sortedPlaceTypeHierarchy.length; index++){
        // console.log("here", index)
        if (index === rank){
          //delete
          sortedPlaceTypeHierarchy.splice(index,1)
          placeTypes_set.splice(index,1)
          // console.log("delete", sortedPlaceTypeHierarchy)
          // console.log("delete", placeTypes_set)

        } else{
          let replaced = index-1
          // sortedPlaceTypeHierarchy[replaced] = sortedPlaceTypeHierarchy[index]
          sortedPlaceTypeHierarchy[replaced][1] = replaced
          // placeTypes_set[replaced] = placeTypes_set[index]
          placeTypes_set[replaced]['rank'] = replaced
          // console.log("changed", sortedPlaceTypeHierarchy)
          // console.log("chagnep", placeTypes_set)
        }


      }

      this._isMounted&&
      this.setState({
        sortedPlaceTypeHierarchy
      })



  }

  /**
   * Handle the action of the user choosing to delete the place. Deletes the
   * place from the backend based on its ID and reloads the page to show the
   * place removal.
   */
  async handleDelete() {
    const { placeTypeId } = this.props;
    this.rerankPlaceTypes(placeTypeId)
    await axios
      .delete(`${API_ENDPOINT}/placeTypes/${placeTypeId}/`)
      .then(() => {

        this.props.reloadPlaceTypes();
        document.getElementById("Tab_Sensors").click();
      })
      .catch(error => console.error(error));
    this.forceUpdate()
  }

  render() {
    const { modalShow, onClose, placeTypeId } = this.props;
    return (
      <Modal
        style={customStyles}
        isOpen={modalShow}
        contentLabel="Place Metadata Input"
      >
        <h2>Edit Place:</h2>
        <PlaceTypeForm
          placeTypeId={placeTypeId}
          onSaveData={this.handleSaveData}
          onDelete={this.handleDelete}
          onClose={onClose}
        />
      </Modal>
    );
  }
}

export default withAlert()(EditPlaceTypeModal);

EditPlaceTypeModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the place being edited */
  placeTypeId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  /** Handle reloading the places after editing */
  reloadPlaceTypes: PropTypes.func.isRequired,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func.isRequired,
  /** Id of the city */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
};
