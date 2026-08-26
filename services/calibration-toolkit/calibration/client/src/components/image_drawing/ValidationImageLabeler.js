// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/** Copyright (c) 2009-2023. NVIDIA CORPORATION.  All rights reserved. */

import React from "react";
import PropTypes from "prop-types";
import Hotkeys from "react-hot-keys";
import update from "immutability-helper";
import "semantic-ui-css/semantic.min.css";

import Canvas from "./canvas/Canvas";

import { genId, colors } from "../common/utils";
import { withLoadImageData } from "./HOC/LoadImageDataHOC";

class ImageLabeler extends React.Component {
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
    const { labels, figures, pushState } = this.props;
    const label =
      figure.color === "gray"
        ? { id: "__temp" }
        : labels[colors.indexOf(figure.color)];
    const idx = (figures[label.id] || []).findIndex(f => f.id === figure.id);
    console.log("IL", label, figures, idx,  eventType, figures[label.id])
    switch (eventType) {
      case "new":
        console.log(label.id)
        pushState(
          state => ({
            figures: update(state.figures, {
              [label.id]: {
                $push: [
                  {
                    id: figure.id || genId(),
                    type: figure.type,
                    points: figure.points,
                    class: label.id
                  }
                ]
              }
            }),
            unfinishedFigure: null
          }),
          this.props.unselectDrawing()
        );
        break;

      case "replace":
        pushState(state => {
          return {
            figures: update(state.figures, {
              [label.id]: {
                $splice: [
                  [
                    idx,
                    1,
                    {
                      id: figure.id,
                      type: figure.type,
                      points: figure.points,
                      class: label.id
                    }
                  ]
                ]
              }
            })
          };
        });
        break;

      case "delete":
        pushState(state => ({
          figures: update(state.figures, {
            [label.id]: {
              $splice: [[idx, 1]]
            }
          })
        }));
        break;

      case "unfinished":
        pushState(
          state => ({ unfinishedFigure: figure }),
          () => {
            const { unfinishedFigure } = this.props;
            const { type, points } = unfinishedFigure;
            if (type === "bbox" && points.length >= 2) {
              this.handleChange("new", unfinishedFigure);
            }
          }
        );
        break;

      case "recolor":
        if (label.id === newLabelId) return;
        pushState(state => ({
          figures: update(state.figures, {
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
      labels,
      popState,
      figures,
      unfinishedFigure,
      hasHomography,
      toggles,
      showImageMarkers
    } = this.props;

    const center = [height / 2, width / 2];

    const allFigures = [];
    console.log ("image labelers" , labels);
    console.log("image labelers figures ", figures)

    labels.forEach((label, i) => {
      figures[label.id].forEach((figure,j) => {


        if (
          toggles[label.id] &&
          (label.type === "polyline" || label.type === "polygon")
        ) {
          const colorIndex = (j % 14)
          console.log(colorIndex)
          allFigures.push({
            color: colors[colorIndex],
            points: figure.points,
            id: figure.id,
            class: label.id,
            type: figure.type
          });
        }
      });
    });
    figures.__temp.forEach(figure => {
      allFigures.push({
        color: "gray",
        ...figure
      });
    });

    return (
      <Hotkeys keyName="ctrl+z" onKeyDown={popState}>
        <Canvas
          height={height}
          width={width}
          center={center}
          imageUrl={imageUrl}
          showImageMarkers={showImageMarkers}
          onChange={this.handleChange}
          onSelectionChange={this.handleSelectionChange}
          figures={allFigures}
          unfinishedFigure={unfinishedFigure}
          ref={this.canvasRef}
          style={{ flex: 1 }}
          hasHomography={hasHomography}
          labels={labels}
        />
      </Hotkeys>
    );
  }
}

export default withLoadImageData(ImageLabeler);

ImageLabeler.propTypes = {
  /** URL of the image to use in the labeler*/
  imageUrl: PropTypes.string,
  /** Possible drawing IDs */
  toggles: PropTypes.object,
  /** Possible drawing labels */
  labels: PropTypes.array,
  /**  */
  pushState: PropTypes.func,
  /** */
  popState: PropTypes.func,
  /** Current figures drawn in the image  */
  figures: PropTypes.object,
  /** Unfinished figure drawing in the image*/
  unfinishedFigure: PropTypes.object,
  /** Indicator wether or not to show vertex number markers */
  showImageMarkers: PropTypes.bool,
  /** Handle unselecting a drawing mode (after finished drawing)*/
  unselectDrawing: PropTypes.func,
  /** Indicator wether or not there is a homography associated with the image */
  hasHomography: PropTypes.bool
};
