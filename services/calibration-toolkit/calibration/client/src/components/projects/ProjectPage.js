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
import { Header, Button,  Grid, Loader } from "semantic-ui-react";
import update from "immutability-helper";
import { withAlert } from "react-alert";

// import Sidebar from '../../components/SideBar/Sidebar'
// import Menubar from "../common/Menubar";
// import CitiesGrid from "./CitiesGrid";
// import DeleteButton from "../common/DeleteButton";
import UploadWebApiModal from "../modals/UploadWebApiModal";
import PropTypes from "prop-types";

import {
  getPlaceDict,
  getIntersectionSegments,
  getGISPolygons,
  getCartesianPolygons,
  getMTMCPolygons,
  convertToCSV,
  checkCalibrationJSON,
  getFloorPlanPolygons,
  getImagePolygons,
  getIntersectionSegmentsForExport
} from "../common/JSONExportUtils";
// import {API_ENDPOINT} from "../common/axios_instances";
import {API_ENDPOINT} from "../common/axios_instance";
import DOMPurify from "dompurify";

/**
 * Page to display information about a project, given a project ID
 * passed via the URL. The specific information shown is the city assigned to
 * the project, the sensors/complete sensors, the intersections/completed road
 * networks, and the corridors/completed corridors. A project page takes no
 * props, except for the projectId passed through the URL.
 */
class ProjectPage extends Component {
  constructor(props) {
    super(props);
    this.state = {
      error: null,
      isLoaded: false,
      projectName: null,
      validName: true,
      sensors: 0,
      completeSensors: 0,
      intersections: 0,
      completeIntersections: 0,
      corridors: 0,
      completeCorridors: 0,
      UploadWebApiSensorModal: false,

      // sensorOnly: false
    };

    this.handleDelete = this.handleDelete.bind(this);
    this.downloadData = this.downloadData.bind(this);
    this.reloadPage = this.reloadPage.bind(this);
    this.loadPage = this.loadPage.bind(this);
    this.handleNameChange = this.handleNameChange.bind(this);
    this.getCalibrationJSON = this.getCalibrationJSON.bind(this);
    this.getSensorsCSV = this.getSensorsCSV.bind(this);
    this.getNetworkJSON = this.getNetworkJSON.bind(this);
    this.getCorridorJSON = this.getCorridorJSON.bind(this);
    this.getWarpedImages = this.getWarpedImages.bind(this);
    this.getFloorPlanImages = this.getFloorPlanImages.bind(this);
    this.getImageJson = this.getImageJson.bind(this)
    this.getImages = this.getImages.bind(this)
    this.handleSaveData = this.handleSaveData.bind(this);
    this.toggleUploadWebApiModal = this.toggleUploadWebApiModal.bind(this);


  }

  /**
   * Load the data in the project and calculate the sensor, intersection, and
   * corridor statistics on component mount.
   */
  async componentDidMount() {
    console.log("a")
    this.setState({ isLoaded: false });
    await this.loadPage();
    console.log("a1", this.state)
  }

  /**
   * Reload the page (runs if there are updates)
   */
  reloadPage() {
    console.log("reload")
    this.setState({ isLoaded: false });
    this.loadPage();
  }

