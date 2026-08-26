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
import { Loader, Form, Header, Button } from "semantic-ui-react";

import { modalLayer1, setEdited } from "../common/utils";
import { Table } from "semantic-ui-react/";
import DraggableTableRow from "../common/DraggableTableRow";
import { Fragment } from "react";

import {API_ENDPOINT} from "../common/axios_instances";
const customStyles = {
  content: {
    top: "25%",
    left: "25%",
    right: "auto",
    bottom: "auto",
    marginRight: "-00%",
    transform: "translate(-10%, -15%)"
  },
  overlay: {
    zIndex: modalLayer1
  }
};
/**
 * Modal to use as popup for entering data to add a new city.
 */
class EditPlaceHierarchyModal extends Component {
  constructor(props) {
    super(props);

    this.state = {
      loadingSensors: false,
      sortedPlaceTypeHierarchy : [],
      placeTypeDict : {},
      cityData: {
        placeTypes_set:[]
      },
      isEdited:{
        placeTypes_set: false
      },
      dataValidation: {
        placeType_set: false
      },
      error: false,
      isLoaded: false,
      placeTypes_set: []

    }

    this.patchPlaceData = this.patchPlaceData.bind(this)
    this.onSaveData = this.onSaveData.bind(this);
  }



  /**
   * Bind the modal to the app element on component mount.
   */
  async componentDidMount() {
    this._isMounted = true;
    Modal.setAppElement("body");
    this.getPlaceData()
    this.forceUpdate()
  }

  componentWillUnmount() {
    this._isMounted = false;
  }


  async swap(a, b) {
    const {cityId} = this.props
    let { sortedPlaceTypeHierarchy, cityData, dataValidation, isEdited  } = this.state;
    let {placeTypes_set} = cityData

    sortedPlaceTypeHierarchy[a] = sortedPlaceTypeHierarchy.splice(b, 1, sortedPlaceTypeHierarchy[a])[0];
    placeTypes_set[a] = placeTypes_set.splice(b, 1, placeTypes_set[a])[0];
    placeTypes_set[a]['rank'] = a
    placeTypes_set[b]['rank'] = b

    const content = placeTypes_set

    const newEdited = setEdited(isEdited, content);

    this.setState({
      ...this.state,
      sortedPlaceTypeHierarchy,
      placeTypes_set
    });

    this.setState({
      isEdited: newEdited
    });

    console.log("send", cityData);
    console.log( "send", this.state.cityData);


  };


