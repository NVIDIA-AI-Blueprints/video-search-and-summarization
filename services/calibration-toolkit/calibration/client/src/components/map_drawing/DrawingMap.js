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
import { LoadScript } from "@react-google-maps/api";
import { genId } from "../common/utils";
import update from "immutability-helper";
import NewLatLngInput from "./NewLatLngInput";

export const libraries = ["drawing"];

export class LoadScriptOnlyIfNeeded extends LoadScript {
  componentDidMount() {
    const cleaningUp = true;
    const isBrowser = typeof document !== "undefined"; // require('@react-google-maps/api/src/utils/isbrowser')
    const isAlreadyLoaded =
      window.google &&
      window.google.maps &&
      document.querySelector("body.first-hit-completed"); // AJAX page loading system is adding this class the first time the app is loaded
    if (!isAlreadyLoaded && isBrowser) {
      // @ts-ignore
      if (window.google && !cleaningUp) {
        console.error("google api is already presented");
        return;
      }

      this.isCleaningUp().then(this.injectScript);
    }

    if (isAlreadyLoaded) {
      this.setState({ loaded: true });
    }
  }
}

/**
 * Parent Drawing Map component for enabling the user to draw shapes (polygon or
 * polyline) on the google map.
 */
export default class DrawingMap extends Component {
  _interval = null;
  constructor(props) {
    super(props);
    this.state = {
      drawingMode: null,
      pointSelect: null,
      checkedA: true,
      center: { lat: 0, lng: 0 },
      zoomLevel: 0,
      coordChangeModal: false,
      selectedSensor: null,
      mapTypeId: "hybrid"
    };

    this.handleShapeComplete = this.handleShapeComplete.bind(this);
    this.noDraw = this.noDraw.bind(this);
    this.handleDeletePoint = this.handleDeletePoint.bind(this);
    this.handleShapeEdit = this.handleShapeEdit.bind(this);
    this.updateVertexLocation = this.updateVertexLocation.bind(this);
    this._handleViewChanged = this._handleViewChanged.bind(this);
    this.toggleModal = this.toggleModal.bind(this);
    this.renderCoordModal = this.renderCoordModal.bind(this);
    this.saveMapTypeId = this.saveMapTypeId.bind(this);
  }

  /**
   * Check for zoom level or map center change on component update. Saves the
   * current zoom level and center to the backend.
   * @param {object} prevProps Previous props before update.
   * @param {object} prevState Previous state before update.
   */
  componentDidUpdate(prevProps, prevState) {
    const { zoomLevel, center } = this.state;
    if (prevState.zoomLevel !== zoomLevel || prevState.center !== center) {
      this.props.updateMapView(zoomLevel, center);
    }
  }

  /**
   * Turns the drawing mode off.
   */
  noDraw = () => {
    this.setState({ drawingMode: null });
  };

  /**
   * Handle actions when shape drawing using google maps API drawing manager is
   * completed. Takes the google maps shape and saves it into an object of type
   * {id, category, points}, then deletes the google maps shape object created
   * by the google maps drawing manager.
   * @param {object} shape Shape object generate by drawing manager within
   * google maps API
   * @param {string} selected ID of type of shape (polygon or polyline) drawn.
   */
  handleShapeComplete(shape, selected) {
    if (selected === null) {
      shape.setMap(null);
      return;
    }
    console.log("shape complete", shape, selected)
    const shapeArray = shape.getPath().getArray();
    const { shapes } = this.props;
    const id = genId();
    let newShape = { id: id, category: selected, points: [] };
    shapeArray.forEach(function(point) {
      newShape.points.push({ lat: point.lat(), lng: point.lng() });
    });
    const newShapes = update(shapes, { [selected]: { $push: [newShape] } });
    this.noDraw();
    shape.setMap(null);
    this.props.handleDrawingChange(newShapes);
    this.props.unselectDrawing();
  }

  /**
   * Handle actions when a point on the shape is clicked. Either updates a the
   * position of an existing vertex or adds a new vertex at the midpoint of an
   * edge.
   * @param {object} point Click event showing information about dragged point
   * @param {object} shape Shape that the point belongs to
   */
  handleShapeEdit(point, shape) {
    if (point.vertex >= 0) {
      this.updateVertexLocation(point, shape);
    } else if (point.edge >= 0) {
      this.addPointFromEdgeDrag(point, shape);
    }
  }

  /**
   * Handle actions when a point is deleted (right click event).
   * @param {object} point Right click event showing information about clicked
   * point
   * @param {object} shape Shape that the point belongs to
   */
  handleDeletePoint(point, shape) {
    if (point.vertex >= 0) {
      const { shapes } = this.props;
      const { id, category } = shape;
      let newShapes = [];
      shapes[category].forEach(shape => {
        if (shape.id === id) {
          if (shape.points.length > 1) {
            let newShape = shape.points.filter((_, i) => i !== point.vertex);
            newShapes.push({
              id: id,
              category: shape.category,
              points: newShape
            });
          }
        } else {
          newShapes.push(shape);
        }
      });
      const finalshapes = update(shapes, { [category]: { $set: newShapes } });
      this.props.handleDrawingChange(finalshapes);
    }
  }

