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


import { matrix, multiply } from "mathjs";
import {
  convertLatLngToXYMatrix,
  convertProjectedPoint,
  flipPointY
} from "../common/mathUtils";


function getAttributes(sensor){
  let attributes = []
  let width = sensor.width.toString()
  let height = sensor.height.toString()
  const fps = {name: "fps", value: sensor.fps}
  const depth = {name: "depth", value: sensor.depth}
  const fieldOfView = {name: "fieldOfView", value: sensor.fieldOfView}
  const direction = {name: "direction", value: sensor.direction}
  const source = {name: "source", value: sensor.mmsInfo_type}
  const frameWidth = {name: "frameWidth", value: width}
  const frameHeight = {name: "frameHeight", value: height}

  attributes.push(
    fps,
    depth,
    fieldOfView,
    direction,
    source,
    frameWidth,
    frameHeight


  )
  return attributes
};

export function getGISPolygons(sensor,placeMap) {
  //get sensor coordinates
  const imageCoordinates = convertLatLngToXY(
    JSON.parse(sensor.sensorPolygon)[0].points
  );

  //get global coordinates
  const globalCoordinates = convertLatLngToXY(
    JSON.parse(sensor.gisPolygon)[0].points
  );

  const coordinates = {x: 0, y: 0};

  //get ROIs
  const rois = []
  let roisDict = JSON.parse(sensor.roiPolygon).map((roi,index) => {
    const roiId= `roi-id-${index+1}`
    rois.push({ id: roiId, roiCoordinates: convertLatLngToXY(roi.points) });
    return ( { id: roiId, roiCoordinates: convertLatLngToXY(roi.points) });
  });


  //parse places
  const places = []
  // places.push( { name: "city", value: "Dubuque"});
  sensor.place_set.forEach( place => {
      placeMap.forEach((x) =>{
        if (x.placeId === place){
          places.push( { name: x.placeTypeName, value: x.placeName });

        }
      });
    });
  const corridor_map = []
  //add corridors to places
  sensor.corridor_set.forEach( corridor => {
    placeMap.forEach((x) =>{
      if (x.placeId === corridor && x.placeTypeName === "corridor" ){
        corridor_map.push( { name: x.placeTypeName, value: x.placeName, sensor: sensor.sensorId });
      }
    });
  });
  //add intersections to places
  // console.log(sensor.intersection_set)
  placeMap.forEach((x) =>{
    if (x.placeId === sensor.intersection_set && x.placeTypeName === "intersection"){
      places.push( { name: x.placeTypeName, value: x.placeName  });
    }
  });

  //get tripwires
  const tripwires_ = JSON.parse(sensor.tripwireLines)
  const directions_ = JSON.parse(sensor.tripDirLines)
  const tripwires = getTripwires(tripwires_, directions_, 1)

  const place  = places

  const attributes = getAttributes(sensor)


  return { imageCoordinates, globalCoordinates, rois, place, tripwires, corridor_map, attributes, coordinates };
}


export function getFloorPlanPolygons(sensor,placeMap) {
  //get sensor coordinates
  const imageCoordinates = convertLatLngToXY(
    JSON.parse(sensor.sensorPolygon)[0].points
  );

  //get global coordinates
  const globalCoordinates = convertLatLngToXY(
    JSON.parse(sensor.gisPolygon)[0].points
  );


  //get ROIs
  const rois = []
  let roisDict = JSON.parse(sensor.roiPolygon).map((roi,index) => {
    const roiId= `roi-id-${index+1}`
    rois.push({ id: roiId, roiCoordinates: convertLatLngToXY(roi.points) });
    return ( { id: roiId, roiCoordinates: convertLatLngToXY(roi.points) });
  });

  //parse places
  const places = []
  sensor.place_set.forEach( place => {
      placeMap.forEach((x) =>{
        if (x.placeId === place){
          places.push( { name: x.placeTypeName, value: x.placeName });

        }
      });
    });
  // const corridor_map = []
  // //add corridors to places
  // sensor.corridor_set.forEach( corridor => {
  //   placeMap.forEach((x) =>{
  //     if (x.placeId === corridor && x.placeTypeName === "corridor" ){
  //       corridor_map.push( { name: x.placeTypeName, value: x.placeName, sensor: sensor.sensorId });
  //     }
  //   });
  // });
  //add intersections to places
  // console.log(sensor.intersection_set)
  placeMap.forEach((x) =>{
    if (x.placeId === sensor.intersection_set && x.placeTypeName === "intersection"){
      places.push( { name: x.placeTypeName, value: x.placeName  });
    }
  });

  //get tripwires
  const tripwires_ = JSON.parse(sensor.tripwireLines)
  const directions_ = JSON.parse(sensor.tripDirLines)
  const tripwires = getTripwires(tripwires_, directions_)

  const place  = places

  const attributes = getAttributes(sensor)

  const origin = {lat: 0, lng: 0};


  return { imageCoordinates, globalCoordinates, rois, place, tripwires, attributes, origin };
}




