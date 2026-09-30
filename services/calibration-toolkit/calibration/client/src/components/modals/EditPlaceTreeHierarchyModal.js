// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component } from "react";
import PropTypes from "prop-types";
import { withAlert } from "react-alert";
import Modal from "react-modal";
import update from "immutability-helper";

import axios from "axios";
import { Loader, Form, Header, Button } from "semantic-ui-react";

import { modalLayer1, setEdited,updateDataValue } from "../common/utils";
import { Table } from "semantic-ui-react/";
// import DraggableTableRow from "../common/DraggableTableRow";
import { Fragment } from "react";
import SortableTree, { getFlatDataFromTree, getTreeFromFlatData, removeNodeAtPath } from 'react-sortable-tree';
// import FileExplorerTheme from 'react-sortable-tree-theme-minimal';
  import 'react-sortable-tree/style.css'; // This only needs to be imported once in your app

import {API_ENDPOINT} from "../common/axios_instance";
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
class EditPlaceTypeTreeHierarchyModal extends Component {
  constructor(props) {
    super(props);

    this.state = {
      treeData: [],
      loadingSensors: false,
      placeTypeHierarchy: [],
      placeTypes_set: [],
      cityData: {
        placeTypeHierarchy: [],
        placeTypes_set: []
      },
      isEdited:{
        placeTypeHierarchy: false,
        treeData: false
      },
      dataValidation: {
        placeTypeHierarchy: false
      },
      error: false,
      isLoaded: false,

    }
    
    this.getPlaceData = this.getPlaceData.bind(this)
    this.patchPlaceData = this.patchPlaceData.bind(this)
    this.onSaveData = this.onSaveData.bind(this);
    this.removeNode = this.removeNode.bind(this);
    this.handleDelete = this.handleDelete.bind(this)
    this.handleChange = this.handleChange.bind(this)
  }



  /**
   * Bind the modal to the app element on component mount.
   */
  async componentDidMount() {
    this._isMounted = true;
    Modal.setAppElement("body");
    this.getPlaceData()
    // this.forceUpdate()
  }

  componentWillUnmount() {
    this._isMounted = false;
  }

  /**
   * Update the center then the component updates.
   * @param {object} prevProps Previous props before the update
   */
   componentDidUpdate(prevProps,prevState) {

    const { placeTypeHierarchy } = this.state;

    if (prevState.placeTypeHierarchy !== placeTypeHierarchy ) {
    }
  }


  

  removeNode (node,path,getNodeKey){
    console.log("remomvenode", node,path,getNodeKey)
    console.log("removenode", this.state)
    console.log("removenode", node.name)
    this.setState(
      {
        treeData: removeNodeAtPath({
          treeData: this.state.treeData,
          path,
          getNodeKey,
        })
      }, () => {console.log("asdf", this.state.treeData)}
    
    )
    console.log("this.state changed", this.state.treeData)
    this.handleChange(this.state.treeData)
    // console.log("test123asdf",this.state.cityData)
    // this.onSaveData(this.state.cityData,this.state.dataValidation)
    // Object.values(this.state.placeTypes_set).forEach((placeType)=>{
    //   // placeType_set.map((placeType,index) =>{
    //     // console.log()
    //     // console.log("save",placeType, placeType.id, placeType.rank)
    //     if (placeType.placeType === node.name){
    //       // console.log("placety", placeType.id)
    //       // console.log("placety", node.name)
    //       this.handleDelete(placeType.id,node.name)
    //       this.getPlaceData()
    //     }
    // })


  }

