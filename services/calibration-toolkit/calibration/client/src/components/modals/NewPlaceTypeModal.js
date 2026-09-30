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


import axios from "axios";
import update from "immutability-helper";
import PropTypes from "prop-types";
import React, { Component } from "react";
import { withAlert } from "react-alert";
import Modal from "react-modal";
import { modalLayer1 } from "../common/utils";
import PlaceTypeForm from "../forms/PlaceTypeForm";


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
 * Modal to use as popup for entering data to add a new place.
 */
class NewPlaceTypeModal extends Component {
  constructor(props) {
    super(props);
    this.state = {
       placeTypesLength : "",
       placeTypeHierarchy : [],
    };
    

    this.handleNewPlace = this.handleNewPlace.bind(this);
    this.getPlaceTypeLength = this.getPlaceTypeLength.bind(this);
    this.addPlaceTypeHierachy = this.addPlaceTypeHierachy.bind(this);
    this.updateCityData = this.updateCityData.bind(this);
  }
  


  /**
   * Bind the modal to the app element on component mount.
   */
  componentDidMount() {
    Modal.setAppElement("body");
  }

  /**
   * Load place length
   */
   async getPlaceTypeLength() {
    const { cityId } = this.props;
    await axios.get(`${API_ENDPOINT}/cities/${cityId}/`).then(res => {
      const { placeTypes_set } = res.data;
      const placeTypesLength = placeTypes_set.length
      this.setState({
        placeTypesLength
      });
    });
  }


  /**
   * 
   * @param {} updatedData 
   */
  async updateCityData(updatedData){
    const { cityId } = this.props;
    await axios
      .patch(`${API_ENDPOINT}/cities/${cityId}/`, updatedData)
      .then(() => {
        // this.props.reloadPlaces();
        this.props.alert.success("Updated City Data");
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
      console.log("cityData updated",updatedData)
  }

  /**
   * Load place length
   */
   async addPlaceTypeHierachy(placeType) {
    const { cityId } = this.props;
    //id: 1, name: N1, parent: null
    // const place
    var cityData = {}
    await axios.get(`${API_ENDPOINT}/cities/${cityId}/`).then(res => {
      var cityData  = res.data;
      const placeTypeHierarchy = JSON.parse(res.data.placeTypeHierarchy)
      this.setState({
        placeTypeHierarchy
      });
      const newPlaceType = {
        id: this.state.placeTypesLength,
        name: placeType.placeType,
        parent: null
      }
      placeTypeHierarchy.push(newPlaceType)
      const newPlaceTypeHierarchy =  JSON.stringify(placeTypeHierarchy)

      cityData = update(cityData,
        {
          placeTypeHierarchy: {$set: newPlaceTypeHierarchy}
        }
      );
      this.updateCityData(cityData)
    });


  }

  /**
   * Handle event that user selects to add the place based on the data
   * currently populating the form. If all the data is valid, a new place
   * will be created in the backend with the data provided in the form.
   * @param {object} placeData Keys represent attribute in the backend, and
   * value represents the value to be used when creating the place.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
  async handleNewPlace(placeTypeData, dataValidation) {
    const { cityId, projectId } = this.props;
    let cancelSave = false;
    Object.keys(dataValidation).forEach(key => {
      if (!dataValidation[key]) {
        this.props.alert.error(`Error in ${key} field`);
        cancelSave = true;
      }
    });
    if (cancelSave) return;

    await this.getPlaceTypeLength()
    await this.addPlaceTypeHierachy(placeTypeData)
    const {placeTypesLength} =  this.state
    placeTypeData = update(placeTypeData, {
      city: { $set: cityId },
      project: {$set: projectId},
      rank: {$set: placeTypesLength}
    });

    //get placetypehierarchy
    //add placetype to place type hierarchy
    //update placetype
    // cityData = update(cityData, {
    //   placeTypeHierarchy: {$set: placeTypeHierarchy }
    // });
    

    await axios
      .post(`${API_ENDPOINT}/placeTypes/`, placeTypeData)
      .then(() => {
        this.props.alert.success(`Created ${placeTypeData.placeType}`);
        // this.props.reloadPlaces();
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
      this.props.reloadPlaces()

  }

  /** add delete, edit row and add */
  render() {
    const { modalShow, onClose, cityName } = this.props;
    return (
      <Modal
        style={customStyles}
        isOpen={modalShow}
        contentLabel="Sensor Metadata Input"
      >
        <h2>{`Create New PlaceType in ${cityName}:`}</h2>
 
        <PlaceTypeForm onSaveData={this.handleNewPlace} onClose={onClose} />
      </Modal>
    );
  }
}

export default withAlert()(NewPlaceTypeModal);

NewPlaceTypeModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,
  /** ID of the project to add the place to */
  projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** ID of the city to add the place to */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  /** Name of the city to add the place to */
  cityName: PropTypes.string,
  /** Handle reloading the places when a new one has been added */
  reloadPlaces: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func
};