export function getMTMCPolygons(sensor,placeMap,projectName) {
  //get sensor coordinates
  const imageCoordinates = convertLatLngToXY(
    JSON.parse(sensor.sensorPolygon)[0].points
  );

  //get global coordinates
  const tempGlobalCoordinates = convertLatLngToXY(
    JSON.parse(sensor.gisPolygon)[0].points
  );
  const flippedCoordinates = flipPointCoordinatesY(
    tempGlobalCoordinates, sensor.floorPlanImHeight)
  const globalCoordinates = scaleCoordinates(flippedCoordinates,sensor.scaleFactor)

   //get global coordinates
   let coordinatesArr = scaleCoordinates(convertLatLngToXY(
    JSON.parse(sensor.coordinates)[0].points
  ), sensor.scaleFactor);
  const coordinates = coordinatesArr[0]

  //get ROIs
  const rois = []
  let roisDict = JSON.parse(sensor.roiPolygon).map((roi,index) => {
    const roiId= `roi-id-${index+1}`
    rois.push({ id: roiId, roiCoordinates: scaleCoordinates(flipPointCoordinatesY(convertLatLngToXY(roi.points),sensor.floorPlanImHeight),sensor.scaleFactor)});
    return ( { id: roiId, roiCoordinates: scaleCoordinates(flipPointCoordinatesY(convertLatLngToXY(roi.points),sensor.floorPlanImHeight),sensor.scaleFactor) });
  });

  //parse places
  const places = []
  sensor.place_set.forEach( place => {
      placeMap.forEach((x) =>{
        if (x.placeId === place){
          places.push( { name: x.placeTypeName, value: x.placeName });

        }
      });
    });

  // const corridor_map = []
  // //add corridors to places
  // sensor.corridor_set.forEach( corridor => {
  //   placeMap.forEach((x) =>{
  //     if (x.placeId === corridor && x.placeTypeName === "corridor" ){
  //       corridor_map.push( { name: x.placeTypeName, value: x.placeName, sensor: sensor.sensorId });
  //     }
  //   });
  // });
  //add intersections to places
  // console.log(sensor.intersection_set)
  placeMap.forEach((x) =>{
    if (x.placeId === sensor.intersection_set && x.placeTypeName === "intersection"){
      places.push( { name: x.placeTypeName, value: x.placeName  });
    }
  });

  places.push({name: "building", value: projectName})
  //get tripwires
  const tripwires_ = JSON.parse(sensor.tripwireLines)
  const directions_ = JSON.parse(sensor.tripDirLines)
  const tripwires = getMTMCTripwires(tripwires_, directions_,
    sensor.scaleFactor, sensor.floorPlanImHeight)

  const place  = places

  // let attributes = []
  const attributes = getAttributes(sensor)
  const origin = {lat: 0, lng: 0};


  return { imageCoordinates, globalCoordinates, rois, place, tripwires, attributes, coordinates, origin };
}