  async patchPlaceData(placeTypeId, placeData){
    await axios
    .patch(`${API_ENDPOINT}/placeTypes/${placeTypeId}/`, placeData)
    .then(() => {
      //Change this to show sensors updated
      this.props.alert.success(`Place Hierarchy saved`);
    })
    .catch(error => {
      const { alert } = this.props;
      console.log(error)
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

  /**
   * @TODO need to fix the comments here
   * Handle event that user selects to add the city based on the data
   * currently populating the form. If all the data is valid, a new city will
   * be created in the backend with the data provided in the form.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be used when creating the city.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
   onSaveData(changedData,dataValidation) {
    const { cityId } = this.props;

    let cityData = {};
    Object.keys(dataValidation).forEach(key => {
      if (dataValidation[key]) {
        cityData = update(cityData, { [key]: { $set: changedData[key] } });
      }
    });
    console.log("newd", changedData)
    // if (Object.entries(cityData).length === 0) {
    //   this.props.alert.show("No valid changes made");
    //   return;
    // }
    console.log("test", Object.values(changedData))
    Object.values(changedData.placeTypes_set).forEach((placeType)=>{
      // placeType_set.map((placeType,index) =>{

        console.log("save",placeType, placeType.id, placeType.rank)

        const placeData = {"rank": placeType.rank}

        this.patchPlaceData(placeType.id, placeData)


    })
    // await axios
    // .patch(`${API_ENDPOINT}/cities/${cityId}/`, cityData)
    // .then(() => {
    //   //Change this to show sensors updated
    //   this.props.alert.success(`Place Hierarchy saved`);
    // })
    // .catch(error => {
    //   const { alert } = this.props;
    //   console.log(error)
    //   if (error.response.data) {
    //     const { data } = error.response;
    //     Object.keys(data).forEach(key => {
    //       alert.error(`Error in ${key}. ${data[key]}`);
    //     });
    //   } else if (error.request) {
    //     console.error("No response from server.");
    //   }
    // });


    // await axios
    //   .patch(`${API_ENDPOINT}/cities/${cityId}/`, cityData)
    //   .then(() => {
    //     //Change this to show sensors updated
    //     this.props.alert.success(`MMS URL saved`);
    //     this.props.reloadSensors();
    //   })
    //   .catch(error => {
    //     const { alert } = this.props;
    //     console.log(error)
    //     if (error.response.data) {
    //       const { data } = error.response;
    //       Object.keys(data).forEach(key => {
    //         alert.error(`Error in ${key}. ${data[key]}`);
    //       });
    //     } else if (error.request) {
    //       console.error("No response from server.");
    //     }
    //   });
  }
  /**
   * @TODO needs to be updated
   * Request the backend to download the map value given the download link
   * provided by the user. If the link doesn't download correctly, a new city
   * will not be created.
   * @param {number} cityId ID representing city to download map file for
   */
  // async importSensors(cityId) {
  //   const { alert } = this.props;
  //   this.setState({ loadingSensors: true });
  //   await axios
  //     .get(`${API_ENDPOINT}/discoverSensors/${cityId}/`)
  //     .then(() => {
  //       alert.success("Importing Sensors");
  //       this.setState({ loadingMapFile: false });
  //     })
  //     .catch(async error => {
  //       alert.error("Invalid Map File Url. City Deleted.");
  //       await axios
  //         .delete(`${API_ENDPOINT}/mms/${cityId}/`)
  //         .then(res => {})
  //         .catch(error => console.error("Delete city error", error));
  //       this.props.reloadSensors();
  //       this.setState({ loadingMapFile: false });
  //     });
  // }
  // updateSortedState(state,sortedPlaceTypeHierarchy){
  //   const newState = {...state, () => { sortedPlaceTypeHierarchy: sortedPlaceTypeHierarchy,
  //     cityData: {...state.cityData, placeTypes_set: res.data.placeTypes_set} }
  //   }
  //   return newState
  // }

  async getPlaceData(){
    const { cityId } = this.props;
    await axios
      .get(`${API_ENDPOINT}/cities/${cityId}/`)
      .then(res => {
        console.log("place",res.data)
        // let clonedResponse = { ...res }
        // const {placeTypes_set} = clonedResponse.data;
        // let placeTypeHierarchy = ;
        let placeTypeHierarchy = new Map();

        const placeTypes_set = {};
        placeTypes_set = res.data.placeTypes_set;

        placeTypes_set.forEach((placeType) => {
          let name = typeof placeType.placeType === 'string' ? placeType.placeType : null
          let num =  typeof placeType.rank === 'number' ? placeType.rank : null
          // let num = placeType.num
          if (name) { 
            placeTypeHierarchy.set(name, num);
            console.log("pt", placeType)
          }
        });
        console.log("pth", placeTypeHierarchy)
        var sortedPlaceTypeHierarchy = []

        for (var key in placeTypeHierarchy) {
          sortedPlaceTypeHierarchy.push([ key, placeTypeHierarchy[key] ])
        }
        sortedPlaceTypeHierarchy.sort(function compare(kv1, kv2) {
          return kv1[1] - kv2[1]
        })

        console.log("spth", sortedPlaceTypeHierarchy)
        sortedPlaceTypeHierarchy.map((obj,i) =>{
          console.log("sort,",obj,i)
        })

        // const newState = update(this.state, { placeTypes_set: { $set: res.data.placeTypes_set } });
        // console.log("check", cityData)
        this._isMounted &&
        this.setState({
          isLoaded: true,
          sortedPlaceTypeHierarchy,
          cityData: {...this.state.cityData, placeTypes_set: placeTypes_set},
          placeTypes_set
        }, () =>{console.log("done", this.state.cityData)} );

      })
      .catch(error => {
        this._isMounted &&
          this.setState({
            error
          });
        console.log("aslfjd", error)
      });
    }

  render() {
    const {
      modalShow,
      onClose,
      cityId,
      reloadSensors,
      onSaveData } = this.props;

    const {
      sortedPlaceTypeHierarchy,
      cityData,
      dataValidation,
      error,
      isLoaded
    } = this.state
    // console.log("render",sortedPlaceTypeHierarchy)
    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    }
    return (
      <Fragment>
        <Modal
          style={customStyles}
          isOpen={modalShow}
          contentLabel="Edit Place Hierarchy"
        >
        <h2>{`Edit Place Hierarchy:`}</h2>

        <div className="ui container">
            <style>{`
              .draggable {
                cursor: move; /* fallback if grab cursor is unsupported */
                cursor: grab;
                cursor: -moz-grab;
                cursor: -webkit-grab;
              }
            `}</style>
            <Table  className="ui single line table">
              <Table.Header>
                <Table.Row>
                  <Table.HeaderCell>PlaceType</Table.HeaderCell>
                  <Table.HeaderCell>Edit</Table.HeaderCell>
                  <Table.HeaderCell>Delete</Table.HeaderCell>
                </Table.Row>
              </Table.Header>

              <Table.Body>
                {sortedPlaceTypeHierarchy.map((obj, i) => (
                  <DraggableTableRow key={i} i={i} action={this.swap.bind(this)}>
                    <Table.Cell>{obj[0]}</Table.Cell>
                    <Table.Cell>{"Edit Row"}</Table.Cell>
                    <Table.Cell>{"Delete row"}</Table.Cell>

                  </DraggableTableRow>
                ))}
              </Table.Body>
            </Table>

          </div>
          <div className="ui horizontal divider"> </div>
          <div>
            <Button
                color="green"
                onClick={() => {
                  // this.setAllEdited();
                  this.onSaveData(cityData, dataValidation);
                }}
                type="button"
                data-testid="CamFormApply"
              >
                Apply
              </Button>
            <Button
                color="purple"
                onClick={() => onClose()}
                data-testid="CamFormClose"
                type="button"
              >
                Close
              </Button>



          </div>

        </Modal>
      </Fragment>
    );
  }
}

export default withAlert()(EditPlaceHierarchyModal);

EditPlaceHierarchyModal.propTypes = {
  /** Indicator whether or not the modal is being shown */
  modalShow: PropTypes.bool.isRequired,

  /** ID of the project to add the city to */
  cityId: PropTypes.oneOfType([PropTypes.number, PropTypes.string])
    .isRequired,
  /** Handle reloading the cities after new city added */
  reloadSensors: PropTypes.func,
  /** Handle the action of closing the modal */
  onClose: PropTypes.func,
  /** Handle the action of saving the PlaceType Hierarchy */
  // onSaveData: PropTypes.func.isRequired
};