  /**
   * Handle the action of the user choosing to delete the place. Deletes the
   * place from the backend based on its ID and reloads the page to show the
   * place removal.
   */
   async handleDelete(placeTypeId,name) {
    // const { placeTypeId } = this.props;
    await axios
      .delete(`${API_ENDPOINT}/placeTypes/${placeTypeId}/`)
      .then(() => {
      
        // this.props.reloadPlaceTypes();
        this.props.reloadCity();
        this.props.alert.success(`Placetype ${name} deleted`)
        // document.getElementById("Tab_Sensors").click();
      })
      .catch(error => console.error(error));
    // this.forceUpdate()
  }

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
   async onSaveData(changedData,dataValidation) {
    const { cityId } = this.props;
    console.log("changedData",changedData)
    let cityData = {}; 
    Object.keys(dataValidation).forEach(key => {
      console.log("key", dataValidation, key)
      if (dataValidation[key]) {
        cityData = update(cityData, { [key]: { $set: changedData[key] } });
      }
    });
    await axios
      .patch(`${API_ENDPOINT}/cities/${cityId}/`, cityData)
      .then(() => {
        this.props.reloadCity();
        this.props.alert.success("Updated Place Hierarchy");
      })
      .catch(error => {
        const { alert } = this.props;
        if (error.response.data) {
          const { data } = error.response;
          console.log("error here")
          Object.keys(data).forEach(key => {
            alert.error(`Error in ${key}. ${data[key]}`);
          });
        } else if (error.request) {
          console.error("No response from server.");
        }
      });
    console.log("newd", changedData)
    // if (Object.entries(cityData).length === 0) {
    //   this.props.alert.show("No valid changes made");
    //   return;
    // }
    console.log("test", Object.values(changedData))
    // Object.values(changedData.placeTypeHierarchy).forEach((placeType)=>{
    //   // placeType_set.map((placeType,index) =>{
        
    //     console.log("save",placeType, placeType.id, placeType.rank)

    //     const placeData = {"rank": placeType.rank}

    //     this.patchPlaceData(placeType.id, placeData)

    // })
    // this.props.reloadCity();

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
  handleChange(treeData){
    let {cityData, isEdited} = this.state
    let {placeTypeHierarchy} = cityData
    console.log("treedata",this.state.treeData)
    const content = "placeTypeHierarchy"
    console.log("handling change")
    const newEdited = setEdited(isEdited,content)
    const flatData = getFlatDataFromTree({
      treeData: treeData,
      getNodeKey: ({ node }) => node.id, // This ensures your "id" properties are exported in the path
      ignoreCollapsed: false, // Makes sure you traverse every node in the tree, not just the visible ones
    }).map(({ node, path }) => ({
      id: node.id,
      name: node.name,

      // The last entry in the path is this node's key
      // The second to last entry (accessed here) is the parent node's key
      parent: path.length > 1 ? path[path.length - 2] : null,
    }));
    placeTypeHierarchy = flatData
    const newData = updateDataValue(cityData, content, flatData);
    this.setState(
      { 
        cityData: newData,
        placeTypeHierarchy,
        treeData
      })
    this.setState({
      isEdited: newEdited
    })
    console.log("send", newEdited, this.state.placeTypeHierarchy)
    console.log("send", cityData);
    console.log( "send", this.state.cityData);
  }

  async getPlaceData(){
    const { cityId } = this.props;
    console.log("component mounted")
    await axios
      .get(`${API_ENDPOINT}/cities/${cityId}/`)
      .then(res => {
        console.log("place",res.data)
        console.log("place2", JSON.parse(res.data.placeTypeHierarchy))
        // let placeTypeHierarchy = {};
        let placeTypeHierarchy = res.data.placeTypeHierarchy
        const {placeTypes_set} = res.data
        console.log("place3", placeTypes_set)
        const initialData = JSON.parse(placeTypeHierarchy)
        
        const treeData = getTreeFromFlatData({
          flatData: initialData.map(node => ({ ...node, title: node.name })),
          getKey: node => node.id, // resolve a node's key
          getParentKey: node => node.parent, // resolve a node's parent's key
          rootKey: null, // The value of the parent key when there is no parent (i.e., at root level)
        })
        placeTypeHierarchy = initialData
        console.log(treeData)
        this._isMounted &&
        this.setState({
          isLoaded: true,
          treeData,
          placeTypeHierarchy,
          placeTypes_set
          // cityData: {...this.state.cityData, placeTypes_set: placeTypes_set},
          // placeTypes_set
        }, () =>{console.log("done", this.state.treeData)} );
        
      })
      .catch(error => {
        this._isMounted &&
          this.setState({
            isLoaded: true,
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
      reloadCity,
      onSaveData } = this.props;

    const {
      sortedPlaceTypeHierarchy, 
      cityData, 
      dataValidation,
      error,
      isLoaded,
      
    } = this.state
    if (error) {
      return <div data-testid="LoaderError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="LoaderLoading" active inline="centered" />;
    };

    const getNodeKey = ({ treeIndex }) => treeIndex;

    return (
      <Fragment>
        <Modal
          style={customStyles}
          isOpen={modalShow}
          contentLabel="Edit Place Hierarchy"
        >
        <h2>{`Edit Place Hierarchy:`}</h2>

        <div style={{ height: 400 }} className="ui container">

            {/* <Table  className="ui single line table"> */}
              {/* <Table.Header>
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
              </Table.Body> */}
            {/* </Table> */}
            <SortableTree
              treeData={this.state.treeData}
              onChange={treeData => this.handleChange(treeData)}
              // generateNodeProps={({ node, path }) => ({
              //   buttons: [
              //     // <button
              //     //   onClick={() =>
              //     //     this.setState(state => ({
              //     //       treeData: addNodeUnderParent({
              //     //         treeData: state.treeData,
              //     //         parentKey: path[path.length - 1],
              //     //         expandParent: true,
              //     //         getNodeKey,
              //     //         newNode: {
              //     //           title: `${getRandomName()} ${
              //     //             node.title.split(' ')[0]
              //     //           }sson`,
              //     //         },
              //     //         addAsFirstChild: state.addAsFirstChild,
              //     //       }).treeData,
              //     //     }))
              //     //   }
              //     // >
              //     //   Add Child
              //     // </button>,
              //     <button
              //       onClick={() =>
              //         this.removeNode(node,path,getNodeKey)
              //       }
              //     >
              //       Remove
              //     </button>,
              //   ],
              // })}
            />


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
                onClick={onClose}
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

export default withAlert()(EditPlaceTypeTreeHierarchyModal);

EditPlaceTypeTreeHierarchyModal.propTypes = {
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