export function getImagePolygons(sensor,placeMap) {
  //get sensor coordinates
  const imageCoordinates = []

  //get global coordinates
  const globalCoordinates = []

   //get global coordinates
   const coordinates = {"x":0, "y":0}


  //get ROIs
  const rois = []
  let roisDict = JSON.parse(sensor.roiPolygon).map((roi,index) => {
    const roiId= `roi-id-${index+1}`
    rois.push({ id: roiId, roiCoordinates: convertLatLngToXY(roi.points) });
    return ( { id: roiId, roiCoordinates: convertLatLngToXY(roi.points) });
  });

  //parse places
  const places = []
  sensor.place_set.forEach( place => {
      placeMap.forEach((x) =>{
        if (x.placeId === place){
          places.push( { name: x.placeTypeName, value: x.placeName });

        }
      });
    });
  // const corridor_map = []
  // //add corridors to places
  // sensor.corridor_set.forEach( corridor => {
  //   placeMap.forEach((x) =>{
  //     if (x.placeId === corridor && x.placeTypeName === "corridor" ){
  //       corridor_map.push( { name: x.placeTypeName, value: x.placeName, sensor: sensor.sensorId });
  //     }
  //   });
  // });
  //add intersections to places
  // console.log(sensor.intersection_set)
  placeMap.forEach((x) =>{
    if (x.placeId === sensor.intersection_set && x.placeTypeName === "intersection"){
      places.push( { name: x.placeTypeName, value: x.placeName  });
    }
  });

  //get tripwires
  const tripwires_ = JSON.parse(sensor.tripwireLines)
  const directions_ = JSON.parse(sensor.tripDirLines)
  const tripwires = getTripwires(tripwires_, directions_, 1)

  const place  = places

  // let attributes = []
  const attributes = getAttributes(sensor)
  const origin = {lat: 0, lng: 0};


  return { imageCoordinates, globalCoordinates, rois, place, tripwires, attributes, coordinates, origin };
}


function padCoordinates(coordinates,xpad,ypad){
  let newCoordinates = []
  coordinates.forEach((point) =>{

    const xm = (point['x']+xpad)
    const ym = (point['y']+ypad)
    newCoordinates.push({
      x: xm,
      y:ym
    })

  })
  return newCoordinates
}

function scaleCoordinates(coordinates, scaleFactor){
  let newCoordinates = []
  coordinates.forEach((point) => {
    const xm = (point['x']) /scaleFactor
    const ym = (point['y'])/scaleFactor
    newCoordinates.push({
      x: xm,
      y:ym
    })
  })
  return newCoordinates
}

function flipPointCoordinatesY(coordinates, height){
  let newCoordinates = []
  coordinates.forEach((point) => {
    const xm = (point['x'])
    const ym = height - (point['y'])
    newCoordinates.push({
      x: xm,
      y: ym
    })
  })
  return newCoordinates
}

export function getCartesianPolygons(sensor, placeMap, projectName, cityName, roomName) {

  //create sensor coordinates
  const imageCoordinates = convertLatLngToXY(
    JSON.parse(sensor.sensorPolygon)[0].points
  );

  //create Global coordinates
  const tempCoordinates = convertLatLngToXY(
    JSON.parse(sensor.edgeLengths)
  );
  // console.log(tempCoordinates)
  const tempGlobalCoordinates = padCoordinates(tempCoordinates,sensor.invertImXPad, sensor.invertImYPad)
  const globalCoordinates = scaleCoordinates(tempGlobalCoordinates, 100)
  // console.log (globalCoordinates, sensor.edgeLengths)
  const coordinates = {"x":0, "y":0}
  // parse ROIs
  const rois = []

  const rois_ = JSON.parse(sensor.roiPolygon)
  const transformedROI = getHomographyPolygon(rois_,sensor.homography, sensor.invertImHeight)
  let roisDict = transformedROI.map((roi,index) => {
    const roiId= `roi-id-${index+1}`
    rois.push({ id: roiId, roiCoordinates: scaleCoordinates(convertLatLngToXY(roi.points), 100) });
    return ( { id: roiId, roiCoordinates: scaleCoordinates(convertLatLngToXY(roi.points), 100) });
  });

  //parse place
  const places = []
  sensor.place_set.forEach( place => {
      placeMap.forEach((x) =>{
        if (x.placeId === place){
          places.push( { name: x.placeTypeName, value: x.placeName });
        }
      });
    });


  //add intersections to places
  placeMap.forEach((x) =>{
    if (x.placeId === sensor.intersection_set && x.placeTypeName === "intersection"){
      places.push( { name: x.placeTypeName, value: x.placeName });
    }
  });
  places.push({name: "city", value: cityName}, {name: "building", value: projectName}, {name: "room", value: roomName})

  //get tripwires
  const tripwires_ = JSON.parse(sensor.tripwireLines)
  const directions_ = JSON.parse(sensor.tripDirLines)
  const transformedTripwires = getHomographyPolygon(tripwires_, sensor.homography, sensor.invertImHeight)
  const transformedDirections = getHomographyPolygon(directions_, sensor.homography, sensor.invertImHeight )
  const tripwires = getTripwires(transformedTripwires, transformedDirections, 100)
  const place = places
  const attributes = getAttributes(sensor)
  const origin = {lat: 0, lng: 0};

  return { imageCoordinates, globalCoordinates, rois, place, tripwires, attributes, coordinates, origin };
}