  /**
   * Function to load the project data from the backend to display on the page
   */
  async loadPage(isLoaded) {
    console.log("b")
    const { projectId } = this.props;
    //@TODO this looks wrong
    this.setState({ projectName: "projectname" });
    console.log("b001", this.state)
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const projectName = res.data.name;
        const project = res.data
        let sensors = 0;
        let completeSensors = 0;
        let intersections = 0;
        let completeIntersections = 0;
        let corridors = 0;
        let completeCorridors = 0;
        // let sensorOnly = false;
        let calibrationType = "geo"
        completeSensors = countCompleted(project.sensor_set, "sensors");
        sensors = project.sensor_set.length;
        calibrationType = project.calibrationType
        intersections = project.intersection_set.length;
        completeIntersections = countCompleted(
          project.intersection_set,
          "intersections"
        );
        // corridors = project.corridor_set.length;
        // completeCorridors = countCompleted(project.corridor_set, "corridors");
        // if (res.data.city_set.length > 0) {
        //   const city = res.data.city_set[0];
        //   console.log("pp", city)
        //   // sensors = project.sensor_set.length;
        //   // completeSensors = countCompleted(project.sensor_set, "sensors");
        //   intersections = city.intersection_set.length;
        //   completeIntersections = countCompleted(
        //     project.intersection_set,
        //     "intersections"
        //   );
        //   corridors = project.corridor_set.length;
        //   completeCorridors = countCompleted(project.corridor_set, "corridors");
        //   // sensorOnly = city.sensorOnly
        // }
        console.log( "b01"
        )
        this.setState({
          isLoaded: true,
          projectName,
          sensors,
          completeSensors,
          intersections,
          completeIntersections,
          corridors,
          completeCorridors,
          // sensorOnly,
          calibrationType
        });
        console.log("b1", this.state)
      })
      .catch(error => this.setState({ error, isLoaded: false }));
  console.log("baba",this.state)
  }

  /**
   * Patch the backend with the new project name if the name in the state has
   * changed. Also setup to reload project data instead of the whole page if
   * the projectID changes.
   * @param {object} prevProps Previous props before update.
   * @param {object} prevState Previous state before update.
   */
  componentDidUpdate(prevProps, prevState) {
    const { projectId } = this.props;
    console.log("c", this.state)
    const { projectName } = this.state.projectName;
    console.log("project page", projectName , prevState.projectName )
    if (projectId !== prevProps.projectId) {
      this.reloadPage();
    }
    // else if (projectName.length > 0 && projectName.length <= 200) {
    //   if (projectName !== prevState.projectName) {
    //     const { projectId } = this.props.match.params;
    //     axios
    //       .patch(`${API_ENDPOINT}/projects/${projectId}/`, {
    //         name: projectName
    //       })
    //       .then()
    //       .catch();
    //   }
    // }
  }

  /**
   * Handle the deletion of the project. Deletes the project and all cities,
   * sensors, intersections, and corridors belonging to it from the backend.
   * Redirects back to the homepage.
   */
  async handleDelete() {
    const { projectId, history } = this.props;
    // const { projectId } = match.params;
    await axios
      .delete(`${API_ENDPOINT}/projects/${projectId}`)
      .then(res => {
        this.props.alert.success("Deleted project");
      })
      .catch(error => console.error(error));

    history.push(`/`);
  }

  /**
   * Change the name in the state when the name is changed in the input form.
   * @param {object} e Event object passed on name change
   */
  handleNameChange(e) {
    const { value } = e.target;
    const { projectName } = this.state;
    if (projectName.length <= 200) {
      this.setState({
        validName: true,
        projectName: update(projectName, {
          $set: value
        })
      });
    } else {
      this.setState({
        validName: false,
        projectName: update(projectName, {
          $set: value
        })
      });
    }
  }

  /**
   * Downloads a json file to the downloads folder of the user's computer
   * @param {object} JSONObject JSON data to save to file
   * @param {string} filename Name of file to save JSON data to
   */
  downloadJSON(JSONObject, filename) {
    const blob = new Blob([JSON.stringify(JSONObject, null, 2)], {
      type: "application/json"
    });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "download";
    const clickHandler = () => {
      setTimeout(() => {
        URL.revokeObjectURL(url);
        a.removeEventListener("click", clickHandler);
      }, 150);
    };
    a.addEventListener("click", clickHandler, false);
    a.click();
  }



  /**
   * Downloads a CSV file to the downloads folder of the user's computer
   * @param {object} CSVObject CSV data to save to file
   * @param {string} filename Name of file to save CSV data to
   */
  downloadCSV(CSVObject, filename) {
    const json = JSON.stringify(CSVObject);
    const csv = convertToCSV(json)
    // console.log(csv)
    const blob = new Blob([csv], {
      type: "text/csv"
    });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || "download";
    const clickHandler = () => {
      setTimeout(() => {
        URL.revokeObjectURL(url);
        a.removeEventListener("click", clickHandler);
      }, 150);
    };
    a.addEventListener("click", clickHandler, false);
    a.click();
  }

    /**
   * Downloads a Zip file to the downloads folder of the user's computer
   * @param {object} ZIPObject Zip data to save to file
   * @param {string} filename Name of file to save CSV data to
   */
     downloadZipFile(ZIPObject, filename) {

      const blob = new Blob([ZIPObject],
        {
        type: "application/zip"
      }
      );
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute(
        "download",
        // "Warped Images.zip"
        filename
      );

      const clickHandler = () => {
        setTimeout(() => {
          URL.revokeObjectURL(url);
          link.removeEventListener("click", clickHandler);
        }, 180);
      };
      link.addEventListener("click", clickHandler, false);
      link.click();
    }


  /**
   * Create and save the calibration.json file to the users downloads folder.
   */
  async getCalibrationJSON() {
    const { projectId } = this.props;
    const { alert } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        // console.log("projects cj", res.data)
        const {  mapFile, calibrationType, intersection_set, corridor_set, placeTypes_set,
                 sensor_set, originLat, originLng, name, cityPlace, roomPlace } = res.data;
        // console.log("projects cj1", placeTypes_set)
        this.setState({calibrationType: calibrationType})
        // const { calibrationType, mapFile, placeTypes_set, intersection_set, corridor_set, sensor_set } = res.data;

        let calibrationJSON = {
          version: "1.0",
          // docType: "calibration",
          osmURL: "",
          calibrationType: "",
          sensors: [],
          corridors: []
        };
        var placeMap = {}

        //Temp
        // const placeTypes_set = []
        // let corridor_set = []
        const projOriginLat = originLat
        const projOriginLng = originLng
        const projCity = cityPlace
        const projRoom = roomPlace
        placeMap = getPlaceDict(placeTypes_set, intersection_set, corridor_set)
        console.log("PP cp12", placeMap)
        if (calibrationType === 'geo'){
          calibrationJSON.calibrationType = "geo"
          calibrationJSON.osmURL = mapFile;
          //console.log("sensorgroupings", sensorGroupings)
          let sensorGroupings = []
          //calibrationJSON.sensors.push(sensor_set)
          corridor_set.forEach(corridor => {

            if (corridor.length > 0) {

              let directions = JSON.parse(corridor.directions);
              let newDirections = directions.join(", ")
              const { length, name } = corridor;
              let cooridor_sensor_set = corridor.sensor_set
              let sensors = []
              cooridor_sensor_set.forEach(
                sensor => {
                  const {deviceId} = sensor;
                  sensors.push(deviceId)
                }
              );
              calibrationJSON.corridors.push({
                directions,
                length,
                name: name,
                sensors: sensors,
              });
            

                //   sensorGroupings:[
                //     {
                //         type: "corridor",
                //         groups:[
                //             {
                //                 name: "US20", // city can be obtained from place array
                //                 sensors: ["x","y"],
                //                 attributes: [
                //                     {
                //                         name: "directions" ,
                //                         value: "E,W"
                //                     },
                //                     {
                //                         name: "length",
                //                         value:
                //                     }
                //                 ]
                //             }
                //         ]
                //      }
                // ]


              // let groups = []
              // let attributes = []
              
              // attributes.push({name:"directions", value: newDirections})
              // attributes.push({name:"length", value: length})

              // console.log("attributes", attributes)
              // groups.push({
              //   name,
              //   sensors: [],
              //   attributes
              // })
              // console.log("groups", groups)
              // sensorGroupings.push({
              //   type: "corridor",
              //   groups: groups
              // });
              // console.log("sensorGroupings", sensorGroupings)
              //     calibrationJSON.sensorGroupings = sensorGroupings
              //     console.log("sensorGroupings", sensorGroupings)
              //     console.log("calibrationJSON", calibrationJSON)
                }
              });
        } else if (calibrationType === 'cartesian'){
          calibrationJSON.calibrationType = "cartesian"

        } else if (calibrationType === 'floorplan'){
          calibrationJSON.calibrationType = "cartesian"

        } else if (calibrationType === 'mtmc'){
          calibrationJSON.calibrationType = "cartesian"

        }else if (calibrationType === 'image'){
          calibrationJSON.calibrationType = "image"

        }
        // var cityOriginLat = 0.0
        // var cityOriginLng = 0.0
        // let sensorList = []
        // city_set.forEach(
        //   city => {
        //     const {name,mapFile, calibrationType, placeTypes_set, intersection_set, corridor_set, originLat, originLng} = city;
        //     placeMap = getPlaceDict(placeTypes_set, intersection_set, corridor_set)
        //     this.setState({calibrationType: calibrationType})
        //     calibrationJSON.city = name;
        //     calibrationJSON.osmURL = mapFile;
        //     cityOriginLat = originLat
        //     cityOriginLng = originLng
        //     // if (!sensorOnly)
        //     if (calibrationType === "geo")
        //     {
        //       calibrationJSON.calibrationType = "geo";
        //       // console.log("corridor", corridor_set)
        //       corridor_set.forEach(corridor => {

        //         if (corridor.length > 0) {

        //           const directions = JSON.parse(corridor.directions);
        //           const { length, name } = corridor;
        //           // sensorList = getSensorsCorridors(corridor)
        //           // let sensors = []
        //           // console.log("corridor",corridor)
        //           calibrationJSON.corridors.push({
        //             directions,
        //             length,
        //             name: name,
        //             sensors: []
        //           });
        //         }
        //       });
        //     }
        //     else if (calibrationType === "cartesian")
        //     {
        //       calibrationJSON.calibrationType = "cartesian";
        //     }
        //     else if (calibrationType === "floorplan")
        //     {
        //       calibrationJSON.calibrationType = "cartesian";
        //     }else if (calibrationType === "mtmc")
        //     {
        //       calibrationJSON.calibrationType = "cartesian";
        //     }


        //   }
        // )

        sensor_set.forEach(sensor => {
          // console.log("sensor", sensor.sensorId)
          if (sensor.isCalibrated && sensor.isValidated ) {
            const { sensorId, originLat, originLng, coordinates, scaleFactor } = sensor;
            const id = sensorId;
            console.log("pp1132", sensorId)
            const geoLocation = {lat: originLat, lng: originLng};

            if (this.state.calibrationType === "geo")
            {
              const {
              imageCoordinates,
              globalCoordinates,
              rois,
              tripwires,
              place,
              corridor_map,
              attributes,
              coordinates
            } = getGISPolygons(sensor,placeMap);
            const origin = {lat: projOriginLat, lng: projOriginLng};
            const new_place = {name: "city", value: name};
            place.unshift(new_place);
            calibrationJSON.sensors.push({
              id,
              type: "camera",
              imageCoordinates,
              globalCoordinates,
              origin,
              geoLocation,
              rois,
              place,
              tripwires,
              attributes,
              coordinates,
              scaleFactor
            });
            // calibrationJSON.corridors.forEach((corridor) =>{
            //   corridor_map.forEach((entry) =>{
            //     if (entry.value === corridor.name){
            //         corridor.sensors.push(sensor.sensorId)
            //     }
            //   });
            // });
            // calibrationJSON.sensorGroupings.forEach((group) =>{
            //   if(group.type === "corridor"){
            //     group.groups.forEach((item) => {

            //       corridor_map.forEach((entry) =>{
            //         console.log("PP cp", entry, group)

            //         if (entry.value === item.name){
            //               item.sensors.push(sensor.sensorId)
            //         }
            //        });
            //     })
            //   }
            // });
            console.log("PP cp, cjjj", calibrationJSON)
          }else if (this.state.calibrationType === "cartesian")
          {
              const {
                imageCoordinates,
                globalCoordinates,
                rois,
                place,
                tripwires,
                // corridor_map,
                attributes,
                coordinates,
                // origin
              } = getCartesianPolygons(sensor,placeMap, name, projCity, projRoom);
              const origin = {lat: projOriginLat, lng: projOriginLng};

              calibrationJSON.sensors.push({
                id,
                type: "camera",
                imageCoordinates,
                globalCoordinates,
                origin,
                geoLocation,
                coordinates,
                scaleFactor: 100,
                rois,
                place,
                tripwires,
                attributes
              });
          } else if (this.state.calibrationType === "floorplan"){
            console.log("floorplan")
            const {
              imageCoordinates,
              globalCoordinates,
              rois,
              place,
              tripwires,
              // corridor_map,
              attributes,
              origin
            } = getFloorPlanPolygons(sensor,placeMap);

            calibrationJSON.sensors.push({
              id,
              type: "camera",
              imageCoordinates,
              globalCoordinates,
              origin,
              geoLocation,
              coordinates,
              scaleFactor,
              rois,
              place,
              tripwires,
              attributes
            });

          }else if (this.state.calibrationType === "mtmc"){
            console.log("mtmc")
            const {
              imageCoordinates,
              globalCoordinates,
              rois,
              place,
              tripwires,
              attributes,
              coordinates,
              origin
            } = getMTMCPolygons(sensor,placeMap,name);

            calibrationJSON.sensors.push({
              id,
              type: "camera",
              imageCoordinates,
              globalCoordinates,
              coordinates,
              origin,
              geoLocation,
              scaleFactor,
              attributes,
              rois,
              place,
              tripwires
            });

          }
          else if (this.state.calibrationType === "image"){
            console.log("image")
            const {
              imageCoordinates,
              globalCoordinates,
              rois,
              place,
              tripwires,
              attributes,
              coordinates,
              origin
            } = getImagePolygons(sensor,placeMap);

            calibrationJSON.sensors.push({
              id,
              type: "camera",
              imageCoordinates,
              globalCoordinates,
              coordinates,
              origin,
              geoLocation,
              scaleFactor,
              attributes,
              rois,
              place,
              tripwires
            });

          }
          }

        });
        // console.log("cjson", calibrationJSON)
        //check Calibration json
        calibrationJSON = checkCalibrationJSON(calibrationJSON)
        this.downloadJSON(calibrationJSON, "calibration.json");
        this.handleSaveData(JSON.stringify(calibrationJSON),"calibrationJson")
        alert.success("calibration.json downloaded");
      })
      .catch(error => {
        console.log(error)
        alert.error("Calibration.json error");
        if (error.message) {
          alert.error(error.message);
        }
        if( this.state.calibrationType === "mtmc"){
          alert.error("Ensure Setup Floor Plan is complete!")
        }
      });
  }

  /**
   * Handle event that user saves the data input into the form. The sensor ID is
   * used to patch the sensor in the backend. Only data that is changed and is
   * valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
   async handleSaveData(json,key) {
    const { projectId } = this.props;
    // console.log ("do we get here data", projectId)
    let projectData = {};
    projectData[key] = json
    // console.log("push update", projectData)
    await axios
    .patch(`${API_ENDPOINT}/projects/${projectId}/`, projectData)
    .then()
    .catch(error => console.error("err", error));

  }

    /**
   * Handle event that user saves the data input into the form. The sensor ID is
   * used to patch the sensor in the backend. Only data that is changed and is
   * valid is patched to the backend.
   * @param {object} changedData Keys represent attribute in the backend, and
   * value represents the value to be patched.
   * @param {object} dataValidation Keys represent attribute in the backend, and
   * value is a boolean that is true if and only if the data belonging to that
   * key has been updated and is valid.
   */
     async downloadData(json,key) {
      const { projectId } = this.props;
      // console.log ("do we get here dd", projectId)
      this.getCalibrationJSON()
      // this.getSensorsCSV()
      // this.getNetworkJSON()
      console.log("data downloaded")
      // let projectData = {};
      // projectData[key] = json
      // console.log("push update", projectData)
      // await axios
      // .patch(`${API_ENDPOINT}/projects/${projectId}/`, projectData)
      // .then()
      // .catch(error => console.error("err", error));

    }



  /**
   * Create and save the calibration.json file to the users downloads folder.
   */
  async getSensorsCSV() {
    const { projectId } = this.props;
    const { alert } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        const { sensor_set } = res.data;
        // const headers = [
        //   { label: "sensorId", key: "" },
        //   { label: "rtspURL", key: "" },
        //   { label: "mmsInfo.protocol", key: "" },
        //   { label: "mmsInfo.host", key: "" },
        //   { label: "mmsInfo.type", key: "" },
        //   { label: "fps", key: "" },
        //   { label: "deviceId", key: "" },
        //   { label: "videoURL", key: "" },
        //   { label: "depth", key: "" },
        //   { label: "fieldOfView", key: "" },
        //   { label: "direction", key: "" },
        // ]
        const headers =[

            "sensorId",
            "rtspURL",
            "mmsInfo.protocol",
            "mmsInfo.host",
            "mmsInfo.type",
            "fps",
            "deviceId",
            "videoURL",
            "depth",
            "fieldOfView",
            "direction"

        ];
        let sensorCSV = [headers];
        sensor_set.forEach(sensor => {
          if (sensor.isCalibrated && sensor.isValidated ) {
            const {
              sensorId,
              rtspURL,
              mmsInfo_protocol,
              mmsInfo_host,
              mmsInfo_type,
              fps,
              deviceId,
              videoURL,
              depth,
              fieldOfView,
              direction
            } = sensor;

            sensorCSV.push({
              sensorId,
              rtspURL,
              mmsInfo_protocol,
              mmsInfo_host,
              mmsInfo_type,
              fps,
              deviceId,
              videoURL,
              depth,
              fieldOfView,
              direction
            });


          }

        });
        console.log("asdfa", sensorCSV)
        console.log("do i get here")
        this.downloadCSV(sensorCSV, "sensorMetadata.csv");
        console.log("test", (sensorCSV))
        this.handleSaveData(JSON.stringify(sensorCSV),"sensorMetadataCsv")
        alert.success("sensorMetadata.csv downloaded");
      })
      .catch(error => {
        alert.error("sensorMetadata.csv error");
        if (error.message) {
          alert.error(error.message);
        }
      });
  }

  /**
   * Create and save the network.json file to the users downloads folder.
   */
  async getNetworkJSON() {
    const { projectId } = this.props;
    const { alert } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        let networkJSON = {
          city: "not defined",
          docType: "roadNetwork",
          intersections: []
        };
        const cleanData = res.data
        const { intersection_set, name } = res.data;
        // if (cleanData.city_set.length > 0) {
        //   const cityName = cleanData.city_set[0].name;

        networkJSON.city = name;
        
        intersection_set.forEach(intersection => {
          if (intersection.linksAreDrawn && intersection.linksAreValid) {
            const { name } = intersection;
            const segments = getIntersectionSegmentsForExport(intersection);

            networkJSON.intersections.push({ name, segments });
          }

        });
        
        // console.log(networkJSON)
        this.downloadJSON(networkJSON, "roadNetwork.json");
        this.handleSaveData(JSON.stringify(networkJSON),"roadNetworkJson")
        alert.success("network.json downloaded");
      })
      .catch(error => {
        alert.error("network.json error");
        if (error.message) {
          alert.error(error.message);
        }
      });
  }

  /**
   * Create and save the corridor.json file to the users downloads folder.
   */
  async getCorridorJSON() {
    const { projectId } = this.props;
    const { alert } = this.props;
    await axios
      .get(`${API_ENDPOINT}/projects/${projectId}/`)
      .then(res => {
        let corridorJSON = { corridors: [] };
        if (res.data.city_set.length > 0) {
          const cityName = res.data.city_set[0].name;
          const { corridor_set } = res.data.city_set[0];

          console.log("corridor",corridor_set)
          corridor_set.forEach(corridor => {
            if (corridor.length > 0) {
              const directions = JSON.parse(corridor.directions);
              const { length, name } = corridor;

              corridorJSON.corridors.push({
                directions,
                // docType: "corridor",
                length,
                corridorName: name,
                cityName
              });
            }
          });
        }

        this.downloadJSON(corridorJSON, "corridor.json");
        alert.success("corridor.json downloaded");
      })
      .catch(error => {
        alert.error("corridor.json error");
        if (error.message) {
          alert.error(error.message);
        }
      });
  }
    /**
   * Toggle if the upload web api modal is shown or not.
   */
     toggleUploadWebApiModal() {
      const { UploadWebApiSensorModal } = this.state;
      this.setState({ UploadWebApiSensorModal: !UploadWebApiSensorModal });
    }

  /**
  * Create and get zipfile of warped images and save to User's downloads
  */
  async getWarpedImages() {
    const { projectId } = this.props;
    const { alert } = this.props;
    await axios
      .get(
        `${API_ENDPOINT}/getWarpedFiles/${projectId}/`,
        {
          responseType: 'arraybuffer'
        }
      )
      .then(res => {
        const disposition = res.request.getResponseHeader('Content-Disposition')
        var fileName = "";
        var filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
        var matches = filenameRegex.exec(disposition);
        if (matches != null && matches[1]) {
            fileName = matches[1].replace(/['"]/g, '');
        }
        this.downloadZipFile(res.data, "Warped Images.zip");
        alert.success("Warped Images Zipfile Downloaded");
      })
      .catch(error => {
        alert.error("Error in Creating Zipfile");
        if (error.message) {
          alert.error(error.message);
        }
      });
  }
/**
       * Create and save the calibration.json file to the users downloads folder.
       */
 async getImageJson() {
  const { projectId } = this.props;
  const { alert } = this.props;
  await axios
    .get(`${API_ENDPOINT}/projects/${projectId}/`)
    .then(res => {
      const cleanData = res.data
      const { sensor_set } = cleanData;
      // const headers = [
      //   { label: "sensorId", key: "" },
      //   { label: "rtspURL", key: "" },
      //   { label: "mmsInfo.protocol", key: "" },
      //   { label: "mmsInfo.host", key: "" },
      //   { label: "mmsInfo.type", key: "" },
      //   { label: "fps", key: "" },
      //   { label: "deviceId", key: "" },
      //   { label: "videoURL", key: "" },
      //   { label: "depth", key: "" },
      //   { label: "fieldOfView", key: "" },
      //   { label: "direction", key: "" },
      // ]

    //   "   "images":[
    //     {
    //         "sensorId":"",
    //         "fileName": "",
    //         "view": "sensor-view/warped-sensor-view/plan-view"
    //      },
    //     {
    //        "place":"",
    //         "fileName": "",
    //         "view": "sensor-view/warped-sensor-view/plan-view"
    //     }
    //  ]"

      let imageMetaDataJson  = {
        images: []
      };

      sensor_set.forEach(sensor => {
        if (sensor.isCalibrated && sensor.isValidated ) {
          const {
            sensorId,
            imageUrl,
            invertImageUrl
          } = sensor;
          if (cleanData.calibrationType === "cartesian" ){

              let fileNameArray = invertImageUrl.split("/")
              let fileName = fileNameArray[fileNameArray.length -1]
              let view = "warped-camera-view"
              imageMetaDataJson.images.push({
                sensorId,
                fileName,
                view
              });

              let imFileNameArray = imageUrl.split("/")
              let imFileName = imFileNameArray[fileNameArray.length -1]
              let cview = "camera-view"
              imageMetaDataJson.images.push({
                sensorId,
                fileName: imFileName,
                view: cview
              });

          } else if (cleanData.calibrationType === "mtmc" || cleanData.calibrationType === "geo" ){
            
            let fileNameArray = imageUrl.split("/")
            let fileName = fileNameArray[fileNameArray.length -1]
            let view = "camera-view"
            imageMetaDataJson.images.push({
              sensorId,
              fileName,
              view
            });

        } else if (cleanData.calibrationType === "image" ){

          let fileNameArray = imageUrl.split("/")
          let fileName = fileNameArray[fileNameArray.length -1]
          let view = "camera-view"
          imageMetaDataJson.images.push({
            sensorId,
            fileName,
            view
          });

      }




        }

      });

      if (cleanData.calibrationType === "mtmc" ){

        let fileNameArray = cleanData.floorPlanImageUrl.split("/")
        let fileName = fileNameArray[fileNameArray.length -1]
        let view = "plan-view"
        let place = "building=" + cleanData.name
        imageMetaDataJson.images.push({
          place,
          fileName,
          view
        });

    }
      // console.log("asdfa", imageMetaDataJson)
      // console.log("do i get here")
      this.downloadJSON(imageMetaDataJson, "imageMetadata.json");
      // console.log("test", (imageMetaDataJson))
      this.handleSaveData(JSON.stringify(imageMetaDataJson),"imageMetaDataJson")
      alert.success("image metadata downloaded");
    })
    .catch(error => {
      alert.error("imageMetadata.json error");
      if (error.message) {
        alert.error(error.message);
      }
    });
}
 /**
  * Create and get zipfile of warped images and save to User's downloads
  */
  async getFloorPlanImages() {
    const { projectId } = this.props;
    const { alert } = this.props;
    await axios
      .get(
        `${API_ENDPOINT}/getFloorPlanFiles/${projectId}/`,
        {
          responseType: 'arraybuffer'
        }
      )
      .then(res => {
        const disposition = res.request.getResponseHeader('content-disposition')
        // var fileName = "";
        var filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
        var matches = filenameRegex.exec(disposition);
        let fileName = "FloorPlan Images.zip"; // Default filename
        if (matches != null && matches[1]) {
          fileName = matches[1].replace(/['"]/g, '');
        }

        this.downloadZipFile(res.data, fileName);
        // this.downloadZipFile(res.data, "FloorPlan Images.zip");
        alert.success("FloorPlan Images Zipfile Downloaded");
      })
      .catch(error => {
        alert.error("Error in Creating Zipfile");
        if (error.message) {
          alert.error(error.message);
        }
      });
  }

  async getImages(){
    const { projectId } = this.props;
    const { alert } = this.props;
    console.log("Getting Images")
    await axios
      .get(
        `${API_ENDPOINT}/getImageFiles/${projectId}/`,
        {
          responseType: 'arraybuffer'
        }
      )
      .then(res => {
        const disposition = res.request.getResponseHeader('Content-Disposition')
        // var fileName = "";
        let fileName = "Images.zip"; // Default filename
        if (disposition) {
        var filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/;
        var matches = filenameRegex.exec(disposition);
        
        if (matches != null && matches[1]) {
          fileName = matches[1].replace(/['"]/g, '');
        }
      }
        // sanitizing file name to 
        this.downloadZipFile(res.data, fileName);
        // TODO: check if this still works
        // this.downloadZipFile(res.data, "Images.zip");
        alert.success("Images Zipfile Downloaded");
      })
      .catch(error => {
        alert.error("Error in Creating Zipfile");
        if (error.message) {
          alert.error(error.message);
        }
      });
  }

  render() {
    // const { url } = this.props.match;
    // const { match } = this.props;
    console.log("PP", this.props)
    const { projectId } = this.props;
    const {
      isLoaded,
      error,
      projectName,
      sensors,
      completeSensors,
      intersections,
      completeIntersections,
      calibrationType,
      UploadWebApiSensorModal
    } = this.state;

    if (error) {
      return <div data-testid="corridorsError">Error: {error.message}</div>;
    } else if (!isLoaded) {
      return <Loader data-testid="corridorsLoader" active inline="centered" />;
    }

    return (
      <React.Fragment>
        {/* <Sidebar/> */}
        {/* <Menubar active={projectId}> */}
          <div>
            {/* <Grid celled columns="equal"> */}
              {/* <Grid.Row columns="equal"> */}
                {/* <Grid.Column> */}
                  {/* <Form>
                    <Form.Input
                      placeholder="Project name"
                      control="input"
                      value={projectName}
                      error={
                        validName
                          ? false
                          : "Invalid Project Name. Maximum Length: 200 Characters."
                      }
                      style={{ fontSize: 30, width: "100%", marginTop: 10 }}
                      onChange={this.handleNameChange}
                    />

                  </Form> */}
                {/* </Grid.Column> */}
                {/* <Grid.Column>
                <Button
                      size="large"
                      color="green"
                      onClick={this.toggleUploadWebApiModal}
                    >
                      Upload to Web/API
                </Button>
                </Grid.Column> */}
              {/* </Grid.Row>
            </Grid> */}
            <UploadWebApiModal
              modalShow={UploadWebApiSensorModal}
              projectId={projectId}
              projectName={projectName}
              reloadProjects={this.loadPage}
              downloadData={this.downloadData}
              onClose={this.toggleUploadWebApiModal}
            />
            <hr />

            {/* <CitiesGrid
              projectId={projectId}
              projectName={projectName}
              linkPrefix={`${url}/`}
              title="LOCATIONS:"
            /> */}

            <hr />
            <Header as="h1"> Actions: </Header>
            <Grid celled columns="equal">
              <Grid.Row columns="equal">
                <Grid.Column verticalAlign="middle" textAlign="center">
                  <Header as="h3"> SENSORS </Header>
                  <Header as="h5">
                    {`${sensors} sensors, ${completeSensors} completed`}
                  </Header>

                  <Button
                    size="large"
                    color="green"
                    onClick={this.getCalibrationJSON}
                  >
                    Export Sensor Calibrations
                  </Button>
                </Grid.Column>
                <Grid.Column verticalAlign="middle" textAlign="center">
                  <Header as="h3"> CAMERAS </Header>
                  <Header as="h5">
                    {`${sensors} sensors, ${completeSensors} completed`}
                  </Header>

                  <Button
                    size="large"
                    color="green"
                    onClick={this.getSensorsCSV}
                  >
                    Export Sensor Details
                  </Button>
                </Grid.Column>
                {calibrationType ==="geo" ? (<Grid.Column verticalAlign="middle" textAlign="center">
                  <Header as="h3"> INTERSECTIONS </Header>
                  <Header as="h5">
                    {`${intersections} intersections, ${completeIntersections} completed`}
                  </Header>
                  <Button
                    size="large"
                    color="green"
                    onClick={this.getNetworkJSON}
                  >
                    Export Intersection Road Networks
                  </Button>
                </Grid.Column>) :
                (<div/>)}
                {this.state.calibrationType === "cartesian" &&  <Grid.Column verticalAlign="middle" textAlign="center">
                  <Header as="h3"> Warped Images </Header>
                  <Header as="h5">
                    {`${sensors} sensors, ${completeSensors} completed`}
                  </Header>
                  <Button
                    size="large"
                    color="green"
                    onClick={this.getWarpedImages}
                  >
                    Download Warped
                  </Button>
                </Grid.Column>}
                {/* {this.state.calibrationType === "floorplan" &&  <Grid.Column verticalAlign="middle" textAlign="center">
                  <Header as="h3"> Floor Plan Images </Header>
                  <Header as="h5">
                    {`${sensors} sensors, ${completeSensors} completed`}
                  </Header>
                  <Button
                    size="large"
                    color="green"
                    onClick={this.getFloorPlanImages}
                  >
                    Download Floorplan Images
                  </Button>
                </Grid.Column>} */}
              </Grid.Row>
              <Grid.Row verticalAlign="middle" textAlign="center">
                {/* <Grid.Column></Grid.Column> */}
                <Grid.Column>
                <Header as="h3"> Get Images</Header>
                <Header as="h5"> </Header>

                  <Button
                    size="large"
                    color="green"
                    onClick={this.getImages}
                  >
                    Download Images
                  </Button>
                </Grid.Column>
                <Grid.Column>
                <Header as="h3"> Get Image Metadata</Header>
                <Header as="h5"> </Header>

                  <Button
                    size="large"
                    color="green"
                    onClick={this.getImageJson}
                  >
                    Download Image Metadata
                  </Button>
                  </Grid.Column> 
                  {/* <Grid.Column></Grid.Column>
              </Grid.Row>
              { <Grid.Row verticalAlign="middle" textAlign="center">
                 {/* {<Grid.Column></Grid.Column> } */}
                <Grid.Column>
                <Header as="h3"> Export to MDX WEB/API</Header>
                <Header as="h5"> </Header>

                  <Button
                    size="large"
                    color="green"
                    onClick={this.toggleUploadWebApiModal}
                  >
                    Upload to Web/API
                  </Button>
                  </Grid.Column>
                  {/* <Grid.Column></Grid.Column> */}
              </Grid.Row> 
            </Grid>
          </div>
          <hr />
          {/* <div id="delete-city" style={{ padding: "2em 0" }}>
            <Header>DELETE PROJECT</Header>
            <p>
              The button bellow will delete project and all data within the city
              (cities, intersections, and sensors).
            </p>
            <DeleteButton
              onConfirmDelete={this.handleDelete}
              label={"DELETE PROJECT"}
            />
          </div> */}
        {/* </Menubar> */}
      </React.Fragment>
    );
  }
}

export default withAlert()(ProjectPage);

ProjectPage.propTypes = {
  //   /** list of dicts to display*/
  //   menus: PropTypes.array.isRequired,
    /** ID of the project to add the project to */
    projectId: PropTypes.oneOfType([PropTypes.number, PropTypes.string]).isRequired,
  //   navigate: PropTypes.func.isRequired

  };

function countCompleted(objectSet, objectType) {
  let numComplete = 0;
  switch (objectType) {
    case "sensors":
      objectSet.forEach(sensor => {
        if (sensor.isCalibrated && sensor.isValidated) {
          numComplete = numComplete + 1;
        }
      });
      break;
    case "intersections":
      objectSet.forEach(intersection => {
        if (intersection.linksAreDrawn && intersection.linksAreValid) {
          numComplete = numComplete + 1;
        }
      });
      break;
    case "corridors":
      objectSet.forEach(corridor => {
        if (corridor.length > 0) {
          numComplete = numComplete + 1;
        }
      });
      break;
    default:
      return null;
  }
  return numComplete;
}
