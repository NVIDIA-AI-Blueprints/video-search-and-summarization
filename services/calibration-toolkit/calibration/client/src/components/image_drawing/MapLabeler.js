// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Copyright (c) 2009-2023. NVIDIA CORPORATION.  All rights reserved. */

import React from "react";
import PropTypes from "prop-types";
import Hotkeys from "react-hot-keys";
import update from "immutability-helper";
import "semantic-ui-css/semantic.min.css";

import Canvas from "./canvas/BuildingFloorMapCanvas";

import { genId, colors } from "../common/utils";
import { withLoadImageData } from "./HOC/LoadImageDataHOC";

class MapLabeler extends React.Component {
  constructor(props) {
    super(props);

    this.state = {
      selected: null,

      // UI
      reassigning: { status: false, type: null }
    };

    this.handleChange = this.handleChange.bind(this);
    this.handleSelectionChange = this.handleSelectionChange.bind(this);
    this.canvasRef = React.createRef();
  }

  handleSelectionChange(figureId) {
    if (figureId) {
      this.setState({ selectedFigureId: figureId });
    } else {
      this.setState({
        reassigning: { status: false, type: null },
        selectedFigureId: null
      });
    }
  }

  handleChange(eventType, figure, newLabelId) {
    if (!figure.color) return;
    const { mapLabels, mapShapes, pushMapState } = this.props;
    console.log("mapl", eventType, figure, newLabelId)

    const label =
      figure.color === "gray"
        ? { id: "__temp" }
        // : mapLabels[colors.indexOf(figure.color)];
        :  mapLabels.filter(item => item.id === figure.class)[0];
    const idx = (mapShapes[label.id] || []).findIndex(f => f.id === figure.id);
    console.log("IL", label, mapShapes, idx,  eventType, mapShapes[label.id], mapLabels, colors.indexOf(figure.color))
    switch (eventType) {
      case "new":
        console.log("il", label.id)
        pushMapState(
          state => ({
            mapShapes: update(state.mapShapes, {
              [label.id]: {
                $push: [
                  {
                    id: figure.id || genId(),
                    type: figure.type,
                    points: figure.points,
                    class: label.id,
                    color: figure.color
                  }
                ]
              }
            }),
            unfinishedMapFigure: null
          }),
          this.props.unselectDrawing()
        );
        break;

      case "replace":
        pushMapState(state => {
          return {
            mapShapes: update(state.mapShapes, {
              [label.id]: {
                $splice: [
                  [
                    idx,
                    1,
                    {
                      id: figure.id,
                      type: figure.type,
                      points: figure.points,
                      class: label.id,
                      color: figure.color
                    }
                  ]
                ]
              }
            })
          };
        });
        break;

      case "delete":
        pushMapState(state => ({
          mapShapes: update(state.mapShapes, {
            [label.id]: {
              $splice: [[idx, 1]]
            }
          })
        }));
        break;

      case "unfinished":
        pushMapState(
          state => ({ unfinishedMapFigure: figure }),
          () => {
            const { unfinishedMapFigure } = this.props;
            const { type, points } = unfinishedMapFigure;
            if (type === "point" && points.length >= 1) {
              this.handleChange("new", unfinishedMapFigure);
            }
          }
        );
        break;

      case "recolor":
        if (label.id === newLabelId) return;
        pushMapState(state => ({
          mapShapes: update(state.mapShapes, {
            [label.id]: {
              $splice: [[idx, 1]]
            },
            [newLabelId]: {
              $push: [
                {
                  id: figure.id,
                  points: figure.points,
                  type: figure.type,
                  class: newLabelId,
                  tracingOptions: figure.tracingOptions
                }
              ]
            }
          })
        }));
        break;

      default:
        throw new Error("unknown event type " + eventType);
    }
  }

  render() {
    const {
      height,
      width,
      imageUrl,
      mapLabels,
      popMapState,
      mapShapes,
      unfinishedMapFigure,
      hasHomography,
      toggles,
      showImageMarkers
    } = this.props;

    const center = [height / 2, width / 2];

    const allFigures = [];
    console.log ("image labelers" , mapLabels);
    console.log("image labelers mapShapes ", mapShapes)

    mapLabels.forEach((label, i) => {
      mapShapes[label.id].forEach(figure => {
        if (
          toggles[label.id] &&
          (label.type === "polyline" || label.type === "polygon" || label.type === "point")
        ) {

          allFigures.push({
            // color: colors[i],
            color: figure.color,
            points: figure.points,
            id: figure.id,
            class: label.id,
            type: figure.type
          });
        }
      });
    });
    mapShapes.__temp.forEach(figure => {
      allFigures.push({
        color: "gray",
        ...figure
      });
    });

    return (
      <Hotkeys keyName="ctrl+z" onKeyDown={popMapState}>
        <Canvas
          height={height}
          width={width}
          center={center}
          imageUrl={imageUrl}
          showImageMarkers={showImageMarkers}
          onChange={this.handleChange}
          onSelectionChange={this.handleSelectionChange}
          figures={allFigures}
          unfinishedFigure={unfinishedMapFigure}
          ref={this.canvasRef}
          style={{ flex: 1 }}
          hasHomography={hasHomography}
          labels={mapLabels}
        />
      </Hotkeys>
    );
  }
}

export default withLoadImageData(MapLabeler);

MapLabeler.propTypes = {
  /** URL of the image to use in the labeler*/
  imageUrl: PropTypes.string,
  /** Possible drawing IDs */
  toggles: PropTypes.object,
  /** Possible drawing labels */
  mapLabels: PropTypes.array,
  /**  */
  pushMapState: PropTypes.func,
  /** */
  popMapState: PropTypes.func,
  /** Current figures drawn in the image  */
  mapShapes: PropTypes.object,
  /** Unfinished figure drawing in the image*/
  unfinishedMapFigure: PropTypes.object,
  /** Indicator wether or not to show vertex number markers */
  showImageMarkers: PropTypes.bool,
  /** Handle unselecting a drawing mode (after finished drawing)*/
  unselectDrawing: PropTypes.func,
  /** Indicator wether or not there is a homography associated with the image */
  hasHomography: PropTypes.bool
};