function getTripwires(tripwires, directions, scaleFactor){

  const tripwiresList = []
    //pairwise find intersections
  let count = 0
  tripwires.forEach((tripwire) => {
    let tripwireCoordinates =  convertLatLngToXY(tripwire.points)
    directions.forEach( (direction) => {
      let dirCoordinates =  convertLatLngToXY(direction.points)
      // const doLinesIntersect = true
      const doLinesIntersect = intersects(tripwireCoordinates,dirCoordinates)
      if (doLinesIntersect){
        tripwiresList.push({
          id: `tripwire-id-${count+=1}`,
          wire: {
            p1: {
              x: (tripwireCoordinates[0].x)/scaleFactor,
              y: (tripwireCoordinates[0].y)/scaleFactor
            },
            p2: {
              x: (tripwireCoordinates[1].x)/scaleFactor,
              y: (tripwireCoordinates[1].y)/scaleFactor
            }
          },
          direction: {
            p1: {
              x: (dirCoordinates[0].x)/scaleFactor,
              y: (dirCoordinates[0].y)/scaleFactor
            },
            p2: {
              x: (dirCoordinates[1].x)/scaleFactor,
              y: (dirCoordinates[1].y)/scaleFactor
            }
          },
        })
      }
    })
  })


  return tripwiresList
}

function getMTMCTripwires(tripwires, directions, scaleFactor, height){

  const tripwiresList = []
    //pairwise find intersections
  let count = 0
  tripwires.forEach((tripwire) => {
    let tripwireCoordinates =  flipPointCoordinatesY(convertLatLngToXY(tripwire.points), height)
    directions.forEach( (direction) => {
      let dirCoordinates =  flipPointCoordinatesY(convertLatLngToXY(direction.points), height)
      // const doLinesIntersect = true
      const doLinesIntersect = intersects(tripwireCoordinates,dirCoordinates)
      if (doLinesIntersect){
        tripwiresList.push({
          id: `tripwire-id-${count+=1}`,
          wire: {
            p1: {
              x: (tripwireCoordinates[0].x)/scaleFactor,
              y: (tripwireCoordinates[0].y)/scaleFactor
            },
            p2: {
              x: (tripwireCoordinates[1].x)/scaleFactor,
              y: (tripwireCoordinates[1].y)/scaleFactor
            }
          },
          direction: {
            p1: {
              x: (dirCoordinates[0].x)/scaleFactor,
              y: (dirCoordinates[0].y)/scaleFactor
            },
            p2: {
              x: (dirCoordinates[1].x)/scaleFactor,
              y: (dirCoordinates[1].y)/scaleFactor
            }
          },
        })
      }
    })
  })


  return tripwiresList
}

