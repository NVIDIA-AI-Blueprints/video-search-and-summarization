// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import PropTypes from "prop-types";
import React from "react";
import "semantic-ui-css/semantic.min.css";
import { colors } from "../common/utils";
import Canvas from "./canvas/Canvas_nolisten";
import { withLoadImageData } from "./HOC/LoadImageDataHOC";


class ImageDisplayer extends React.Component {
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
    switch (eventType) {
      // case "new":
      //   pushState(
      //     state => ({
      //       figures: update(state.figures, {
      //         [label.id]: {
      //           $push: [
      //             {
      //               id: figure.id || genId(),
      //               type: figure.type,
      //               points: figure.points
      //             }
      //           ]
      //         }
      //       }),
      //       unfinishedFigure: null
      //     }),
      //     this.props.unselectDrawing()
      //   );
      //   break;

      // case "replace":
      //   pushState(state => {
      //     return {
      //       figures: update(state.figures, {
      //         [label.id]: {
      //           $splice: [
      //             [
      //               idx,
      //               1,
      //               {
      //                 id: figure.id,
      //                 type: figure.type,
      //                 points: figure.points
      //               }
      //             ]
      //           ]
      //         }
      //       })
      //     };
      //   });
      //   break;

      // case "delete":
      //   pushState(state => ({
      //     figures: update(state.figures, {
      //       [label.id]: {
      //         $splice: [[idx, 1]]
      //       }
      //     })
      //   }));
      //   break;

      // case "unfinished":
      //   pushState(
      //     state => ({ unfinishedFigure: figure }),
      //     () => {
      //       const { unfinishedFigure } = this.props;
      //       const { type, points } = unfinishedFigure;
      //       if (type === "bbox" && points.length >= 2) {
      //         this.handleChange("new", unfinishedFigure);
      //       }
      //     }
      //   );
      //   break;

      // case "recolor":
      //   if (label.id === newLabelId) return;
      //   pushState(state => ({
      //     figures: update(state.figures, {
      //       [label.id]: {
      //         $splice: [[idx, 1]]
      //       },
      //       [newLabelId]: {
      //         $push: [
      //           {
      //             id: figure.id,
      //             points: figure.points,
      //             type: figure.type,
      //             tracingOptions: figure.tracingOptions
      //           }
      //         ]
      //       }
      //     })
      //   }));
      //   break;

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
      // popState,
      figures,
      unfinishedFigure,
      hasHomography,
      toggles,
      showImageMarkers
    } = this.props;

    // console.log("disp", height,width)

    const center = [height / 2, width / 2];
    // console.log ("ptc", center)
    // const center = [0,0]
    const allFigures = [];
    // console.log("image display labels", labels)
    console.log("image display figures", figures)
    labels.forEach((label, i) => {
      figures[label.id].forEach(figure => {
        if (
          toggles[label.id] &&
          (label.type === "polyline" || label.type === "polygon")
        ) {
          allFigures.push({
            color: colors[i],
            points: figure.points,
            id: figure.id,
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
      />
    );
  }
}

export default withLoadImageData(ImageDisplayer);

ImageDisplayer.propTypes = {
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