  /**
   * Update the location of a vertex when the vertex is dragged.
   * @param {object} point Click event showing information about dragged point
   * @param {object} shape Shape that the point belongs to
   */
  updateVertexLocation(point, shape) {
    const { shapes } = this.props;
    const { id, category } = shape;
    let newShapes = [];
    const newPoint = { lat: point.latLng.lat(), lng: point.latLng.lng() };
    const index = point.vertex;
    shapes[category].forEach(shape => {
      if (shape.id === id) {
        let newShape = shape;
        newShape.points[index] = newPoint;
        newShapes.push(newShape);
      } else {
        newShapes.push(shape);
      }
    });
    const finalShapes = update(shapes, { [category]: { $set: newShapes } });
    this.props.handleDrawingChange(finalShapes);
  }

  /**
   * Add point when the midpoint of an edge is clicked.
   * @param {object} point Click event showing information about clicked point
   * @param {object} shape Shape that the point belongs to
   */
  addPointFromEdgeDrag(point, shape) {
    const { shapes } = this.props;
    const { id, category } = shape;
    const newMidPoint = { lat: point.latLng.lat(), lng: point.latLng.lng() };
    const index = point.edge + 1;
    const prevIndex = index - 1;
    let newShapes = [];
    shapes[category].forEach(shape => {
      if (shape.id === id) {
        const prevPoint = shape.points[prevIndex];
        let newPoint = {
          lat: newMidPoint.lat + (newMidPoint.lat - prevPoint.lat),
          lng: newMidPoint.lng + (newMidPoint.lng - prevPoint.lng)
        };
        newPoint = this.checkNewPoint(shape, newPoint, newMidPoint);
        const leftArray = shape.points.slice(0, index);
        const rightArray = shape.points.slice(index, shape.points.length);
        let newShape = {
          id: id,
          category: shape.category,
          points: leftArray.concat([newPoint], rightArray)
        };
        newShapes.push(newShape);
      } else {
        newShapes.push(shape);
      }
    });
    const finalShapes = update(shapes, { [category]: { $set: newShapes } });
    this.props.handleDrawingChange(finalShapes);
  }

  /**
   * Check the location of a new point to see if it is too close to an existing
   * point. If it is too close, the point is updated accordingly to the correct
   * midpoint. This function is necessary because different coordinates are
   * returned depending on if the midpoint is dragged versus clicked.
   * @param {object} shape Shape that the new point is being added to
   * @param {object} newPoint Location of new point
   * @param {object} newMidPoint Location of midpoint return from click event
   */
  checkNewPoint(shape, newPoint, newMidPoint) {
    shape.points.forEach(point => {
      if (Math.abs(point.lat - newPoint.lat) < 0.000001) {
        newPoint.lat = newMidPoint.lat;
        newPoint.lng = newMidPoint.lng;
      }
    });
    return newPoint;
  }

  /**
   * Handle actions when the view is changed. Set the state of the map component
   * to use the updated zoom level and center.
   */
  _handleViewChanged() {
    if (this.refs.map) {
      const { map } = this.refs.map.state;
      const newLat = map.center.lat();
      const newLng = map.center.lng();
      const center = { lat: newLat, lng: newLng };
      const zoomLevel = map.zoom;
      if (zoomLevel !== this.state.zoomLevel || center !== this.state.center) {
        this.setState({ zoomLevel, center });
      }
    }
  }

  /**
   * Save the map type ID (e.g. satellite, hybrid, etc.) so that the map type
   * is preserved when the component updates.
   */
  saveMapTypeId() {
    if (this.refs.map) {
      const { mapTypeId } = this.refs.map.state.map;
      if (mapTypeId !== this.state.mapTypeId) {
        this.setState({ mapTypeId });
      }
    }
  }

  /**
   * Toggle the modal showing the change map center coordinate interface.
   */
  toggleModal() {
    const { coordChangeModal } = this.state;
    this.setState({ coordChangeModal: !coordChangeModal });
  }

  /**
   * Render the coordinate modal based on the state of the current map.
   */
  renderCoordModal() {
    const { coordChangeModal, zoomLevel } = this.state;
    const { updateMapView, reloadPage, id, type } = this.props;
    let center;
    if (this.state.center.lat === 0 && this.state.center.lng === 0) {
      center = this.props.center;
    } else {
      center = this.state.center;
    }

    return (
      <NewLatLngInput
        modalShow={coordChangeModal}
        id={id}
        type={type}
        mapZoom={zoomLevel}
        center={center}
        onSubmit={updateMapView}
        reloadPage={reloadPage}
        onClose={this.toggleModal}
      />
    );
  }

  // abstract
  render() {
    return null;
  }
}

DrawingMap.propTypes = {
  id: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  type: PropTypes.string,
  apiKey: PropTypes.string,
  toggles: PropTypes.object,
  center: PropTypes.object,
  mapZoom: PropTypes.number,
  shapes: PropTypes.object,
  showMapMarkers: PropTypes.bool,
  updateMapView: PropTypes.func,
  drawingMode: PropTypes.string,
  handleDrawingChange: PropTypes.func
};