export function getIntersectionSegments(intersection) {
  const networkSegments = JSON.parse(intersection.roadLinks);

  let segments = networkSegments.map(segment => {
    const { id, direction } = segment;

    //TODO do i need convertlatlngtolatlon if i am not use lon in network.json anymore
    const start = convertLatLngToLatLon(segment.points[0]);
    const end = convertLatLngToLatLon(
      segment.points[segment.points.length - 1]
    );

    let points = segment.points.map(point => {
      point = convertLatLngToLatLon(point);
      point.alt = 0.0;
      return point;
    });
    return { id, direction, start, end, points };
  });
  return segments;
}

export function getIntersectionSegmentsForExport(intersection) {
  const networkSegments = JSON.parse(intersection.roadLinks);

  let segments = networkSegments.map(segment => {
    const { id, direction } = segment;

    //TODO do i need convertlatlngtolatlon if i am not use lon in network.json anymore
    const start = convertLatLngToLatLonForExport(segment.points[0]);
    const end = convertLatLngToLatLonForExport(
      segment.points[segment.points.length - 1]
    );

    let points = segment.points.map(point => {
      point = convertLatLngToLatLonForExport(point);
      point.alt = 0.0;
      return point;
    });
    return { id, direction, start, end, points };
  });
  return segments;
}

/**
 * Convert lat/lng object to x/y object for writing calibration.json
 * @param {Array} latLngArray array of lat/lng objects
 * @return {Array} array of x/y objects
 */
function convertLatLngToXY(latLngArray) {
  const newArray = latLngArray.map(latLngObject => {
    // Recall lat is y and lng is x
    return { x: latLngObject.lng, y: latLngObject.lat };
  });
  return newArray;
}

/**
 * Convert lat/lng object to x/y object for writing calibration.json
 * @param {object} latLngObject array of lat/lng objects
 * @return {object} array of x/y objects
 */
  function convertLatLngToLatLon(latLngObject) {
    return { lat: latLngObject.lat, lng: latLngObject.lng };
  }

  function convertLatLngToLatLonForExport(latLngObject) {
    return { lat: latLngObject.lat, lon: latLngObject.lng };
  }

/**
 * Convert lat/lng object to x/y object for writing calibration.json
 * @param {object} a x1 of 1st point in line
 * @param {object} b y1 of 1st point in line
 * @param {object} c x2 of 1st point in line
 * @param {object} d y2 of 1st point in line
 * @param {object} p x1 of 2nd point in line
 * @param {object} q y1 of 2nd point in line
 * @param {object} r x2 of 2nd point in line
 * @param {object} s y2 of 2nd point in line
 * @return {Boolean} boolean
 */
// returns true iff the line from (a,b)->(c,d) intersects with (p,q)->(r,s)
function intersects(line1,line2) {

  var det, gamma, lambda;
  // det = (c - a) * (s - q) - (r - p) * (d - b);
  det = (line1[1].x - line1[0].x) * (line2[1].y - line2[0].y) - (line2[1].x - line2[0].x) * (line1[1].y - line1[0].y);

  if (det === 0) {
    return false;
  } else {
    // lambda = ((s - q) * (r - a) + (p - r) * (s - b)) / det;
    // gamma =  ((b - d) * (r - a) + (c - a) * (s - b)) / det;

    lambda = ((line2[1].y - line2[0].y) * (line2[1].x - line1[0].x) + (line2[0].x - line2[1].x) * (line2[1].y - line1[0].y)) / det;
    gamma  = ((line1[0].y - line1[1].y) * (line2[1].x - line1[0].x) + (line1[1].x - line1[0].x) * (line2[1].y - line1[0].y)) / det;

    return (0 < lambda && lambda < 1) && (0 < gamma && gamma < 1);
  }
};



/**
   * converts a json file a csv
   * @param {object} JSONObject JSON data to save to file
   */
export function convertToCSV(objArray) {
    var array = typeof objArray != 'object' ? JSON.parse(objArray) : objArray;
    var str = '';

    for (var i = 0; i < array.length; i++) {
        var line = '';
        for (var index in array[i]) {
            if (line !== '') line += ','

            line += array[i][index];
        }

        str += line + '\r\n';
    }

    return str;
  };

/**
 * converts placetype_set into a map of placetypes and places
 *
 * @param {object} placeType_set placetype data
 */
export function getPlaceDict(placeTypes_set,intersection_set,corridor_set){
  let placeMap = [];
  Object.values(placeTypes_set).map((placeType,index) => {
    placeType['place_set'].forEach((place,_) =>{
      const row = {
        placeTypeId: placeType.id,
        placeTypeName: placeType.placeType,
        placeName: place.name,
        placeId: place.id
      }
      placeMap.push(row)
    });
  });
  Object.values(intersection_set).map((intersection,index) => {
    const row = {
      placeTypeId: intersection.id,
      placeTypeName: "intersection",
      placeName: intersection.name,
      placeId: intersection.id,
    }
    placeMap.push(row)
  });

  Object.values(corridor_set).map((corridor,index) => {
    const row = {
      placeTypeId: corridor.id,
      placeTypeName: "corridor",
      placeName: corridor.name,
      placeId: corridor.id,
    }
    placeMap.push(row)
  });
  return placeMap;
  };

export function checkCalibrationJSON(calibrationJSON){

  // Remove corridors if empty
  if ("corridors" in calibrationJSON && calibrationJSON.corridors.length === 0) {
    delete calibrationJSON.corridors;
  }
  calibrationJSON.sensors.forEach((sensor,index) =>{

    // if (sensor.tripwires.length == 0 || sensor.tripwires.length === 0 || typeof sensor.tripwires.length == undefined ){
    //   delete calibrationJSON.sensors[index].tripwires
    // }


  })
  if (!("sensorGroupings" in calibrationJSON)){
    return calibrationJSON
  }
  if (calibrationJSON.sensorGroupings.length === 0) {
    delete calibrationJSON.corridors
  }
  else {
    calibrationJSON.sensorGroupings.forEach((group,index) =>{
      if(group.type === "corridor"){
        // console.log("PP cpsadf" )
        if (group.groups.length == 0  ){
          // delete calibrationJSON.corridors[index]
            calibrationJSON.sensorGroupings.splice(index, 1);

        }else {
          group.groups.forEach((item)=>{
            // console.log("PP cpasdfg", item )
            //|| group.groups.sensors.length === 0 || typeof group.groups.sensors.length == undefined
              if ( item.sensors.length === 0 || typeof item.sensors.length == undefined ){
                // console.log("cjson corridor sensor length 0 or undefined")
                calibrationJSON.sensorGroupings.splice(index, 1);
              } else {
                // console.log (item.attributes)
                item.attributes.forEach((att) => {
                   if (att.name === "length" && att.value === "0"){
                    // console.log("cjson corridor length == 0")

                    calibrationJSON.sensorGroupings.splice(index, 1);
                   }
                  })
              }

          })
        }
      }
    });
  }
  // console.log(calibrationJSON)
  return calibrationJSON
};


function getHomographyPolygon(polygon, M, invImHeight){

  const polygonArray = []
  const homography_ = matrix(JSON.parse(M))

  // const newFigurespy = flipY(Object.values(polygon),this.state.invImHeight)

  Object.values(polygon).map((figure,index) => {
    const newShapePoints = figure.points.map( point =>{
      // console.log("hpt",this.state.height)
      // console.log("ght",point)
      const flippedPoint = flipPointY(point, invImHeight)
      // console.log("ght1",flippedPoint)
      const matrixPoint = convertLatLngToXYMatrix(flippedPoint, invImHeight);
      // console.log("xpt",matrixPoint)
      // console.log("h12",homography_)
      const projectedPoint = convertProjectedPoint(
        multiply(homography_, matrixPoint)
      );
      // console.log("ght2",projectedPoint)
      // const flipProjPoint = flipPointY(projectedPoint, invImHeight)
      // console.log("ght3",flipProjPoint)

      // const paddedPoint = padPointY(projectedPoint,0, invertImYPad,this.state.height)

      // console.log(projectedPoint)
      return projectedPoint;

    });
    // console.log ("polygon", figure.id)
    polygonArray.push({
      id: figure.id,
      points: newShapePoints,
      type: figure.type
    })
  });

  return polygonArray
}